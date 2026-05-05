# Session Query

[![skills.sh](https://skills.sh/b/samanvaya5/session-query)](https://skills.sh/samanvaya5/session-query)

Search your AI coding history. An agent skill that lets you query through past coding sessions from OpenCode, Claude Code, and Gemini CLI — by project, agent, date range, or conversation content.

## Install

```bash
npx skills add samanvaya5/session-query
```

## What It Does

You've had hundreds of AI coding sessions. Some were epic debugging adventures, others solved a problem you'll face again. Session Query lets you find them — without grep'ing through raw JSON files.

- **Search by project, agent, date range** — "What was I working on in the infa project last week with Claude?"
- **Deep dive into sessions** — Read summaries, key actions, outcomes
- **Navigate session hierarchy** — See which root sessions spawned sub-agents
- **Incremental extraction** — Pull new sessions and keep the index up to date

## Quick Start

1. **Install the skill** (see above)

2. **Extract your sessions** (first time only):
```bash
# Find the bundled scripts directory
SCRIPTS=$(python3 -c "from scripts.config import resolve_scripts_dir; print(resolve_scripts_dir())")
OUT=$(python3 -c "from scripts.config import resolve_output_dir; print(resolve_output_dir())")

# Extract from all agents
python3 $SCRIPTS/extract_opencode.py --output-dir $OUT
python3 $SCRIPTS/extract_claude_code.py --output-dir $OUT
python3 $SCRIPTS/extract_gemini.py --output-dir $OUT

# Build the search index
python3 $SCRIPTS/index_sessions.py --output-dir $OUT
```

3. **Ask your agent:**
   - "What sessions did I have last week?"
   - "Find Claude sessions in the sessions project from April"
   - "Show me my longest sessions"

## Configuration

Session Query resolves paths in this priority order:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Environment variables | `export SESSION_QUERY_DB=~/work/sessions.db` |
| 2 | Config file | `~/.session-query/config.yaml` |
| 3 | Auto-detect | Looks for `sessions.db` in CWD and parent dirs |
| 4 | Default fallback | `~/.session-query/sessions.db` |

### Environment Variables

```bash
SESSION_QUERY_DB         # Path to sessions.db SQLite database
SESSION_QUERY_OUTPUT_DIR  # Where extracted session files live
SESSION_QUERY_SCRIPTS_DIR # Where extraction tools are
SESSION_QUERY_CONFIG      # Custom config file path
CLAUDE_PROJECT_PATH_PREFIX # Override auto-detected Claude project root
```

### Config File

On first run, create the config:
```bash
python3 -c "from scripts.config import init_config; init_config()"
```

Edit `~/.session-query/config.yaml`:
```yaml
sessions_db: ~/my-projects/sessions/sessions.db
output_dir: ~/my-projects/sessions/sessions/
```

## Supported Agents

Extracts sessions from:

| Agent | Source |
|-------|--------|
| **OpenCode** | `~/.local/share/opencode/opencode.db` |
| **Claude Code** | `~/.claude/projects/` + `~/.claude/transcripts/` |
| **Gemini CLI** | `~/.gemini/tmp/` |

## Schema

The `sessions.db` database contains:

- **sessions** — one row per session file (agent, project, timestamps, message count, hierarchy)
- **file_fingerprints** — file size tracking for incremental updates
- **extraction_log** — history of index rebuilds

See [`references/schema.md`](references/schema.md) for the full schema.

## License

MIT
