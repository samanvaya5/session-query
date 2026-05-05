# Session Index Database Schema

`sessions.db` is a SQLite database indexing metadata for all extracted AI coding agent sessions.

## Table: `sessions`

One row per session file. Primary key: `(agent, session_id)`.

| Column | Type | Description |
|--------|------|-------------|
| session_id | TEXT | Agent-specific session identifier |
| agent | TEXT | `opencode`, `claude`, or `gemini` |
| project | TEXT | Canonical project name (or `_unresolved/...`) |
| start_time | TEXT | ISO 8601 session start |
| end_time | TEXT | ISO 8601 session end |
| message_count | INTEGER | Number of messages/events in the session |
| parent_session_id | TEXT | Parent session ID for sub-agent sessions (NULL for root) |
| file_path | TEXT | Relative path from output directory |
| file_size | INTEGER | File size in bytes |
| indexed_at | TEXT | When this row was last indexed (ISO 8601) |

## Table: `file_fingerprints`

Tracks file sizes for incremental index updates. Primary key: `(agent, file_path)`.

| Column | Type | Description |
|--------|------|-------------|
| agent | TEXT | Agent identifier |
| file_path | TEXT | Relative path from output directory |
| file_size | INTEGER | File size at last index |

## Table: `extraction_log`

History of index runs. Auto-increment primary key: `run_id`.

| Column | Type | Description |
|--------|------|-------------|
| run_id | INTEGER | Auto-increment ID |
| agent | TEXT | `all` for full rebuilds, or specific agent |
| ran_at | TEXT | When the run happened (ISO 8601) |
| mode | TEXT | `full` or `incremental` |
| watermark_used | TEXT | ISO 8601 date used as `--since` cutoff |
| sessions_found | INTEGER | Total files scanned |
| sessions_new | INTEGER | New sessions indexed |
| sessions_updated | INTEGER | Updated sessions |

## Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_time);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_agent_project ON sessions(agent, project);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);
```
