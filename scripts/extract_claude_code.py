#!/usr/bin/env python3
"""
Extract Claude Code sessions organized by project.

Reads from:
  ~/.claude/projects/{encoded-path}/*.jsonl  — per-project sessions
  ~/.claude/transcripts/ses_*.jsonl           — global transcripts

Outputs:
  {output_dir}/{project_name}/claude/{session_id}.jsonl

Usage:
  python3 extract_claude_code.py --dry-run
  python3 extract_claude_code.py --output-dir /tmp/test_claude/
  python3 extract_claude_code.py --project infa --output-dir ./sessions/
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Segments to skip when extracting project name from a path.
# Usernames are added dynamically at import time.
_GENERIC_SEGMENTS = {
    "project",
    "projects",
    "src",
    "home",
    "users",
    "work",
    "code",
    "workspace",
    "workspaces",
    "repos",
    "repositories",
    "config",
    "conductor",
}

# Dynamically add the current user's home directory segments to skip
_home = str(Path.home())
_home_parts = _home.strip("/").split("/")
for _part in _home_parts:
    if _part:
        _GENERIC_SEGMENTS.add(_part.lower())
if os.environ.get("USER"):
    _GENERIC_SEGMENTS.add(os.environ["USER"].lower())

GENERIC_SEGMENTS = frozenset(_GENERIC_SEGMENTS)


def _detect_project_prefix() -> str:
    """Auto-detect the common project path prefix from the user's filesystem.

    Checks common project root directories, falls back to $HOME.
    Override with CLAUDE_PROJECT_PATH_PREFIX env var.
    """
    candidates = [
        os.path.join(str(Path.home()), "project"),
        os.path.join(str(Path.home()), "repos"),
        os.path.join(str(Path.home()), "Projects"),
        os.path.join(str(Path.home()), "workspace"),
        str(Path.home()),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate + "/"
    return str(Path.home()) + "/"


# Auto-detected project path prefix (overridable via env var)
_PROJECT_PATH_PREFIX = os.environ.get(
    "CLAUDE_PROJECT_PATH_PREFIX",
    _detect_project_prefix(),
)


def _last_nongeneric_segment(path_str: str) -> str:
    """Return the last path segment that isn't generic, lowercase."""
    parts = [p for p in path_str.strip("/").split("/") if p]
    nongeneric = [
        (i, seg.lower())
        for i, seg in enumerate(parts)
        if seg.lower() not in GENERIC_SEGMENTS
    ]
    if nongeneric:
        return nongeneric[-1][1]
    return parts[-1].lower() if parts else "unknown"


def decode_project_name(encoded_dir_name: str) -> str:
    """Decode Claude's encoded directory name to a human-readable project name.

    Examples:
        '-Users-samanvayayagsen-project-rethink-paradigms-infa' -> 'infa'
        '-Users-samanvayayagsen--config-opencode' -> 'opencode'
        '-Users-samanvayayagsen-project-rethink-paradigms-infa--claude-worktrees-modest-beaver-5df149' -> 'infa-worktree-5df149'
        '-' -> 'root'
    """
    name = encoded_dir_name.strip()

    if name == "-":
        return "root"

    parts = [p for p in name.split("-") if p]

    if not parts:
        return "root"

    if "worktrees" in parts:
        wt_idx = parts.index("worktrees")
        proj = parts[wt_idx - 1] if wt_idx > 0 else parts[-1]
        wt_name = parts[wt_idx + 1] if wt_idx + 1 < len(parts) else "unknown"
        short_hash = parts[-1] if len(parts) > wt_idx + 2 else ""
        if short_hash:
            return f"{proj}-worktree-{short_hash}"
        return f"{proj}-worktree-{wt_name}"

    return parts[-1]


def find_claude_base_dir() -> Path:
    """Find the ~/.claude directory."""
    claude_dir = Path.home() / ".claude"
    if claude_dir.exists():
        return claude_dir
    raise FileNotFoundError(f"Claude directory not found: {claude_dir}")


