#!/usr/bin/env python3
"""
Extract OpenCode conversation data into project-organized JSONL files.

Supports:
  - SQLite DB: ~/.local/share/opencode/opencode.db (primary)
  - CLI JSON storage: ~/.local/share/opencode/storage/ (fallback)
  - Desktop Tauri .dat files (fallback)

Output layout:
  {output_dir}/{project_name}/opencode/{session_id}.jsonl

Usage:
  python3 tools/extract_opencode.py --output-dir ./sessions/
  python3 tools/extract_opencode.py --dry-run --output-dir /tmp/test_oc/
  python3 tools/extract_opencode.py --project sessions --output-dir ./sessions/
"""

import argparse
import json
import os
import platform
import re
import sqlite3
import struct
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def canonical_project_name(directory, worktree=None):
    """
    Extract project name from directory or worktree path.
    Uses the last non-empty segment of the path.

    Examples:
      /Users/x/project/rethink-paradigms/infa -> infa
      /Users/x/project/sessions -> sessions
      /Users/x -> x
    """
    source = worktree if worktree else directory
    if not source:
        return "unknown"
    return source.rstrip("/").rsplit("/", 1)[-1] or "unknown"


def extract_from_sqlite(db_path, output_dir, dry_run=False, project_filter=None, since_ms=None):
    """
    Extract conversations from OpenCode SQLite database.
    Streams messages per-session to avoid loading everything into memory.
    """
    if not os.path.exists(db_path):
        print(f"  SQLite DB not found: {db_path}")
        return 0

    db_size_mb = os.path.getsize(db_path) / (1024 * 1024)
    print(f"  SQLite DB: {db_path} ({db_size_mb:.0f} MB)")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if since_ms is not None:
        cur.execute(
            """SELECT s.id, s.title, s.directory, s.project_id,
                      s.parent_id, s.time_created, s.time_updated, s.version,
                      p.worktree, p.name AS project_name
               FROM session s
               JOIN project p ON s.project_id = p.id
               WHERE s.time_updated > ?
               ORDER BY s.time_created""",
            (since_ms,),
        )
    else:
        cur.execute("""
            SELECT s.id, s.title, s.directory, s.project_id,
                   s.parent_id, s.time_created, s.time_updated, s.version,
                   p.worktree, p.name AS project_name
            FROM session s
            JOIN project p ON s.project_id = p.id
            ORDER BY s.time_created
        """)
    sessions = cur.fetchall()
    print(f"  Found {len(sessions)} sessions")

    project_sessions = defaultdict(list)
    for row in sessions:
        pname = canonical_project_name(row["directory"], row["worktree"])
        if pname == "unknown" and row["project_name"]:
            pname = row["project_name"]
        project_sessions[pname].append(dict(row))

    if project_filter:
        if project_filter not in project_sessions:
            print(f"  No sessions found for project '{project_filter}'")
            print(f"  Available projects: {', '.join(sorted(project_sessions.keys()))}")
            conn.close()
            return 0
        project_sessions = {project_filter: project_sessions[project_filter]}

    files_written = 0

    for pname, sess_list in sorted(project_sessions.items()):
        print(f"\n  Project: {pname} ({len(sess_list)} sessions)")

        for sess in sess_list:
            session_id = sess["id"]
            out_path = os.path.join(
                output_dir, pname, "opencode", f"{session_id}.jsonl"
            )

            cur.execute(
                """
                SELECT id, data FROM message
                WHERE session_id = ?
                ORDER BY time_created, id
            """,
                (session_id,),
            )
            messages = cur.fetchall()

            if not messages:
                continue

            lines = []
            for msg_row in messages:
                try:
                    msg_data = json.loads(msg_row["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                message_id = msg_row["id"]
                message = {
                    "role": msg_data.get("role", "assistant"),
                    "content": "",
                    "timestamp": msg_data.get("time", {}).get("created")
                    if isinstance(msg_data.get("time"), dict)
                    else msg_data.get("time"),
                }

                for key in (
                    "modelID",
                    "providerID",
                    "agent",
                    "mode",
                    "cost",
                    "tokens",
                    "finish",
                ):
                    if key in msg_data:
                        out_key = {"modelID": "model", "providerID": "provider"}.get(
                            key, key
                        )
                        message[out_key] = msg_data[key]

                cur.execute(
                    """
                    SELECT id, data FROM part
                    WHERE message_id = ?
                    ORDER BY time_created, id
                """,
                    (message_id,),
                )
                parts = cur.fetchall()

                content_parts = []
                tool_calls = []
                tool_results = []
                reasoning_parts = []

                for part_row in parts:
                    try:
                        part_data = json.loads(part_row["data"])
                    except (json.JSONDecodeError, TypeError):
                        continue

                    part_type = part_data.get("type", "")
                    part_text = part_data.get("text", "")

                    if part_type == "text":
                        content_parts.append(part_text)
                    elif part_type in ("tool", "tool-call"):
                        state = part_data.get("state", {})
                        tool_name = part_data.get("tool", part_data.get("name"))
                        tool_call = {
                            "id": part_data.get("callID", part_data.get("id")),
                            "name": tool_name,
                            "input": state.get("input", part_data.get("input")),
                        }
                        if state.get("status") == "completed" and "output" in state:
                            tool_results.append(
                                {
                                    "tool_call_id": part_data.get("callID"),
                                    "tool": tool_name,
                                    "output": state["output"],
                                }
                            )
                        tool_calls.append(tool_call)
                    elif part_type == "tool-result":
                        tool_results.append(
                            {
                                "tool_call_id": part_data.get("toolCallID"),
                                "output": part_data.get("output"),
                            }
                        )
                    elif part_type == "code":
                        code_text = part_data.get("text", "")
                        language = part_data.get("language", "")
                        content_parts.append(f"```{language}\n{code_text}\n```")
                    elif part_type == "reasoning":
                        if part_text:
                            reasoning_parts.append(part_text)

                message["content"] = "\n".join(content_parts)
                if tool_calls:
                    message["tool_calls"] = tool_calls
                if tool_results:
                    message["tool_results"] = tool_results
                if reasoning_parts:
                    message["reasoning"] = "\n".join(reasoning_parts)

                lines.append(message)

            if not lines:
                continue

            session_record = {
                "session_id": session_id,
                "parent_session_id": sess.get("parent_id"),
                "title": sess.get("title"),
                "directory": sess.get("directory"),
                "project_id": sess.get("project_id"),
                "source": "opencode-sqlite",
                "created_at": sess.get("time_created"),
                "updated_at": sess.get("time_updated"),
                "version": sess.get("version"),
                "messages": lines,
            }

            if sess.get("worktree"):
                session_record["worktree"] = sess["worktree"]
            if sess.get("project_name"):
                session_record["project_name"] = sess["project_name"]

            if dry_run:
                print(f"    [DRY-RUN] {out_path} ({len(lines)} messages)")
            else:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "w") as f:
                    f.write(json.dumps(session_record, ensure_ascii=False) + "\n")
                print(f"    {out_path} ({len(lines)} messages)")

            files_written += 1

    conn.close()
    return files_written


def extract_cli_conversations(storage_dir):
    """Extract conversations from CLI JSON storage (fallback)."""
    conversations = []
    message_dir = storage_dir / "storage" / "message"
    part_dir = storage_dir / "storage" / "part"

    if not message_dir.exists():
        return conversations

    session_dirs = [
        d for d in message_dir.iterdir() if d.is_dir() and d.name.startswith("ses_")
    ]
    for session_dir_path in session_dirs:
        try:
            session_id = session_dir_path.name
            session_file = (
                storage_dir / "storage" / "session" / "global" / f"{session_id}.json"
            )
            session_data = None
            if session_file.exists():
                with open(session_file) as f:
                    session_data = json.load(f)

            message_files = sorted(session_dir_path.glob("msg_*.json"))
            if not message_files:
                continue

            messages = []
            for msg_file in message_files:
                with open(msg_file) as f:
                    msg_data = json.load(f)

                message_id = msg_data.get("id")
                role = msg_data.get("role", "assistant")
                msg_time = msg_data.get("time", {}).get("created")

                message = {"role": role, "content": "", "timestamp": msg_time}
                for key in ("modelID", "providerID", "agent", "mode", "tokens", "cost"):
                    if key in msg_data:
                        out_key = {"modelID": "model", "providerID": "provider"}.get(
                            key, key
                        )
                        message[out_key] = msg_data[key]

                message_part_dir = part_dir / message_id
                if message_part_dir.exists():
                    content_parts, tool_calls, tool_results, reasoning_parts = (
                        [],
                        [],
                        [],
                        [],
                    )
                    for part_file in sorted(message_part_dir.glob("prt_*.json")):
                        with open(part_file) as f:
                            part_data = json.load(f)
                        part_type = part_data.get("type")
                        part_text = part_data.get("text", "")
                        if part_type == "text":
                            content_parts.append(part_text)
                        elif part_type in ("tool", "tool-call"):
                            state = part_data.get("state", {})
                            tool_name = part_data.get("tool", part_data.get("name"))
                            tool_calls.append(
                                {
                                    "id": part_data.get("callID"),
                                    "name": tool_name,
                                    "input": state.get("input"),
                                }
                            )
                            if state.get("status") == "completed" and "output" in state:
                                tool_results.append(
                                    {
                                        "tool_call_id": part_data.get("callID"),
                                        "tool": tool_name,
                                        "output": state["output"],
                                    }
                                )
                        elif part_type == "tool-result":
                            tool_results.append(
                                {
                                    "tool_call_id": part_data.get("toolCallID"),
                                    "output": part_data.get("output"),
                                }
                            )
                        elif part_type == "code":
                            content_parts.append(
                                f"```{part_data.get('language', '')}\n{part_data.get('text', '')}\n```"
                            )
                        elif part_type == "reasoning" and part_text:
                            reasoning_parts.append(part_text)

                    message["content"] = "\n".join(content_parts)
                    if tool_calls:
                        message["tool_calls"] = tool_calls
                    if tool_results:
                        message["tool_results"] = tool_results
                    if reasoning_parts:
                        message["reasoning"] = "\n".join(reasoning_parts)

                messages.append(message)

            if not messages:
                continue

            conversation = {
                "messages": messages,
                "source": "opencode-cli",
                "session_id": session_id,
            }
            if session_data:
                conversation["title"] = session_data.get("title")
                conversation["created_at"] = session_data.get("time", {}).get("created")
                conversation["updated_at"] = session_data.get("time", {}).get("updated")
                conversation["directory"] = session_data.get("directory")
                conversation["project_id"] = session_data.get("projectID")
            conversations.append(conversation)
        except Exception as e:
            print(f"    Error: {e}")
            continue
    return conversations


def find_opencode_installations():
    """Find all OpenCode installation directories (for JSON/fallback mode)."""
    system = platform.system()
    home = Path.home()
    locations = []
    cli_dirs = []
    if system == "Darwin":
        cli_dirs = [
            home / "Library/Application Support/opencode",
            Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "opencode",
        ]
    elif system == "Linux":
        cli_dirs = [
            Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "opencode"
        ]
    for d in cli_dirs:
        if d.exists():
            locations.append(("cli", d))
    desktop_dirs = []
    if system == "Darwin":
        desktop_dirs = [home / "Library/Application Support/ai.opencode.app"]
    elif system == "Linux":
        desktop_dirs = [home / ".local/share/ai.opencode.app"]
    for d in desktop_dirs:
        if d.exists():
            locations.append(("desktop", d))
    return locations


def write_json_sessions(conversations, output_dir, dry_run=False, project_filter=None):
    """Write JSON-based conversations to project-organized files."""
    files_written = 0
    for conv in conversations:
        directory = conv.get("directory", "")
        pname = canonical_project_name(directory)
        if project_filter and pname != project_filter:
            continue
        session_id = conv.get("session_id", "unknown")
        out_path = os.path.join(output_dir, pname, "opencode", f"{session_id}.jsonl")
        if dry_run:
            print(
                f"    [DRY-RUN] {out_path} ({len(conv.get('messages', []))} messages)"
            )
        else:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w") as f:
                f.write(json.dumps(conv, ensure_ascii=False) + "\n")
        files_written += 1
    return files_written


def main():
    parser = argparse.ArgumentParser(
        description="Extract OpenCode conversations into project-organized JSONL files"
    )
    parser.add_argument(
        "--output-dir",
        default="./sessions/",
        help="Output directory (default: ./sessions/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned output paths without writing files",
    )
    parser.add_argument(
        "--project", default=None, help="Only extract sessions for this project name"
    )
    parser.add_argument(
        "--db-path", default=None, help="Path to opencode.db (default: auto-detect)"
    )
    parser.add_argument(
        "--since", default=None,
        help="Only extract sessions updated since this ISO 8601 date"
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    dry_run = args.dry_run
    project_filter = args.project

    since_ms = None
    if args.since:
        since_ms = int(datetime.fromisoformat(args.since).timestamp() * 1000)

    print("=" * 80)
    print("OPENCODE EXTRACTION")
    print("=" * 80)
    print(f"\nOutput directory: {output_dir}")
    if project_filter:
        print(f"Project filter: {project_filter}")
    if args.since:
        print(f"Since: {args.since} (epoch ms: {since_ms})")
    if dry_run:
        print("Mode: DRY-RUN (no files written)")
    print()

    total_files = 0

    db_path = args.db_path
    if not db_path:
        home = Path.home()
        candidates = [
            home / ".local/share/opencode/opencode.db",
            Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
            / "opencode"
            / "opencode.db",
        ]
        for c in candidates:
            if c.exists():
                db_path = str(c)
                break

    if db_path:
        print("--- SQLite DB Extraction ---")
        n = extract_from_sqlite(
            db_path, output_dir, dry_run=dry_run, project_filter=project_filter,
            since_ms=since_ms
        )
        total_files += n
        print()

    installations = find_opencode_installations()
    for install_type, install_dir in installations:
        if install_type == "cli":
            print(f"--- JSON Storage: {install_dir} ---")
            conversations = extract_cli_conversations(install_dir)
            if conversations:
                print(f"  Found {len(conversations)} conversations from JSON storage")
                n = write_json_sessions(
                    conversations,
                    output_dir,
                    dry_run=dry_run,
                    project_filter=project_filter,
                )
                total_files += n
            print()

    if total_files == 0:
        print("No sessions extracted.")
        if not db_path and not installations:
            print("No OpenCode data found. Searched:")
            print("  SQLite: ~/.local/share/opencode/opencode.db")
            print("  CLI JSON: ~/.local/share/opencode/storage/")
            print("  macOS: ~/Library/Application Support/opencode")
        sys.exit(1)

    mode_str = "would be " if dry_run else ""
    print(f"\n{'=' * 80}")
    print(f"DONE: {total_files} session files {mode_str}written to {output_dir}")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
