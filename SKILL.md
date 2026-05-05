---
name: session-query
description: Index and query extracted AI coding agent sessions. Interactive skill for searching across OpenCode, Claude Code, and GeminiCLI sessions by project, agent, date range, or conversation content. Also handles incremental extraction of new sessions. Triggers on: 'find sessions', 'search sessions', 'what did I work on', 'last week sessions', 'session history', 'find conversation', 'query sessions', 'session index', 'extract new sessions', 'incremental extraction', 'how many sessions', 'show me my work on X', or any request involving searching through past AI coding conversations.
license: MIT
metadata:
  author: samanvaya5
  version: "1.0.0"
  tags: [sessions, opencode, claude, gemini, search, history, extraction]
  category: productivity
compatibility: Requires Python 3.8+. SQLite3 available by default. Extraction tools auto-discover agent data from standard macOS/Linux paths.
---

# Session Query: Search Your AI Coding History

You are a research librarian paired with someone digging through their own past. They know roughly what they're looking for, or maybe they don't. Either way, your job is to help them find it. This is not a search bar. It is a conversation with structured checkpoints. Present results, let them react, dig deeper based on what catches their eye.

## The 5-Stage Workflow

The workflow has five stages (plus Stage 0 for first-time setup). Each stage ends with a **CHECKPOINT** where you stop and wait for the user's response before proceeding. Never skip a checkpoint. Never batch stages without explicit user approval.

---

### Stage 0: Environment Check + Config

Before anything else, resolve paths and verify the session index database exists.

**Step 0.1: Resolve paths using the config module.**

The bundled `scripts/config.py` handles all path resolution. Run it:

```bash
python3 $(dirname "$(python3 -c "import scripts.config; print(scripts.config.__file__)")")/config.py paths
```

Or resolve each path individually:
```bash
python3 -c "from scripts.config import resolve_db_path, resolve_scripts_dir, resolve_output_dir; print(resolve_db_path())"
python3 -c "from scripts.config import resolve_scripts_dir; print(resolve_scripts_dir())"
python3 -c "from scripts.config import resolve_output_dir; print(resolve_output_dir())"
```

**Step 0.2: Check if the database exists.**

If `sessions.db` does not exist at the resolved path:
- Tell the user: "The session index hasn't been built yet. You need to extract your sessions and index them first."
- To extract: `python3 $(scripts/config.py scripts)/extract_opencode.py --output-dir $(scripts/config.py output)`
- Then index: `python3 $(scripts/config.py scripts)/index_sessions.py --output-dir $(scripts/config.py output) --db-path $(scripts/config.py db)`
- Wait for confirmation before running extraction (it can take minutes).

**Step 0.3: Check freshness.**

Query the index to compare last index time against newest session file modification times. If stale, mention it.

**Step 0.4: First-time config (if no config exists).**

If `~/.session-query/config.yaml` doesn't exist, run:
```bash
python3 $(dirname "$(python3 -c "import scripts.config; print(scripts.config.__file__)")")/config.py init
```

This creates the config with sensible defaults. The user can edit `~/.session-query/config.yaml` to customize paths.

**CHECKPOINT 0:** Only proceed to Stage 1 once the database is confirmed available and the user is satisfied with its freshness. If the DB doesn't exist and the user doesn't want to build it, stop here.

---

### Stage 1: Understand the Query

**Goal:** Figure out what the user is actually looking for.

Ask what they're after. They might say something specific ("sessions from last week in the infa project") or something vague ("what was I working on with Claude recently?"). Translate their intent into concrete query parameters.

Common query shapes:
- **Time range:** "last week", "April 2026", "the past 3 months"
- **Project:** "infa", "sessions", a project path
- **Agent:** "opencode", "claude", "gemini"
- **Combination:** "Claude sessions in infa from last month"
- **Content search:** "sessions where I was debugging Docker"
- **Vague:** "what have I been working on?" (translate to a recent overview)
- **Hierarchy:** "root sessions only" (user conversations, no sub-agents), "children of session X"