def collect_project_sessions(
    claude_dir: Path, since_ts: float | None = None
) -> dict[str, list[tuple[Path, str | None]]]:
    """Collect all JSONL sessions from project directories.

    Returns:
        dict mapping project_name -> list of (file_path, parent_uuid) tuples.
        parent_uuid is the parent session UUID for agent files in subdirectories,
        or None for flat files and regular sessions.
    """
    projects_dir = claude_dir / "projects"
    if not projects_dir.exists():
        return {}

    result: dict[str, list[tuple[Path, str | None]]] = {}
    for proj_dir in sorted(projects_dir.iterdir()):
        if not proj_dir.is_dir():
            continue

        project_name = decode_project_name(proj_dir.name)

        for jsonl_file in sorted(proj_dir.glob("**/*.jsonl")):
            if jsonl_file.name == "sessions-index.json":
                continue
            if since_ts is not None and jsonl_file.stat().st_mtime <= since_ts:
                continue

            parent_uuid: str | None = None
            try:
                relative = jsonl_file.relative_to(proj_dir)
                parts = relative.parts
                if len(parts) >= 3 and parts[1] == "subagents":
                    parent_uuid = parts[0]
            except ValueError:
                pass

            result.setdefault(project_name, []).append((jsonl_file, parent_uuid))

    return result


def collect_transcripts(claude_dir: Path, since_ts: float | None = None) -> list[Path]:
    """Collect all transcript files from ~/.claude/transcripts/."""
    transcripts_dir = claude_dir / "transcripts"
    if not transcripts_dir.exists():
        return []
    files = sorted(transcripts_dir.glob("ses_*.jsonl"))
    if since_ts is not None:
        files = [f for f in files if f.stat().st_mtime > since_ts]
    return files


def _extract_all_paths_from_transcript(transcript_path: Path, max_lines: int = 100) -> list[str]:
    """Extract all project-relative directory paths found in a transcript.

    Returns list of paths like 'rethink-paradigms/ai-re' (relative to
    /Users/samanvayayagsen/project/), sorted shortest-first.
    """
    paths = []
    try:
        with open(transcript_path) as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for field in ("tool_input", "tool_output", "content"):
                    val = obj.get(field)
                    if not val:
                        continue
                    if isinstance(val, dict):
                        val = json.dumps(val)
                    elif not isinstance(val, str):
                        continue
                    for m in re.finditer(
                        re.escape(_PROJECT_PATH_PREFIX) + r"([\w./\-]+)", val
                    ):
                        raw = m.group(1)
                        segments = raw.split("/")
                        dir_segs = []
                        for seg in segments:
                            if "." in seg and not seg.startswith("."):
                                break
                            dir_segs.append(seg)
                        if dir_segs:
                            p = "/".join(dir_segs)
                            if p not in paths:
                                paths.append(p)
    except OSError:
        pass
    return paths


def extract_cwd_from_transcript(transcript_path: Path) -> str | None:
    """Try to extract working directory from first user message in transcript."""
    try:
        with open(transcript_path) as f:
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                if obj.get("type") == "user":
                    content = obj.get("content", "")
                    match = re.search(r"Working Directory\s+(.+)", content)
                    if match:
                        return match.group(1).strip()
                    break
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _match_path_to_known_project(
    rel_path: str, known_project_paths: dict[str, str]
) -> str | None:
    """Match a relative project path to a known project name.

    known_project_paths maps decoded paths like
    '/Users/samanvayayagsen/project/rethink-paradigms/infa' to project names like 'infa'.
    Checks if the known path is a prefix of the found path.
    """
    full_path = (_PROJECT_PATH_PREFIX + rel_path).rstrip("/")
    if full_path in known_project_paths:
        return known_project_paths[full_path]
    # Try prefix match — longest (most specific) known path first
    for known_path, proj_name in sorted(known_project_paths.items(), key=lambda x: -len(x[0])):
        if full_path.startswith(known_path + "/"):
            return proj_name
    return None


