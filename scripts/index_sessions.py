#!/usr/bin/env python3
"""
Index extracted session files into a SQLite database.

Scans {output_dir}/{project}/{agent}/*.jsonl|*.json files and builds
a queryable index with session metadata (no message content).

Supports incremental updates: skips unchanged files, updates changed ones,
removes entries for deleted files.

Usage:
    python3 tools/index_sessions.py --output-dir ./sessions/
    python3 tools/index_sessions.py --output-dir ./sessions/ --db-path ./sessions.db
    python3 tools/index_sessions.py --output-dir ./sessions/ --dry-run

As a module:
    from tools.index_sessions import build_index
    stats = build_index(output_dir="./sessions/", db_path="./sessions.db")
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


AGENT_DIRS = {"opencode", "claude", "gemini"}

SCHEMA_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT NOT NULL,
    agent        TEXT NOT NULL,
    project      TEXT NOT NULL,
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    parent_session_id TEXT,
    file_path    TEXT NOT NULL,
    file_size    INTEGER DEFAULT 0,
    indexed_at   TEXT NOT NULL,
    PRIMARY KEY (agent, session_id)
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_time);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_agent_project ON sessions(agent, project);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);
"""

SCHEMA_FILE_FINGERPRINTS = """
CREATE TABLE IF NOT EXISTS file_fingerprints (
    agent      TEXT NOT NULL,
    file_path  TEXT NOT NULL,
    file_size  INTEGER NOT NULL,
    PRIMARY KEY (agent, file_path)
);
"""

SCHEMA_EXTRACTION_LOG = """
CREATE TABLE IF NOT EXISTS extraction_log (
    run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent            TEXT NOT NULL,
    ran_at           TEXT NOT NULL,
    mode             TEXT NOT NULL,
    watermark_used   TEXT,
    sessions_found   INTEGER DEFAULT 0,
    sessions_new     INTEGER DEFAULT 0,
    sessions_updated INTEGER DEFAULT 0
);
"""