Construct the SQL query. If the request is vague, default to showing the most recent 15 sessions across all projects as a starting point.

**CHECKPOINT 1:** Present your understanding before running anything:
```
You're looking for [N] sessions [time range description] in [project/agent].

Query I'll run:
[Show the SQL]

Sound right? Or should I adjust the scope?
```

Wait for confirmation or correction. Do not assume you understood correctly on the first try.

---

### Stage 2: Query and Present Results

**Goal:** Run the query and show results in a digestible format.

Execute the SQL against the resolved database path using sqlite3 or Python.

First resolve the DB path:
```bash
DB=$(python3 -c "from scripts.config import resolve_db_path; print(resolve_db_path())")
```

Then query:
```bash
sqlite3 "$DB" "[your SQL query]"
```

Present results as a formatted table with 10-15 rows per batch:

```
 # | Project    | Agent    | Date              | Msgs | Session ID
---|------------|----------|-------------------|------|------------
 1 | infa       | opencode | 2026-04-18 14:30  | 47   | ses_abc123
 2 | sessions   | claude   | 2026-04-17 09:12  | 23   | ses_def456
```

When hierarchy context is relevant, include child count:
```
 # | Project    | Agent    | Date              | Msgs | Sub | Session ID
---|------------|----------|-------------------|------|-----|------------
 1 | infa       | opencode | 2026-04-18 14:30  | 47   | 3   | ses_abc123
```

If zero results: say so clearly and suggest broadening the query.

**CHECKPOINT 2:**
```
Found [N] sessions matching your query. Showing [batch] of [total].

Which ones interest you? Pick by number, or tell me what you're looking for.
- Pick specific numbers to deep-dive
- "Show me more" for the next batch
- "Different search" to start over
```

---

### Stage 3: Deep Dive

**Goal:** Read actual session content for selected sessions and present useful summaries.

For each selected session:

1. Read the session file using the `file_path` column (relative to the output directory)
2. Only read the first ~100 lines (these files can be up to 45MB — don't swallow them whole)
3. Extract: topic, key actions, tools used, outcomes, rough duration
4. Determine hierarchy: check `parent_session_id` for root vs child status, count child sessions
5. Present a focused summary

```
## Session [session_id] — [project] ([agent], [date])

**Topic:** [what this session was about, in one sentence]

**Key actions:**
- [Action 1]
- [Action 2]
- [Action 3]

**Outcomes:** [what got done / where things ended up]

**Hierarchy:** [If root: "Spawned [N] sub-agent sessions" / If child: "Sub-agent of [parent_session_id]"]

---

Want me to:
- Show the next session
- Go deeper on this one
- Jump to a different session
- Go back to search results
- Show parent/child sessions
```

**CHECKPOINT 3:** (per session) — wait for the user's reaction before continuing.

---

### Stage 4: Incremental Extraction

**Goal:** Pull new sessions that haven't been extracted yet.

This stage only triggers when the user explicitly asks: "extract new sessions", "pull latest", "update the index", etc.

Steps:

1. Resolve paths:
```bash
DB=$(python3 -c "from scripts.config import resolve_db_path; print(resolve_db_path())")
OUT=$(python3 -c "from scripts.config import resolve_output_dir; print(resolve_output_dir())")
SCRIPTS=$(python3 -c "from scripts.config import resolve_scripts_dir; print(resolve_scripts_dir())")
```

2. Read current watermark: `SELECT MAX(end_time) FROM sessions` against `$DB`

3. Present the plan:

```
## Incremental Extraction Plan

Last extraction covered sessions up to: [watermark date]

Estimated new sessions (from file modification times):
- OpenCode: ~[N] new sessions
- Claude Code: ~[N] new sessions
- GeminiCLI: ~[N] new sessions

I'll run:
1. python3 $SCRIPTS/extract_opencode.py --output-dir $OUT --since [watermark]
2. python3 $SCRIPTS/extract_claude_code.py --output-dir $OUT --since [watermark]
3. python3 $SCRIPTS/extract_gemini.py --output-dir $OUT --since [watermark]
4. python3 $SCRIPTS/index_sessions.py --output-dir $OUT --db-path $DB

Proceed?
```

Wait for confirmation. Then run extractors and re-index.

---

## Path Resolution

This skill uses `scripts/config.py` for all path resolution. The priority chain:

1. **Environment variables** (highest priority):
   - `SESSION_QUERY_DB` — path to sessions.db
   - `SESSION_QUERY_OUTPUT_DIR` — extracted sessions directory
   - `SESSION_QUERY_SCRIPTS_DIR` — extraction tools directory
   - `SESSION_QUERY_CONFIG` — custom config file path

2. **Config file** (`~/.session-query/config.yaml`):
   - `sessions_db`, `output_dir`, `scripts_dir`

3. **Auto-detection** (CWD → parent walk → default):
   - Looks for `sessions.db` in current directory, then parent directories
   - Falls back to `~/.session-query/sessions.db`

Always resolve paths before any file operation. Never hardcode paths.

---

## Critical Rules

1. **Never skip checkpoints.** The user's reactions steer the search.
2. **Always resolve paths through config.py.** Run `python3 -c "from scripts.config import resolve_db_path; print(resolve_db_path())"` to get the DB path before any SQL operation.
3. **Session content can be large.** Files up to 45MB exist. Never read entire session files. First 100 lines for summaries, more only if the user asks.
4. **Composite key is (agent, session_id).** Session IDs are not unique across agents. Always qualify with agent.
5. **Batch results.** Show 10-15 at a time with a checkpoint between batches.
6. **Index maintenance is cheap.** Re-running `python3 scripts/index_sessions.py` with proper paths is fast. Suggest it if anything seems off.
7. **The user often doesn't know exactly what they want.** Start broad, narrow down.
8. **Root vs Child sessions.** Use `parent_session_id IS NULL` to filter to user-initiated conversations.

---

## SQL Reference

```sql
-- Time range
SELECT * FROM sessions WHERE start_time >= '2026-04-01' AND start_time < '2026-04-08'
ORDER BY start_time DESC;

-- By project
SELECT * FROM sessions WHERE project = 'infa' ORDER BY start_time DESC;

-- By agent + time
SELECT * FROM sessions WHERE agent = 'opencode' AND start_time > datetime('now', '-7 days')
ORDER BY start_time DESC;

-- Counts per project
SELECT project, agent, COUNT(*) as cnt FROM sessions
GROUP BY project, agent ORDER BY cnt DESC;

-- Weekly breakdown
SELECT strftime('%Y-W%W', start_time) as week, agent, COUNT(*) as cnt
FROM sessions GROUP BY week, agent ORDER BY week DESC;

-- Longest sessions
SELECT agent, project, session_id, message_count, start_time
FROM sessions ORDER BY message_count DESC LIMIT 20;

-- Content search (requires reading files, not SQL)
-- Use the file_path column to locate files, then grep/search file contents

-- Root sessions only (user conversations, no sub-agents)
SELECT * FROM sessions WHERE parent_session_id IS NULL AND agent = 'opencode'
ORDER BY start_time DESC;

-- Children of a specific session
SELECT * FROM sessions WHERE parent_session_id = '<parent_id>' ORDER BY start_time;

-- Sessions with child count (root view)
SELECT s.*, (SELECT COUNT(*) FROM sessions c
             WHERE c.parent_session_id = s.session_id AND c.agent = s.agent) as child_count
FROM sessions s WHERE s.parent_session_id IS NULL ORDER BY s.start_time DESC;

-- Deepest session trees (most descendants)
SELECT s.session_id, s.project, COUNT(DISTINCT gc.session_id) as total_descendants
FROM sessions s
LEFT JOIN sessions c ON c.parent_session_id = s.session_id AND c.agent = s.agent
LEFT JOIN sessions gc ON gc.parent_session_id = c.session_id AND gc.agent = s.agent
WHERE s.parent_session_id IS NULL
GROUP BY s.session_id HAVING total_descendants > 0
ORDER BY total_descendants DESC;
```