def assign_transcripts_to_projects(
    transcripts: list[Path],
    project_sessions: dict[str, list[tuple[Path, str | None]]],
) -> dict[str, list[Path]]:
    """Assign transcript files to projects based on CWD matching or path extraction.

    Returns dict of project_name -> list of transcript paths.
    Unmatched transcripts go under '_transcripts_'.
    """
    import os

    existing_stems = set()
    for paths in project_sessions.values():
        for p, _parent in paths:
            existing_stems.add(p.stem)

    claude_dir = find_claude_base_dir()
    projects_dir = claude_dir / "projects"
    known_project_paths: dict[str, str] = {}
    if projects_dir.exists():
        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            proj_name = decode_project_name(proj_dir.name)
            resolved = _resolve_claude_project_dir(proj_dir.name)
            if resolved and resolved.startswith(_PROJECT_PATH_PREFIX):
                known_project_paths[resolved.rstrip("/")] = proj_name

    # Also scan ~/project/ for direct subdirectories (and one level of nesting)
    # to catch projects like 'ai-re' that don't have Claude project dirs
    project_base = Path(_PROJECT_PATH_PREFIX.rstrip("/"))
    if project_base.exists():
        for subdir in project_base.iterdir():
            if not subdir.is_dir() or subdir.name.startswith("."):
                continue
            path = str(subdir)
            if path not in known_project_paths:
                name = subdir.name.lower()
                if name not in GENERIC_SEGMENTS:
                    known_project_paths[path] = name
            # One level of nesting (e.g. rethink-paradigms/infa)
            for sub2 in subdir.iterdir():
                if not sub2.is_dir() or sub2.name.startswith("."):
                    continue
                path2 = str(sub2)
                if path2 not in known_project_paths:
                    name2 = sub2.name.lower()
                    if name2 not in GENERIC_SEGMENTS:
                        known_project_paths[path2] = name2

    result: dict[str, list[Path]] = {}

    for transcript in transcripts:
        stem = transcript.stem

        if stem in existing_stems:
            continue

        # Strategy 1: Working Directory pattern (original logic)
        cwd = extract_cwd_from_transcript(transcript)
        if cwd:
            proj_name = os.path.basename(cwd.rstrip("/"))
            matched = False
            for known_proj in project_sessions:
                if known_proj == proj_name or proj_name in known_proj:
                    result.setdefault(known_proj, []).append(transcript)
                    matched = True
                    break
            if not matched:
                result.setdefault(proj_name, []).append(transcript)
            continue

        # Strategy 2: Extract project paths from transcript content
        paths = _extract_all_paths_from_transcript(transcript)
        if paths:
            paths.sort(key=len)
            matched = False
            for rel_path in paths:
                proj_name = _match_path_to_known_project(rel_path, known_project_paths)
                if proj_name:
                    result.setdefault(proj_name, []).append(transcript)
                    matched = True
                    break
            if not matched:
                # Walk from shortest path upward — longest matching known
                # project wins. For paths with no known project, use
                # _last_nongeneric_segment on the path whose last segment
                # is closest to the project root.
                proj_name = None
                for rel_path in reversed(paths):
                    full = (_PROJECT_PATH_PREFIX + rel_path).rstrip("/")
                    candidate = _last_nongeneric_segment(full)
                    # Prefer the shallowest non-generic segment
                    if proj_name is None:
                        proj_name = candidate
                result.setdefault(proj_name or "unknown", []).append(transcript)
            continue

        result.setdefault("_transcripts_", []).append(transcript)

    return result


def _claude_raw_to_path(raw: str) -> str:
    """Convert Claude's dash-encoded directory name back to a filesystem path."""
    if raw == "-":
        return "/"
    return "/" + raw.lstrip("-").replace("-", "/")


def _resolve_claude_project_dir(encoded: str) -> str | None:
    """Resolve a Claude dash-encoded project dir to an actual filesystem path.

    Claude replaces '/' with '-' which is ambiguous for hyphenated dir names
    like 'rethink-paradigms'. We try all possible merge combinations and verify
    each against the filesystem, returning the shortest (most merged) valid path.
    """
    if encoded == "-":
        return "/"

    parts = encoded.lstrip("-").split("-")
    if not parts:
        return "/"

    best = None

    def try_merges(remaining: list[str], current: list[str]):
        nonlocal best
        if not remaining:
            path = "/" + "/".join(current)
            if Path(path).exists():
                if best is None or len(current) < len(best.split("/")):
                    best = path
            return
        try_merges(remaining[1:], current + [remaining[0]])
        if len(remaining) >= 2:
            try_merges(
                remaining[2:],
                current + [remaining[0] + "-" + remaining[1]],
            )

    try_merges(parts, [])
    return best


def session_output_path(output_dir: Path, project_name: str, session_id: str) -> Path:
    return output_dir / project_name / "claude" / f"{session_id}.jsonl"


def _extract_parent_from_agent_file(agent_file: Path) -> str | None:
    try:
        with open(agent_file) as f:
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                return obj.get("sessionId")
    except (json.JSONDecodeError, OSError):
        pass
    return None


