#!/usr/bin/env python3
"""
Session Query — Configuration Resolution Module

Resolves the session database path, output directory, and tools directory
using a priority chain:

  1. SESSION_QUERY_DB env var           (highest priority — scripting/CI)
  2. ~/.session-query/config.yaml        (persistent configuration)
  3. sessions.db in CWD                  (auto-detect: already in sessions repo?)
  4. Parent directory walk              (find sessions.db in ancestors)
  5. ~/.session-query/sessions.db       (ultimate fallback)

Also resolves SESSION_QUERY_OUTPUT_DIR (where extracted sessions live)
and SESSION_QUERY_SCRIPTS_DIR (where extraction tools are — defaults to
the scripts/ directory alongside this module).

Usage:
    from scripts.config import resolve_db_path, resolve_output_dir, resolve_scripts_dir

    db = resolve_db_path()
    output = resolve_output_dir()
    tools = resolve_scripts_dir()
"""

import os
import sys
from pathlib import Path

# ————————————————————————————————————————————————————————————————————
# Default locations
# ————————————————————————————————————————————————————————————————————

DEFAULT_HOME_DIR = Path.home() / ".session-query"
DEFAULT_CONFIG_FILE = DEFAULT_HOME_DIR / "config.yaml"
DEFAULT_DB_PATH = DEFAULT_HOME_DIR / "sessions.db"
DEFAULT_OUTPUT_DIR = DEFAULT_HOME_DIR / "sessions"


def _load_config() -> dict:
    """Load ~/.session-query/config.yaml if it exists.

    Returns empty dict if file doesn't exist or can't be parsed.
    Supports a minimal subset of YAML (flat keys only — no nesting needed).
    """
    config_path = os.environ.get(
        "SESSION_QUERY_CONFIG",
        str(DEFAULT_CONFIG_FILE),
    )
    try:
        with open(config_path) as f:
            content = f.read()
    except OSError:
        return {}

    config: dict = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("\"'")
            if value:
                config[key] = value
    return config


def _walk_up_for_file(start_dir: Path, filename: str) -> Path | None:
    """Walk up from start_dir looking for filename.

    Returns the full path if found, None otherwise.
    Stops at filesystem root.
    """
    current = start_dir.resolve()
    while True:
        candidate = current / filename
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:  # reached root
            return None
        current = parent


def resolve_db_path(verbose: bool = False) -> str:
    """Resolve the path to sessions.db using the priority chain.

    Returns an absolute path string. May point to a non-existent file
    (callers should handle that case — e.g., offer to build the index).
    """
    # 1. Environment variable (highest priority)
    env_db = os.environ.get("SESSION_QUERY_DB")
    if env_db:
        resolved = str(Path(env_db).expanduser().resolve())
        if verbose:
            print(f"Using SESSION_QUERY_DB: {resolved}", file=sys.stderr)
        return resolved

    # 2. Config file
    config = _load_config()
    config_db = config.get("sessions_db") or config.get("db_path")
    if config_db:
        resolved = str(Path(config_db).expanduser().resolve())
        if verbose:
            print(f"Using config file: {resolved}", file=sys.stderr)
        return resolved

    # 3. CWD — sessions.db in current directory
    cwd_candidate = Path.cwd() / "sessions.db"
    if cwd_candidate.is_file():
        if verbose:
            print(f"Auto-detected: {cwd_candidate}", file=sys.stderr)
        return str(cwd_candidate.resolve())

    # 4. Walk up from CWD
    found = _walk_up_for_file(Path.cwd(), "sessions.db")
    if found:
        if verbose:
            print(f"Auto-detected (parent): {found}", file=sys.stderr)
        return str(found)

    # 5. Default fallback
    fallback = str(DEFAULT_DB_PATH.resolve())
    if verbose:
        print(f"No sessions.db found. Default: {fallback}", file=sys.stderr)
    return fallback


def resolve_output_dir() -> str:
    """Resolve the extracted sessions output directory.

    Priority: SESSION_QUERY_OUTPUT_DIR env var → config → CWD /sessions/ → default.
    """
    env_out = os.environ.get("SESSION_QUERY_OUTPUT_DIR")
    if env_out:
        return str(Path(env_out).expanduser().resolve())

    config = _load_config()
    config_out = config.get("output_dir")
    if config_out:
        return str(Path(config_out).expanduser().resolve())

    cwd_candidate = Path.cwd() / "sessions"
    if cwd_candidate.is_dir():
        return str(cwd_candidate.resolve())

    return str(DEFAULT_OUTPUT_DIR.resolve())


def resolve_scripts_dir() -> str:
    """Resolve the directory containing extraction scripts.

    Defaults to the scripts/ directory alongside this config module.
    Override with SESSION_QUERY_SCRIPTS_DIR env var.
    """
    env_scripts = os.environ.get("SESSION_QUERY_SCRIPTS_DIR")
    if env_scripts:
        return str(Path(env_scripts).expanduser().resolve())

    # Default: the directory containing this file
    return str(Path(__file__).resolve().parent)


def init_config() -> str:
    """Create ~/.session-query/config.yaml with sensible defaults.

    Called on first-time setup. Will not overwrite an existing config file.

    Returns the path to the config file.
    """
    config_path = Path(os.environ.get("SESSION_QUERY_CONFIG", str(DEFAULT_CONFIG_FILE)))
    if config_path.exists():
        return str(config_path)

    config_path.parent.mkdir(parents=True, exist_ok=True)

    default_content = f"""# Session Query Configuration
# Generated by session-query skill (scripts/config.py)

# Path to your sessions.db SQLite database
sessions_db: {DEFAULT_DB_PATH}

# Directory where extracted session files live
output_dir: {DEFAULT_OUTPUT_DIR}

# Directory containing extraction scripts (extract_*.py, index_sessions.py)
# Leave commented to auto-detect (defaults to the bundled scripts/)
# scripts_dir: ~/.promptql/skills/session-query/scripts

# Source paths for extraction (auto-detected on most systems)
# opencode_db: ~/.local/share/opencode/opencode.db
# claude_projects_dir: ~/.claude/projects
# gemini_tmp_dir: ~/.gemini/tmp
"""
    config_path.write_text(default_content)
    return str(config_path)


# ————————————————————————————————————————————————————————————————————
# CLI mode
# ————————————————————————————————————————————————————————————————————

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Session Query — Configuration Resolution"
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=["db", "output", "scripts", "init", "paths"],
        default="paths",
        help="What to resolve (default: paths — show all)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show resolution source",
    )
    args = parser.parse_args()

    if args.action == "init":
        path = init_config()
        print(f"Config created: {path}")
    elif args.action == "db":
        print(resolve_db_path(verbose=args.verbose))
    elif args.action == "output":
        print(resolve_output_dir())
    elif args.action == "scripts":
        print(resolve_scripts_dir())
    else:  # paths
        db = resolve_db_path(verbose=True)
        out = resolve_output_dir()
        scripts = resolve_scripts_dir()
        print(f"sessions.db:     {db}")
        print(f"output dir:      {out}")
        print(f"scripts dir:     {scripts}")
