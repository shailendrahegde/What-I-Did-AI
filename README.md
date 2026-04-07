# What I Did — AI Impact Report

**A combined GitHub Copilot + Claude Code activity reporter.**

Transforms raw session logs from both tools into a single, polished HTML report that answers the question every developer should be able to answer: *What did I actually ship with AI assistance, and what was it worth?*

---

## Why this tool exists

GitHub Copilot and Claude Code keep separate session logs with no unified view. This tool bridges that gap — merging both data sources, de-duplicating work done across tools on the same project, and producing a report calibrated against peer-reviewed productivity research.

---

## What the report shows

| Section | What it tells you |
|---|---|
| **Headline KPIs** | Human-equivalent hours, active engagement time, speed multiplier, ROI vs. combined subscription cost |
| **Goals accomplished** | Business-outcome titles, not task lists — what exists now that didn't before |
| **Skills mobilised** | Which professional roles your AI usage stood in for (Engineer, Analyst, Designer, etc.) |
| **How I collaborated** | Time-weighted breakdown of how you used AI: building, refining, researching, grunt work |
| **When I worked** | Non-trivial prompts by time of day, split by Copilot vs. Claude, with engagement rate KPIs |
| **ROI** | Human-equivalent hours × market rate vs. $58/mo combined subscription cost |

The report has three tabs: **GitHub Copilot**, **Claude Code**, and an **All** tab with a combined view.

---

## Quick start

**Requirements**

- Python 3.10+
- Active GitHub Copilot sessions at `~/.copilot/session-state/`
- Claude Code sessions at `~/.claude/projects/`
- One of: **Claude Code** CLI, **GitHub CLI** (`gh`), or an Anthropic API key — used for AI analysis (see [Backend detection](#backend-detection))

**Install**

```bash
git clone https://github.com/shailendrahegde/What-I-Did-AI.git
cd What-I-Did-AI
```

Run the subsequent steps inside **Claude Code** or **GitHub Copilot** — no extra packages needed. The tool auto-detects which AI backend is available (see below).

If you prefer to call the Anthropic API directly:

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

**Run**

```bash
python whatidid.py             # last 7 days, both sources
python whatidid.py --30D       # last 30 days
python whatidid.py --today     # today only
python whatidid.py --copilot   # Copilot sessions only
python whatidid.py --claude    # Claude sessions only
python whatidid.py --refresh   # bypass cache, re-analyse from scratch
python whatidid.py --email your@address.com   # send via Outlook
```

The report opens automatically in your browser and is saved to `~/.claude/whatidid_ai/report.html`.

---

## How it works

```
Session logs  →  Harvest  →  Analyse  →  Report
```

**Harvest** (`harvest_copilot.py`, `harvest_claude.py`)  
Reads local session files. Filters out trivial approvals, single-key responses, and injected system context. Extracts user messages, tool invocations, files touched, lines added/removed, and timestamps.

**Analyse** (`analyze.py`)  
Sends a structured transcript to the Anthropic API with a research-calibrated prompt (see `prompts/analysis.txt`). The prompt groups work into business goals, estimates human-equivalent effort using the SPACE framework, and classifies collaboration quality. Results are cached per day so re-runs are instant.

**Report** (`report.py`)  
Aggregates cached analyses across the date range, merges cross-day goals by project, and renders a self-contained HTML file with inline CSS — no external dependencies, no CDN calls.

---

## Effort estimation methodology

Human-equivalent hours are calculated from four signals calibrated against peer-reviewed research (Alaswad et al. 2026, Cambon et al. 2023, Ziegler et al. 2024, Forsgren et al. 2021 SPACE framework):

- **Conversation turns** — substantive prompts only; trivial approvals excluded
- **Tool invocations** — weighted by type (reads, edits, runs)
- **Active engagement time** — gaps under 5 minutes summed; idle time excluded
- **Lines of code** — additive on top of the base estimate

Speed multiplier = human-equivalent hours ÷ active engagement hours.

---

## Privacy

**Your data stays on your machine.**

- Reads only existing local session logs — no agents, no scrapers
- AI analysis uses your own credentials (Anthropic API key, Claude Code CLI, or GitHub token) sent directly to the respective provider
- The generated report is a local HTML file
- No telemetry, no tracking, no cloud uploads

The only network call is the Anthropic API request for session analysis, using credentials you supply.

---

## Backend detection

The tool tries AI backends in this order and uses the first one available:

| Priority | Backend | How it's detected |
|----------|---------|-------------------|
| 1 | **Anthropic API** (direct) | `ANTHROPIC_API_KEY` env var set |
| 2 | **Claude Code CLI** | `claude` binary on PATH |
| 3 | **GitHub Models** | `gh auth token` succeeds |
| 4 | Heuristic fallback | Always available — no AI, rule-based only |

Running inside Claude Code or GitHub Copilot satisfies options 2 or 3 automatically, so no extra setup is needed.

---

## Configuration

`prompts/analysis.txt` — controls how the AI analyses sessions (goal grouping rules, effort estimation steps, output schema).

`prompts/active_time_quality.txt` — controls how active time is classified into collaboration modes (hand-holding detection patterns, intent categories, mode colours).

Both files are plain text and can be edited without touching Python code.

---

## Related

- [microsoft/What-I-Did-Copilot](https://github.com/microsoft/What-I-Did-Copilot) — the Copilot-only version this tool extends