def copy_session(
    src: Path, dest: Path, dry_run: bool = False, parent_session_id: str | None = None
) -> bool:
    if dry_run:
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)

    with open(src) as fin, open(dest, "w") as fout:
        for line in fin:
            if not line.strip():
                continue
            try:
                json.loads(line)  # validate
                fout.write(line)
            except json.JSONDecodeError:
                continue

    meta_dest = dest.with_suffix(".meta.json")
    with open(meta_dest, "w") as fmeta:
        json.dump({"parent_session_id": parent_session_id}, fmeta)

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Extract Claude Code sessions organized by project"
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default="./sessions/",
        help="Output directory (default: ./sessions/)",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Print planned actions without writing files",
    )
    parser.add_argument("--project", "-p", help="Filter to a specific project name")
    parser.add_argument(
        "--since", default=None,
        help="Only extract sessions modified since this ISO 8601 date"
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    since_ts = None
    if args.since:
        since_ts = datetime.fromisoformat(args.since).timestamp()

    # Find Claude base directory
    try:
        claude_dir = find_claude_base_dir()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("CLAUDE CODE SESSION EXTRACTOR")
    print("=" * 60)
    print(f"Source: {claude_dir}")
    print(f"Output: {output_dir}")
    print(f"Dry run: {args.dry_run}")
    if args.since:
        print(f"Since: {args.since}")
    print()

    # Step 1: Collect project sessions
    project_sessions = collect_project_sessions(claude_dir, since_ts=since_ts)
    print(f"Projects found: {len(project_sessions)}")
    for proj, files in sorted(project_sessions.items()):
        main_sessions = [f for f, _parent in files if not f.name.startswith("agent-")]
        agent_sessions = [f for f, _parent in files if f.name.startswith("agent-")]
        print(f"  {proj}: {len(main_sessions)} sessions, {len(agent_sessions)} agents")

    # Step 2: Collect and assign transcripts
    transcripts = collect_transcripts(claude_dir, since_ts=since_ts)
    print(f"\nTranscripts found: {len(transcripts)}")

    transcript_projects = assign_transcripts_to_projects(transcripts, project_sessions)
    for proj, files in sorted(transcript_projects.items()):
        print(f"  {proj}: {len(files)} unmatched transcripts")

    print()

    # Step 3: Apply project filter
    if args.project:
        filtered_projects = {
            k: v
            for k, v in project_sessions.items()
            if args.project in k or k == args.project
        }
        filtered_transcripts = {
            k: v
            for k, v in transcript_projects.items()
            if args.project in k or k == args.project
        }
        if not filtered_projects and not filtered_transcripts:
            print(f"No sessions found for project '{args.project}'")
            print(
                f"Available projects: {', '.join(sorted(set(project_sessions) | set(transcript_projects)))}"
            )
            sys.exit(1)
        project_sessions = filtered_projects
        transcript_projects = filtered_transcripts
        print(f"Filtered to project '{args.project}'")

    # Step 4: Write sessions
    total_written = 0
    total_skipped = 0

    # Write project sessions
    for proj_name, files in sorted(project_sessions.items()):
        for jsonl_file, parent_uuid in files:
            session_id = jsonl_file.stem
            dest = session_output_path(output_dir, proj_name, session_id)

            if parent_uuid:
                parent_session_id = parent_uuid
            elif jsonl_file.name.startswith("agent-"):
                parent_session_id = _extract_parent_from_agent_file(jsonl_file)
            else:
                parent_session_id = None

            if args.dry_run:
                parent_info = f" (parent: {parent_session_id})" if parent_session_id else ""
                print(f"  [DRY] {jsonl_file.parent.name}/{jsonl_file.name} -> {dest}{parent_info}")
                total_written += 1
            else:
                try:
                    copy_session(jsonl_file, dest, parent_session_id=parent_session_id)
                    total_written += 1
                except Exception as e:
                    print(f"  [ERR] {jsonl_file}: {e}", file=sys.stderr)
                    total_skipped += 1

    # Write transcript sessions
    for proj_name, files in sorted(transcript_projects.items()):
        for jsonl_file in files:
            session_id = jsonl_file.stem
            dest = session_output_path(output_dir, proj_name, session_id)

            if args.dry_run:
                print(f"  [DRY] transcript/{jsonl_file.name} -> {dest}")
                total_written += 1
            else:
                try:
                    copy_session(jsonl_file, dest)
                    total_written += 1
                except Exception as e:
                    print(f"  [ERR] {jsonl_file}: {e}", file=sys.stderr)
                    total_skipped += 1

    # Summary
    print()
    print("=" * 60)
    if args.dry_run:
        print(f"DRY RUN: {total_written} sessions would be written")
    else:
        print(f"Written: {total_written} sessions")
        if total_skipped:
            print(f"Skipped: {total_skipped} (errors)")

        # Show output structure
        print(f"\nOutput structure:")
        for proj_dir in sorted(output_dir.iterdir()):
            if proj_dir.is_dir():
                claude_dir_path = proj_dir / "claude"
                if claude_dir_path.exists():
                    count = len(list(claude_dir_path.glob("*.jsonl")))
                    print(f"  {proj_dir.name}/claude/ ({count} files)")

    print("=" * 60)


if __name__ == "__main__":
    main()
