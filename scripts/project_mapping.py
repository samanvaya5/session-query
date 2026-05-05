"""
Project name canonicalization across AI coding agents.

Maps raw project identifiers from OpenCode, Claude, and GeminiCLI
to a single canonical project name, so sessions from different agents
can be grouped by project.

Usage:
    from tools.project_mapping import canonicalize, build_mapping_log

    name = canonicalize("opencode", "/Users/samanvayayagsen/project/rethink-paradigms/infa")
    # → "infa"
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

GENERIC_SEGMENTS = frozenset(
    {
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
)

CLAUDE_WORKTREE_SUFFIXES = (
    "-claude-worktrees-",
    "--claude-worktrees-",
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


def _extract_conductor_workspace(path_str: str) -> str | None:
    """Extract 'project-workspace' name from conductor workspace paths.

    /Users/.../conductor/workspaces/infa/provo → 'infa-provo'
    Returns None if not a conductor workspace path.
    """
    parts = path_str.strip("/").split("/")
    for i in range(len(parts) - 2):
        if parts[i] == "conductor" and parts[i + 1] == "workspaces":
            project = parts[i + 2] if i + 2 < len(parts) else None
            workspace = parts[i + 3] if i + 3 < len(parts) else None
            if project and workspace:
                return f"{project}-{workspace}"
            elif project:
                return project
    return None


def _claude_raw_to_path(raw: str) -> str:
    """Convert Claude's dash-encoded directory name back to a filesystem path.

    '-Users-samanvayayagsen-project-rethink-paradigms-infa'
    → '/Users/samanvayayagsen/project/rethink-paradigms/infa'
    """
    if raw == "-":
        return "/"
    return "/" + raw.lstrip("-").replace("-", "/")


def _strip_claude_worktree_suffix(segment: str) -> str:
    """Remove Claude worktree suffix from a segment if present.

    'infa--claude-worktrees-modest-beaver-5df149' → 'infa'
    """
    for suffix in CLAUDE_WORKTREE_SUFFIXES:
        idx = segment.find(suffix)
        if idx != -1:
            return segment[:idx]
    return segment


def _load_gemini_projects() -> dict[str, str]:
    """Load the Gemini projects.json mapping (path → canonical name)."""
    path = Path.home() / ".gemini" / "projects.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("projects", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _load_opencode_projects() -> dict[str, str]:
    """Load OpenCode project mapping from SQLite (worktree → name)."""
    db_path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT worktree, name FROM project").fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except sqlite3.Error:
        return {}


def canonicalize(agent: str, raw_identifier: str) -> str:
    """Convert a raw project identifier from any agent to a canonical project name.

    Args:
        agent: 'opencode', 'claude', or 'gemini'
        raw_identifier: The raw project path/name from the agent

    Returns:
        Canonical project name (lowercase, no spaces, hyphens preserved).
    """
    agent = agent.lower().strip()

    if agent == "opencode":
        return _canonicalize_opencode(raw_identifier)
    elif agent == "claude":
        return _canonicalize_claude(raw_identifier)
    elif agent == "gemini":
        return _canonicalize_gemini(raw_identifier)
    else:
        raise ValueError(
            f"Unknown agent: {agent!r}. Expected 'opencode', 'claude', or 'gemini'."
        )


def _canonicalize_opencode(raw_identifier: str) -> str:
    """OpenCode: raw_identifier is a filesystem path (worktree).

    Rule: last non-generic path segment, lowercase.
    """
    return _last_nongeneric_segment(raw_identifier)


def _claude_raw_to_candidates(raw: str) -> list[str]:
    """Generate candidate filesystem paths from a Claude dash-encoded name.

    Claude replaces '/' with '-', which is ambiguous when project names
    contain hyphens (e.g. 'ai-re' vs 'ai/re'). We generate candidates
    by trying different split points for hyphens, longest-suffix first.
    """
    body = raw.lstrip("-")
    parts = body.split("-")

    candidates = []
    for i in range(len(parts)):
        candidate = "/" + "/".join(parts[: i + 1]) + "-" + "-".join(parts[i + 1 :])
        candidates.append(candidate)
    candidates.append("/" + body.replace("-", "/"))
    return candidates


def _canonicalize_claude(raw_identifier: str) -> str:
    """Claude: raw_identifier is a dash-encoded directory name.

    Strategy: try to match against known OpenCode project paths first
    (they share the same filesystem). Fall back to naive dash→slash
    decode with last non-generic segment extraction.
    """
    if raw_identifier == "-":
        return "root"

    raw_clean = _strip_claude_worktree_suffix(raw_identifier)
    if raw_clean != raw_identifier:
        return _canonicalize_claude(raw_clean)

    oc_projects = _load_opencode_projects()
    body = raw_identifier.lstrip("-")

    for oc_path in oc_projects:
        oc_encoded = oc_path.lstrip("/").replace("/", "-")
        if body == oc_encoded:
            return _last_nongeneric_segment(oc_path)

    for oc_path in oc_projects:
        oc_encoded = oc_path.lstrip("/").replace("/", "-")
        if body.startswith(oc_encoded + "-"):
            return _last_nongeneric_segment(oc_path)

    decoded_path = _claude_raw_to_path(raw_identifier)
    conductor_ws = _extract_conductor_workspace(decoded_path)
    if conductor_ws:
        return conductor_ws.lower()

    return _last_nongeneric_segment(decoded_path)


def _canonicalize_gemini(raw_identifier: str) -> str:
    """GeminiCLI: raw_identifier is either a hash, a plain name, or a path.

    Resolution order:
      1. Check projects.json for matching path → use its canonical name
      2. If it looks like a directory under ~/.gemini/history/ that is a plain name → use it
      3. Fallback: check ~/.gemini/history/{hash}/.project_root → extract from path
      4. Final fallback: first 12 chars of hash
    """
    projects_map = _load_gemini_projects()

    if raw_identifier in projects_map:
        return projects_map[raw_identifier].lower()

    is_hash = len(raw_identifier) >= 16 and all(
        c in "0123456789abcdef" for c in raw_identifier
    )

    if not is_hash:
        return raw_identifier.lower()

    history_base = Path.home() / ".gemini" / "history"
    hash_dir = history_base / raw_identifier

    project_root_file = hash_dir / ".project_root"
    if project_root_file.exists():
        try:
            root_path = project_root_file.read_text().strip()
            if root_path in projects_map:
                return projects_map[root_path].lower()
            return _last_nongeneric_segment(root_path)
        except OSError:
            pass

    if hash_dir.exists():
        for root_file in hash_dir.glob(".project_*"):
            try:
                content = root_file.read_text().strip()
                if content in projects_map:
                    return projects_map[content].lower()
            except OSError:
                continue

    return raw_identifier[:12]


def build_mapping_log(output_dir: str = ".") -> str:
    """Scan all 3 agent data sources and write a mapping.log.

    Returns the path to the generated log file.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    log_file = output_path / "mapping.log"

    lines: list[str] = []
    lines.append(f"# Project Name Canonicalization Mapping")
    lines.append(f"# Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    lines.append("## OpenCode Projects")
    lines.append("-" * 60)
    oc_projects = _load_opencode_projects()
    for worktree, name in sorted(oc_projects.items()):
        canonical = canonicalize("opencode", worktree)
        lines.append(f"  {worktree}")
        lines.append(f"    → name={name!r}  canonical={canonical!r}")
    lines.append("")

    lines.append("## Claude Projects")
    lines.append("-" * 60)
    claude_dir = Path.home() / ".claude" / "projects"
    if claude_dir.exists():
        for entry in sorted(claude_dir.iterdir()):
            if entry.is_dir():
                raw = entry.name
                canonical = canonicalize("claude", raw)
                lines.append(f"  {raw}")
                lines.append(f"    → canonical={canonical!r}")
    lines.append("")

    lines.append("## GeminiCLI Projects")
    lines.append("-" * 60)
    gemini_projects = _load_gemini_projects()
    for path, name in sorted(gemini_projects.items()):
        canonical = canonicalize("gemini", path)
        lines.append(f"  {path}")
        lines.append(f"    → name={name!r}  canonical={canonical!r}")

    history_base = Path.home() / ".gemini" / "history"
    if history_base.exists():
        known_names = set(gemini_projects.values())
        for entry in sorted(history_base.iterdir()):
            if entry.is_dir() and entry.name not in known_names:
                canonical = canonicalize("gemini", entry.name)
                lines.append(f"  [hash] {entry.name}")
                lines.append(f"    → canonical={canonical!r}")
    lines.append("")

    log_content = "\n".join(lines) + "\n"
    log_file.write_text(log_content)
    return str(log_file)


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
        agent, raw = sys.argv[1], sys.argv[2]
        print(canonicalize(agent, raw))
    else:
        print("Usage: python -m tools.project_mapping <agent> <raw_identifier>")
        print("       python -m tools.project_mapping --log [output_dir]")