def _utcnow_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _init_db(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they don't exist."""
    conn.executescript(
        SCHEMA_SESSIONS + SCHEMA_FILE_FINGERPRINTS + SCHEMA_EXTRACTION_LOG
    )
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN parent_session_id TEXT")
    except sqlite3.OperationalError:
        pass
    conn.executescript(SCHEMA_INDEXES)


def _scan_session_files(output_dir: Path) -> list[tuple[str, str, Path, int]]:
    """Walk output_dir and find all session files.

    Returns list of (agent, project, file_path, file_size) tuples.
    file_path is relative to output_dir.
    """
    results = []
    output_dir = output_dir.resolve()

    for project_entry in sorted(output_dir.iterdir()):
        if not project_entry.is_dir():
            continue

        is_unresolved = project_entry.name == "_unresolved"

        for agent_entry in sorted(project_entry.iterdir()):
            if not agent_entry.is_dir():
                continue

            if is_unresolved and agent_entry.name in AGENT_DIRS:
                agent = agent_entry.name
                for f in sorted(agent_entry.iterdir()):
                    if not f.is_file():
                        continue
                    if f.suffix not in (".jsonl", ".json"):
                        continue
                    rel = str(f.relative_to(output_dir))
                    results.append((agent, "_unresolved", rel, f.stat().st_size))

                for hash_dir in sorted(agent_entry.iterdir()):
                    if not hash_dir.is_dir():
                        continue
                    for agent_sub in sorted(hash_dir.iterdir()):
                        if not agent_sub.is_dir() or agent_sub.name not in AGENT_DIRS:
                            continue
                        for f in sorted(agent_sub.iterdir()):
                            if not f.is_file():
                                continue
                            if f.suffix not in (".jsonl", ".json"):
                                continue
                            rel = str(f.relative_to(output_dir))
                            results.append(
                                (agent_sub.name, f"_unresolved/{hash_dir.name}", rel, f.stat().st_size)
                            )

            # Sub-directory agent dirs under _unresolved (e.g. _unresolved/gemini/{hash}/gemini/*.json)
            elif is_unresolved:
                subdir_name = agent_entry.name
                for sub_entry in sorted(agent_entry.iterdir()):
                    if not sub_entry.is_dir() or sub_entry.name not in AGENT_DIRS:
                        continue
                    agent = sub_entry.name
                    for f in sorted(sub_entry.iterdir()):
                        if not f.is_file():
                            continue
                        if f.suffix not in (".jsonl", ".json"):
                            continue
                        rel = str(f.relative_to(output_dir))
                        results.append(
                            (agent, f"_unresolved/{subdir_name}", rel, f.stat().st_size)
                        )

            # Normal case: {project}/{agent}/*.jsonl|*.json
            elif agent_entry.name in AGENT_DIRS:
                agent = agent_entry.name
                project = project_entry.name
                for f in sorted(agent_entry.iterdir()):
                    if not f.is_file():
                        continue
                    if f.suffix not in (".jsonl", ".json"):
                        continue
                    rel = str(f.relative_to(output_dir))
                    results.append((agent, project, rel, f.stat().st_size))

    return results


def _parse_opencode(filepath: Path) -> dict | None:
    """Parse an OpenCode session file (single-line JSON despite .jsonl extension).

    Returns dict with session_id, start_time, end_time, message_count or None.
    """
    try:
        with open(filepath) as f:
            data = json.loads(f.read())
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: Failed to parse {filepath}: {e}", file=sys.stderr)
        return None

    messages = data.get("messages", [])
    if not messages:
        return None

    session_id = data.get("session_id")
    if not session_id:
        return None

    created_at = data.get("created_at")
    updated_at = data.get("updated_at")

    # Epoch milliseconds → ISO 8601
    start_time = _epoch_ms_to_iso(created_at) if isinstance(created_at, (int, float)) else created_at
    end_time = _epoch_ms_to_iso(updated_at) if isinstance(updated_at, (int, float)) else updated_at

    if not start_time or not end_time:
        return None

    return {
        "session_id": str(session_id),
        "start_time": start_time,
        "end_time": end_time,
        "message_count": len(messages),
        "parent_session_id": data.get("parent_session_id"),
    }


def _epoch_ms_to_iso(val) -> str | None:
    """Convert epoch milliseconds to ISO 8601 string."""
    if val is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(val) / 1000, tz=timezone.utc)
        return dt.isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _parse_claude(filepath: Path) -> dict | None:
    """Parse a Claude session file (multi-line JSONL).

    Returns dict with session_id, start_time, end_time, message_count or None.
    """
    try:
        with open(filepath) as f:
            lines = [line for line in f if line.strip()]
    except OSError as e:
        print(f"  WARNING: Failed to read {filepath}: {e}", file=sys.stderr)
        return None

    if not lines:
        return None

    message_count = len(lines)
    session_id = filepath.stem

    # Extract timestamps from first and last valid JSON lines
    first_ts = None
    last_ts = None

    # First line with a timestamp
    for line in lines:
        try:
            obj = json.loads(line)
            ts = obj.get("timestamp")
            if ts:
                first_ts = ts
                break
        except json.JSONDecodeError:
            continue

    # Last line with a timestamp
    for line in reversed(lines):
        try:
            obj = json.loads(line)
            ts = obj.get("timestamp")
            if ts:
                last_ts = ts
                break
        except json.JSONDecodeError:
            continue

    if not first_ts or not last_ts:
        try:
            mtime = datetime.fromtimestamp(filepath.stat().st_mtime, tz=timezone.utc).isoformat()
            first_ts = first_ts or mtime
            last_ts = last_ts or mtime
        except OSError:
            return None

    parent_id = None
    meta_path = filepath.parent / (filepath.stem + ".meta.json")
    if meta_path.exists():
        try:
            with open(meta_path) as mf:
                meta = json.load(mf)
            parent_id = meta.get("parent_session_id")
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "session_id": session_id,
        "start_time": first_ts,
        "end_time": last_ts,
        "message_count": message_count,
        "parent_session_id": parent_id,
    }


def _parse_gemini(filepath: Path) -> dict | None:
    """Parse a Gemini session file (single JSON object).

    Returns dict with session_id, start_time, end_time, message_count or None.
    """
    try:
        with open(filepath) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: Failed to parse {filepath}: {e}", file=sys.stderr)
        return None

    messages = data.get("messages", [])
    if not messages:
        return None

    session_id = data.get("sessionId")
    start_time = data.get("startTime")
    end_time = data.get("lastUpdated")

    if not session_id or not start_time:
        return None

    return {
        "session_id": str(session_id),
        "start_time": start_time,
        "end_time": end_time or start_time,
        "message_count": len(messages),
        "parent_session_id": None,
    }


def _parse_file(agent: str, filepath: Path) -> dict | None:
    """Route to the correct parser based on agent type."""
    if agent == "opencode":
        return _parse_opencode(filepath)
    elif agent == "claude":
        return _parse_claude(filepath)
    elif agent == "gemini":
        return _parse_gemini(filepath)
    return None


