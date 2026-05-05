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
- **Auto-build on first run** — Just open any project and ask; the skill builds the database automatically
- **One-command resync** — "resync database" rebuilds everything from scratch

## Quick Start

1. **Install the skill** (see above)

2. **Ask your agent to build the database** (first time, or anytime you need to resync):
   ```
   "resync database"
   "rebuild the session index"
   ```
   The skill will extract from OpenCode, Claude, and Gemini (2-5 minutes for ~1100 sessions).

3. **Query your history:**
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
