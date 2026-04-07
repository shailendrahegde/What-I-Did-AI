# Architecture

## Data flow

```
~/.copilot/session-state/<uuid>/events.jsonl        ~/.claude/projects/<encoded-path>/<uuid>.jsonl
~/.copilot/session-state/<uuid>/workspace.yaml
           │                                                      │
           ▼                                                      ▼
    harvest_copilot.py                               harvest_claude.py
  - Scans session directories for target date      - Scans Claude project directories
  - Extracts user messages from user.message       - Extracts user messages and tool calls
    events (filters approvals + injected context)    from JSONL records
  - Captures tool summaries from assistant         - Records tool-execution approvals
    toolRequests[].intentionSummary                  (tool_result records)
  - Reads token/code stats from session.shutdown   - Captures tokens, lines added, git ops
  - Returns: list of session dicts (source=copilot)- Returns: list of session dicts (source=claude)
           │                                                      │
           └──────────────────┬───────────────────────────────────┘
                              ▼
                          analyze.py
  - Builds a structured transcript from sessions (includes SIGNALS block with tool
    invocation breakdown, active minutes, files touched, lines added/removed)
  - Detects best available AI backend (priority order):
      1. ANTHROPIC_API_KEY env var         → Anthropic API (Claude Haiku)
      2. ~/.claude/config.json primaryApiKey → Anthropic API (Claude Haiku)
      3. `claude` CLI on PATH               → claude -p (Claude Code OAuth session)
      4. gh auth token / GH_TOKEN           → GitHub Models API (gpt-4o-mini)
      5. heuristic fallback
  - Returns: goals[] with tasks[], skills, hours, quality_modes, time_buckets
  - Caches result to ~/.claude/whatidid_ai/cache/<source>_YYYY-MM-DD.json
                              │
                              ▼
                          report.py
  - Aggregates cached analyses across the date range
  - Merges cross-day goals by project key (first 2 path components)
  - Generates self-contained HTML with three tabs:
      · GitHub Copilot tab
      · Claude Code tab
      · All tab (combined view with comparative sections)
  - All tab sections: KPI row → ROI → Skills Mobilized →
    How I Collaborated (side-by-side comparison) → When I Worked (split bars)
                              │
                              ▼
                       email_send.py (optional)
  - Writes HTML to temp file
  - PowerShell Outlook COM automation sends it
```

## Session file formats

### GitHub Copilot — events.jsonl

Copilot writes one directory per session at `~/.copilot/session-state/<uuid>/`.

Each line in `events.jsonl` is a JSON object. Relevant event types:

| Type | Content |
|---|---|
| `session.start` | `data.context`: cwd, repository, branch |
| `user.message` | `data.content`: raw user instruction (may include injected `<current_datetime>` tags) |
| `assistant.message` | `data.toolRequests[]`: name, intentionSummary |
| `tool.execution_start` | `data.toolName`, `data.arguments` |
| `tool.execution_complete` | `data.success`, `data.result` |
| `session.shutdown` | `data.totalPremiumRequests`, `data.codeChanges`, `data.modelMetrics` |

`workspace.yaml` provides: `cwd`, `repository`, `branch`, `summary` (Copilot-generated session title).

### Claude Code — project JSONL

Claude writes one JSONL per conversation at `~/.claude/projects/<encoded-path>/<uuid>.jsonl`.

| Record type | Content |
|---|---|
| `user` | `message.content`: string (user text) or list with `text` and `tool_result` items |
| `assistant` | `message.content`: list with `text` and `tool_use` items |
| `system` | Session metadata, context injections |

**Tool approval records:** When the user approves a tool in the Claude Code TUI (pressing Enter/Y), the tool result returns as a `user` record with `tool_result` content and no text. These are counted as trivial approvals in the `trivial_timestamps` list.

## AI backend detection

`analyze.py` selects the AI backend at runtime:

| Priority | Credential | Backend | Model |
|---|---|---|---|
| 1 | `ANTHROPIC_API_KEY` env var | Anthropic API | Claude Haiku |
| 2 | `~/.claude/config.json` `primaryApiKey` | Anthropic API | Claude Haiku |
| 3 | `claude` CLI on PATH | `claude -p` subprocess | Claude (OAuth session) |
| 4 | `gh auth token` / `GH_TOKEN` | GitHub Models API | gpt-4o-mini |
| 5 | None | Heuristic fallback | — |

GitHub Models endpoint: `https://models.inference.ai.azure.com/chat/completions`  
Uses OpenAI-compatible request format. Auth: `Authorization: Bearer <gh_token>`.

## Token cost model

Token data from `session.shutdown.modelMetrics.<model>.usage` (Copilot) or accumulated from assistant records (Claude).

| Token type | Rate |
|---|---|
| Input | $3.00 / 1M |
| Output | $15.00 / 1M |
| Cache read | $0.30 / 1M |
| Cache creation | $3.75 / 1M |

## ROI and leverage metric

```
human_value       = total_human_hours × HOURLY_RATE    ($72/hr blended rate)
combined_seat_cost = $58/mo                             (GitHub Copilot $39 + Claude Max $19)
leverage           = human_value / combined_seat_cost
speed_multiplier   = human_hours / active_hours
```

Example: 87h × $72 = $6,264 human value ÷ $58/mo seats = **108×**

Speed multiplier example: 87h human-equivalent ÷ 14.5h active engagement = **6×**

## Caching

Results are cached per source per day at `~/.claude/whatidid_ai/cache/<source>_YYYY-MM-DD.json`.

Pass `--refresh` to bypass cache and re-analyse from scratch. Cache files include an `analysis_backend` field recording which AI backend produced the analysis.

## Goal deduplication

Cross-day goals from the same project are merged in `_agg()`:

1. **Exact title match** — same title across days → merge into one goal
2. **Project key match** — same first 2 path components → merge, keep highest-hours title

This prevents the same ongoing project from appearing as multiple separate goals across a 30-day view.
