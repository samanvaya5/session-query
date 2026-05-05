#!/usr/bin/env python3
"""
Extract ALL Google Gemini CLI chat data
Includes: messages, thoughts (reasoning), token usage, model info
Auto-discovers Gemini CLI installations on the device

Output structure: {output_dir}/{project_name}/gemini/{session_filename}.json
"""

import json
import argparse
import hashlib
from pathlib import Path
import platform
import os
import shutil
from datetime import datetime


def find_gemini_installations():
    """Find all Gemini CLI installation directories"""
    system = platform.system()
    home = Path.home()

    locations = []

    gemini_patterns = ["gemini", ".gemini"]

    if system == "Darwin":  # macOS
        base_dirs = [home, home / ".config"]
    elif system == "Linux":
        base_dirs = [
            home / ".gemini",
            home / ".config/gemini",
            home / ".local/share/gemini",
            home,
        ]
    elif system == "Windows":
        base_dirs = [
            Path(os.environ.get("USERPROFILE", home)) / ".gemini",
            Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "gemini",
            home,
        ]
    else:
        base_dirs = [home / ".gemini", home / ".config", home]

    for base_dir in base_dirs:
        if not base_dir.exists():
            continue

        for pattern in gemini_patterns:
            gemini_dir = base_dir / pattern
            if gemini_dir.exists():
                locations.append(gemini_dir)

    return list(set(locations))


def build_project_name_mapping(installation):
    """
    Build mapping from tmp directory name -> readable project name.

    Resolution strategy:
    1. Named directories (e.g., "infa", "europe") -> use name directly
    2. Hash directories -> SHA256(project_path) from projects.json
    3. Check history/{hash}/.project_root for path info
    4. Fallback: first 12 chars of hash
    """
    projects_json = installation / "projects.json"
    history_dir = installation / "history"
    tmp_dir = installation / "tmp"

    # Step 1: Build hash -> project name from projects.json
    hash_to_name = {}
    path_to_name = {}
    if projects_json.exists():
        try:
            with open(projects_json) as f:
                data = json.load(f)
            for proj_path, proj_name in data.get("projects", {}).items():
                path_to_name[proj_path] = proj_name
                # SHA256 of the absolute path gives the hash directory name
                path_hash = hashlib.sha256(proj_path.encode()).hexdigest()
                hash_to_name[path_hash] = proj_name
        except (json.JSONDecodeError, OSError):
            pass

    # Step 2: For hash dirs in tmp, also check history/{hash}/.project_root
    if tmp_dir.exists() and history_dir.exists():
        for d in tmp_dir.iterdir():
            if not d.is_dir() or d.name == "bin":
                continue
            if d.name in hash_to_name or len(d.name) <= 20:
                continue
            project_root_file = history_dir / d.name / ".project_root"
            if project_root_file.exists():
                try:
                    proj_path = project_root_file.read_text().strip()
                    if proj_path in path_to_name:
                        hash_to_name[d.name] = path_to_name[proj_path]
                    else:
                        hash_to_name[d.name] = Path(proj_path).name
                except OSError:
                    pass

    # Step 3: Build final mapping for all tmp dirs
    mapping = {}
    if tmp_dir.exists():
        for d in tmp_dir.iterdir():
            if not d.is_dir() or d.name == "bin":
                continue
            if d.name in hash_to_name:
                mapping[d.name] = hash_to_name[d.name]
            elif len(d.name) > 20:
                mapping[d.name] = d.name[:12]
            else:
                mapping[d.name] = d.name

    return mapping


def extract_gemini_session(session_file):
    """Extract conversation from a Gemini CLI session file (raw JSON)"""
    try:
        with open(session_file, "r") as f:
            data = json.load(f)

        if "messages" not in data or not data["messages"]:
            return None

        return data

    except (json.JSONDecodeError, KeyError, Exception):
        return None


def find_all_gemini_sessions(installation):
    """Find all Gemini CLI session files in an installation"""
    session_files = []

    tmp_dir = installation / "tmp"
    if tmp_dir.exists():
        # Pattern: tmp/{project_dir}/chats/session-*.json
        session_files.extend(tmp_dir.rglob("chats/session-*.json"))

    return session_files


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract Gemini CLI chat sessions organized by project"
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
        help="Print planned paths without writing files",
    )
    parser.add_argument(
        "--project", "-p", default=None, help="Filter to a specific project name"
    )
    parser.add_argument(
        "--since", default=None,
        help="Only extract sessions modified since this ISO 8601 date"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)

    since_ts = None
    if args.since:
        since_ts = datetime.fromisoformat(args.since).timestamp()

    print("=" * 80)
    print("GOOGLE GEMINI CLI DATA EXTRACTION")
    print("=" * 80)
    if args.dry_run:
        print("  (DRY RUN — no files written)")
    print(f"  Output: {output_dir.resolve()}")
    if args.project:
        print(f"  Filter: project={args.project}")
    if args.since:
        print(f"  Since: {args.since}")
    print()

    print("Searching for Gemini CLI installations...")
    installations = find_gemini_installations()

    if not installations:
        print("No Gemini CLI installations found!")
        return

    print(f"Found {len(installations)} installation(s):")
    for inst in installations:
        print(f"  - {inst}")
    print()

    total_copied = 0
    total_skipped = 0
    project_counts = {}

    for installation in installations:
        print(f"Processing: {installation}")
        name_mapping = build_project_name_mapping(installation)
        print(f"  Found {len(name_mapping)} project directories")

        session_files = find_all_gemini_sessions(installation)
        print(f"  Found {len(session_files)} session files")

        for session_file in session_files:
            if since_ts is not None and os.path.getmtime(session_file) <= since_ts:
                continue

            project_dir = session_file.parent.parent.name
            project_name = name_mapping.get(
                project_dir, project_dir[:12] if len(project_dir) > 20 else project_dir
            )

            if args.project and project_name != args.project:
                continue

            data = extract_gemini_session(session_file)
            if data is None:
                total_skipped += 1
                continue

            dest_dir = output_dir / project_name / "gemini"
            dest_file = dest_dir / session_file.name

            project_counts[project_name] = project_counts.get(project_name, 0) + 1

            if args.dry_run:
                print(f"  {session_file.name} -> {dest_file}")
                total_copied += 1
            else:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(session_file, dest_file)
                total_copied += 1

    print()
    print("=" * 80)
    print("EXTRACTION COMPLETE")
    print("=" * 80)
    print(f"Sessions copied: {total_copied:,}")
    print(f"Sessions skipped (empty/invalid): {total_skipped:,}")
    print()

    if project_counts:
        print("Breakdown by project:")
        for name, count in sorted(project_counts.items(), key=lambda x: -x[1]):
            print(f"  {name:30} {count:5,} sessions")
        print()
        print(f"Total projects: {len(project_counts)}")
    else:
        print("No sessions extracted.")

    if not args.dry_run and total_copied > 0:
        print()
        print(f"Output directory: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