def build_index(output_dir: str = "./sessions/", db_path: str = "./sessions.db", dry_run: bool = False) -> dict:
    """Build or update the session index SQLite database.

    Args:
        output_dir: Directory containing extracted session files.
        db_path: Path to the SQLite database file.
        dry_run: If True, scan and report but don't write to DB.

    Returns:
        Dict with stats: total_scanned, new, updated, skipped, removed, per_agent counts.
    """
    output_path = Path(output_dir).resolve()
    if not output_path.exists():
        print(f"Error: Output directory not found: {output_path}", file=sys.stderr)
        return {"error": "output_dir not found"}

    conn = sqlite3.connect(db_path)
    _init_db(conn)

    file_entries = _scan_session_files(output_path)
    print(f"Files scanned: {len(file_entries)}")

    stats = {
        "total_scanned": len(file_entries),
        "new": 0,
        "updated": 0,
        "skipped": 0,
        "removed": 0,
        "per_agent": {},
    }
    seen_paths = set()

    for agent, project, rel_path, file_size in file_entries:
        stats["per_agent"][agent] = stats["per_agent"].get(agent, 0) + 1
        seen_paths.add(rel_path)

        fp_row = conn.execute(
            "SELECT file_size FROM file_fingerprints WHERE agent = ? AND file_path = ?",
            (agent, rel_path),
        ).fetchone()

        if fp_row and fp_row[0] == file_size:
            stats["skipped"] += 1
            continue

        full_path = output_path / rel_path
        parsed = _parse_file(agent, full_path)

        if parsed is None:
            if not dry_run:
                conn.execute(
                    "INSERT OR REPLACE INTO file_fingerprints (agent, file_path, file_size) VALUES (?, ?, ?)",
                    (agent, rel_path, file_size),
                )
            stats["skipped"] += 1
            continue

        indexed_at = _utcnow_iso()

        has_session = conn.execute(
            "SELECT 1 FROM sessions WHERE agent = ? AND session_id = ?",
            (agent, parsed["session_id"]),
        ).fetchone()

        existing_session = conn.execute(
            "SELECT file_path FROM sessions WHERE agent = ? AND session_id = ?",
            (agent, parsed["session_id"]),
        ).fetchone()
        is_new = not has_session

        if not dry_run:
            conn.execute(
                """INSERT OR REPLACE INTO sessions
                   (session_id, agent, project, start_time, end_time,
                    message_count, parent_session_id, file_path, file_size, indexed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    parsed["session_id"],
                    agent,
                    project,
                    parsed["start_time"],
                    parsed["end_time"],
                    parsed["message_count"],
                    parsed.get("parent_session_id"),
                    rel_path,
                    file_size,
                    indexed_at,
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO file_fingerprints (agent, file_path, file_size) VALUES (?, ?, ?)",
                (agent, rel_path, file_size),
            )

        if is_new:
            stats["new"] += 1
        else:
            stats["updated"] += 1

    if not dry_run:
        all_db_paths = conn.execute("SELECT file_path FROM sessions").fetchall()
        for (fp,) in all_db_paths:
            if fp not in seen_paths:
                conn.execute("DELETE FROM sessions WHERE file_path = ?", (fp,))
                stats["removed"] += 1

        all_fp_paths = conn.execute("SELECT file_path FROM file_fingerprints").fetchall()
        for (fp,) in all_fp_paths:
            if fp not in seen_paths:
                conn.execute("DELETE FROM file_fingerprints WHERE file_path = ?", (fp,))

        conn.execute(
            """INSERT INTO extraction_log (agent, ran_at, mode, sessions_found, sessions_new, sessions_updated)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("all", _utcnow_iso(), "full", stats["total_scanned"], stats["new"], stats["updated"]),
        )
        conn.commit()

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Index extracted session files into a SQLite database"
    )
    parser.add_argument(
        "--output-dir",
        default="./sessions/",
        help="Directory containing extracted session files (default: ./sessions/)",
    )
    parser.add_argument(
        "--db-path",
        default="./sessions.db",
        help="Path to the SQLite database file (default: ./sessions.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report stats without writing to database",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("SESSION INDEXER")
    print("=" * 60)
    print(f"Output dir: {args.output_dir}")
    print(f"Database:   {args.db_path}")
    if args.dry_run:
        print("Mode: DRY-RUN")
    print()

    stats = build_index(
        output_dir=args.output_dir,
        db_path=args.db_path,
        dry_run=args.dry_run,
    )

    if "error" in stats:
        sys.exit(1)

    print()
    print("=" * 60)
    print("INDEX SUMMARY")
    print("=" * 60)
    print(f"Total files scanned: {stats['total_scanned']}")
    print(f"New:                 {stats['new']}")
    print(f"Updated:             {stats['updated']}")
    print(f"Skipped (unchanged): {stats['skipped']}")
    print(f"Removed (deleted):   {stats['removed']}")
    print()
    print("Per agent:")
    for agent in sorted(stats["per_agent"]):
        print(f"  {agent:12} {stats['per_agent'][agent]:5} files")
    print()

    if not args.dry_run:
        conn = sqlite3.connect(args.db_path)
        row_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        print(f"Database total rows: {row_count}")
        conn.close()

    print("=" * 60)


if __name__ == "__main__":
    main()
