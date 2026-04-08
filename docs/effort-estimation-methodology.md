# Effort Estimation Methodology

**How this tool estimates the human-equivalent effort of AI-assisted work**

This document describes the research basis, signals, and calibration logic behind
the effort estimates in *What I Did — AI*. Every design decision traces to a
specific research finding. The methodology draws on peer-reviewed research in
software engineering cost estimation, cognitive load theory, and the emerging
field of LLM-assisted productivity measurement.

---

## 1. The Core Question

> If a skilled professional had done this work entirely without AI assistance,
> how many hours would it have taken?

This is the "human-equivalent effort" — the counterfactual cost of the work that
AI accelerated. It is **not** how long the user spent, nor how long the AI
took. It is what a competent expert would bill for delivering the same outcome
by hand.

---

## 2. Research → Design Decisions

### 2.1 "No single metric captures effort" → Two complementary estimation systems

Classic software effort estimation relies on size-oriented metrics — lines of
code (LOC) and function points (FP). However:

- **Lavazza et al. (2024)** analysed hundreds of projects and found that simpler
  proxies performed as well as full function-point analysis — and *all* methods
  underestimated effort on highly complex projects.

- **Hao et al. (2023)** measured actual brain activity (EEG) and eye-tracking of
  developers and found that popular code complexity metrics often *mis-predict*
  how hard code is for humans to understand.

- **Forsgren et al. (2021)** proposed the SPACE framework, arguing that
  productivity requires measuring multiple dimensions: Satisfaction, Performance,
  Activity, Communication, and Efficiency.

**Our response:** We run two estimation systems in parallel, each addressing a
different limitation of the other:

| System | Approach | Strength | Limitation |
|---|---|---|---|
| **Deterministic formula** | `turns_h + lines_h + reads_h` — three additive log terms, no judgment | Transparent, reproducible, shown in report | Cannot see business value or work quality; treats all turns equally |
| **AI semantic estimate** | Reads the full transcript; uses formula as a floor; applies expert billing judgment | Distinguishes boilerplate from design; sees iteration quality and outcome significance | Requires API; less reproducible |

The deterministic formula is the **floor** — what the work was worth at minimum based on
measurable counts. The AI estimate is the **primary output** — it reads what was actually
accomplished and estimates what an expert firm would bill. The AI is explicitly instructed
that its estimate should land at or above the formula floor.


### 2.2 "LLMs provide 1.4–4× speed-ups" → Active time as primary AI anchor

- **Cambon et al. (2023)** synthesised 30+ experiments and found that participants
  with Copilot tools completed tasks in 26–73% of the time (1.4× to 4× faster).

- **Peng et al. (2023)** found that developers using GitHub Copilot completed a
  programming task **55.8% faster** on average (~2.3× speedup).

**Our response:** Active engagement time is the primary real-world anchor for AI
estimation. A professional who was actively engaged for 90 minutes directing AI
produced 2–6 hours of human-equivalent work, depending on the nature of that
engagement:

| Work type | Speedup applied | Rationale |
|---|---|---|
| Mechanical execution (deploy, config, git ops) | 1.4× (lower bound) | Routine; AI provides modest leverage |
| Implementation (feature building, code editing) | 2–3× (midpoint) | Standard productivity gain |
| Design, research, debugging, decision-making | 3–4× (upper bound) | Expert judgment is the scarce resource AI amplifies most |

Active time is used by the **AI estimator** as its primary anchor, not by the
deterministic formula (which uses turns and lines instead, as they are more
directly countable from session data).


### 2.3 "78% of 'complex' tasks done in <25% effort; 22% of 'simple' tasks took >180%" → Task-type classification with caps

- **Alaswad et al. (2026)** documented that human-perceived complexity is a poor
  predictor of AI-assisted effort. Installing a tool seems "complex" but AI
  handles it in seconds. Integrating a one-line change into legacy code seems
  "simple" but may require extensive verification.

**Our response:** The AI prompt classifies tasks by type using tool distribution
(read-heavy = research, edit-heavy = implementation, run-heavy = debugging).
Mechanical tasks (install, deploy, git push) are **always capped at 0.25–0.5h**
regardless of tool count.


### 2.4 "Suggestion counts are misleading — acceptance rate matters" → Reqs capped by turns

- **Ziegler et al. (2024)** found that the **acceptance rate of AI suggestions**
  is a meaningful productivity signal. Raw suggestion counts are misleading —
  high counts with low acceptance mean wasted overhead, not productive work.

**Our response:** Conversation turns replace raw request counts as the primary
interaction signal. Premium requests (Copilot) are capped at 10× conversation
turns to prevent automated completions from inflating estimates.


### 2.5 "Iteration count and prompt efficiency predict true complexity" → AI qualitative judgment

- **Chen et al. (2023)** introduced "prompt efficiency" — measuring how many
  interactions were needed before the AI produced a correct solution — as an
  indicator of task complexity.

- **Alaswad et al. (2026)** identified **iterative reasoning cycles** as one of
  five key dimensions driving effort in LLM-assisted work.

**Our response:** Iteration depth is reflected in two ways depending on the
estimation system:

- **Deterministic formula:** The logarithmic turns scale inherently captures diminishing
  returns. Heavy iteration (many turns on the same files) is already embedded in
  `turns_h` — applying additional percentage multipliers on top degraded accuracy in
  30-day calibration testing (they over-inflated estimates for high-turn sessions).

- **AI semantic estimator:** The AI reads the actual transcript and applies a qualitative
  upward adjustment when it observes significant rework, course-correction, or debugging
  cycles — typically +25–50% on the base estimate for heavily iterative sessions. This
  is judgment-based, not mechanical.


### 2.6 "Broader scope projects have significantly larger effort overruns" → AI qualitative judgment

- **Morcov et al. (2020)** found that projects with more moving parts had
  significantly larger effort overruns.

- **Tregubov et al. (2017)** measured that engineers working across multiple
  contexts spent **17% of their time** recovering from context switches.

**Our response:** Scope breadth informs AI judgment, not the deterministic formula:

- **Deterministic formula:** `files_touched` was tested in 30-day marginal R² analysis
  and added only +0.00–0.03 R² beyond the three-signal base. It is tracked in session
  metrics for display but excluded from the deterministic formula.

- **AI semantic estimator:** When the transcript shows a session touching 10+ files
  across multiple systems, the AI applies a +20–30% upward adjustment for coordination
  and integration overhead — consistent with Morcov and Tregubov findings.


### 2.7 "Not all lines are equal" → Logic lines only; boilerplate excluded

- **Alaswad et al. (2026)** emphasise that an LLM can generate 1,000 lines of
  HTML or JSON in seconds. A human expert writing the same output from scratch
  would spend hours — but they wouldn't: they'd write a 20-line template instead.

- **Empirical finding from this dataset (n=48 days):** total lines added has only
  r=+0.25 correlation with AI effort estimates. When split by file type,
  *logic lines* (`.py`, `.js`, `.ts`, …) reach r=+0.41, while *boilerplate lines*
  (`.html`, `.css`, `.json`, `.md`, …) have r=−0.14 — actively hurting accuracy.

**Our response:** Lines are split at harvest time into two categories — only logic
code is counted in the estimate, because that is what a human expert would actually
hand-author:

| Category | File types | Treatment |
|---|---|---|
| **Logic lines** | `.py` `.js` `.ts` `.go` `.rs` `.java` `.cs` `.sh` and other compiled/scripted code | Counted; contribute to the effort estimate on a logarithmic scale |
| **Boilerplate lines** | `.html` `.css` `.json` `.md` `.yaml` `.csv` and other data/markup/template files | Tracked for display but **excluded from the effort formula** |

AI can generate 1,000 lines of HTML or JSON in seconds — a human expert would
never hand-write those line-for-line; they'd write a 20-line template instead. By
isolating only logic code, the signal reflects the decisions and reasoning that
AI actually accelerated, not the volume of generated output.

Logic lines use a logarithmic scale because the first 100 lines of a new module
require far more design thinking than the next 900 lines of implementation:

| Logic lines written | Contribution to estimate |
|---|---|
| 50 | +0.20h |
| 100 | +0.40h |
| 200 | +0.63h |
| 500 | +1.03h |
| 1 000 | +1.33h |
| 5 000 | +1.86h |

For Copilot sessions (where per-file line counts are unavailable), the logic fraction
is estimated from the proportion of modified files that have logic extensions.


### 2.8 "Turns follow diminishing returns, not linear growth" → Logarithmic turns scale

- **Vaithilingam et al. (2022)** observed that programmers using a code generator
  spent significant time **iteratively probing and correcting the AI**.

- **Santos et al. (2025)** found that while code-writing effort decreased with AI,
  effort on **debugging and validating AI-generated code remained high**.

- **Empirical calibration (this dataset):** OLS regression of conversation turns
  against AI effort estimates yields the relationship `−0.15 + 0.67 × ln(turns + 1)`.
  The previous tiered step-function overestimated by 5–10× on heavy sessions —
  e.g. 108 turns produced a formula estimate of 26h vs the AI's 2.75h. The log
  curve correctly captures diminishing returns: each additional turn in a long
  session adds less incremental human-equivalent effort than the first few turns.

**Our response:** Only **substantive turns** count — trivial confirmations like
"yes", "commit", "1", "2" are filtered out before the formula runs.

| Substantive turns | Formula hours (log scale) | Previous tiered scale |
|---|---|---|
| 5 | 0.92h | 0.75h |
| 15 | 1.57h | 1.5h |
| 30 | 2.02h | 3.0h |
| 60 | 2.50h | 5.0h |
| 100 | 2.82h | 8.0h |


### 2.9 "Investigation and analysis work leaves no code trace" → Read/search tool calls

- **Vaithilingam et al. (2022)** found that a significant portion of AI-assisted
  programming time is spent **probing and exploring** — reading files, searching
  codebases, understanding context — before any code is produced.

- **Forsgren et al. (2021)** SPACE framework includes **Activity** as a distinct
  productivity dimension — encompassing tool invocations and exploratory actions,
  not just code output.

- **Alaswad et al. (2026)** identify **Context completeness** (Dimension 2) as a
  driver of LLM-assisted effort: the work of gathering and validating information
  before and during an AI-assisted task.

- **Empirical finding (this dataset, n=48 days):** marginal R² analysis shows
  `read_calls` (file reads + grep/glob/search tool invocations) adds +0.05 R²
  on top of the turns + lines_logic base — the only signal that held up
  consistently across both log-transformed and raw-hours targets. Intent-based
  signals (e.g. `research_frac`, `building_frac`) were strong at 7 days but
  shrank to noise at 30 days, indicating overfitting. `read_calls` remained
  stable.

**Our response:** File reads and search tool calls are counted separately and
contribute a third logarithmic term. This captures sessions where the work is
investigation, exploration, or analysis rather than code output — sessions that
would otherwise be underestimated by the turns+lines formula alone:

| Read/search tool calls | Contribution to estimate |
|---|---|
| 5 | +0.26h |
| 10 | +0.35h |
| 20 | +0.44h |
| 50 | +0.57h |
| 100 | +0.67h |

---

## 3. The Five-Dimension Framework

Grounded in the **Hybrid Intelligence Effort** framework (Alaswad et al. 2026):

| # | Dimension | Deterministic formula proxy | AI estimator proxy |
|---|---|---|---|
| 1 | LLM reasoning complexity | `turns_h` — log-scaled conversation turns | Turns + transcript quality assessment |
| 2 | Context completeness | `reads_h` — file reads + grep/glob/search calls | Reads + AI's reading of what was investigated |
| 3 | Transformation scope | `lines_h` — logic code only (not HTML/CSS/JSON/MD) | Logic lines + assessment of design decisions made |
| 4 | Iterative reasoning cycles | Embedded in `turns_h` log curve | Qualitative rework/iteration premium (+25–50%) |
| 5 | Human oversight effort | Speed multiplier display only | `active_minutes × 2–4` as primary anchor |

---

## 4. The Two Estimation Systems

### 4A. AI Semantic Estimate (primary output)

The AI reads the full session transcript — every instruction, every tool action,
every file change — and estimates what a skilled expert firm would bill for the
same outcome without AI assistance. It uses the deterministic formula as a
**floor**: its estimate should land at or above what the formula produces, never
below.

**AI estimation logic (in priority order):**

1. **Active time as anchor:** `active_minutes × 2–4` gives the plausible range.
   Work type determines the multiplier — routine execution at 2×, design and
   decision-making at 3–4×.

2. **Turns and transcript depth:** Each substantive turn represents real expert
   thinking. The AI uses the outcome-anchored scale (9–15 turns → 1.5h,
   31–60 → 3–5h, 61–100 → 5–8h) rather than the log formula, because the AI
   can also assess *quality* of turns — a 30-turn session on a hard architecture
   problem is worth more than 30 turns of copy-paste instructions.

3. **Logic lines, excluding boilerplate:** Logic code output contributes ~1h per
   100 lines at expert writing speed (80–130 LoC/hr). HTML/CSS/JSON/MD are excluded.

4. **Read/search calls:** Heavy read sessions (+40 reads) add 0.5–1h for the
   investigation and navigation effort.

5. **Qualitative upward adjustments:**
   - Significant rework or debugging cycles: +25–50%
   - Broad scope (10+ files, multiple systems): +20–30%
   - Architecturally significant decisions with lasting impact: scale toward upper anchors
   - Mechanical execution only: cap at 0.25–0.5h regardless of other signals

6. **Task-type caps:** Mechanical tasks (install, deploy, git push, copy files) are
   always capped at 0.25–0.5h regardless of how many turns or reads they generated.

---

### 4B. Deterministic Formula (transparency floor)

**In plain English:** The formula asks three questions and adds the answers together.

1. *How deep was the collaboration?* Count the substantive back-and-forth turns —
   each one represents the human doing real thinking (framing a problem, reviewing
   output, deciding next steps). The relationship is logarithmic: going from 5 to
   15 turns adds more human-equivalent effort than going from 85 to 95 turns, because
   early turns drive decisions while later turns are refinements.

2. *How much original logic was written?* Count lines added to **logic code files
   only** — `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.sh`, and similar. This
   deliberately excludes HTML, CSS, JSON, Markdown, YAML, and other markup or data
   files. AI generates those cheaply; a human expert would never hand-write them
   line-for-line. Apply a log scale here too: the first hundred lines of a new
   module require design decisions; the tenth hundred are mostly implementation
   following established patterns.

3. *How much investigation and analysis happened?* Count file reads and search/grep/
   glob tool invocations. These capture sessions where the work is exploration and
   understanding rather than code output — research spikes, debugging investigations,
   architecture reviews — which leave no code trace but still required skilled human
   judgment to direct.

```
turns_h  = max(0,  −0.15 + 0.67 × ln(turns + 1))
lines_h  = 0.40 × log₂(lines_logic ÷ 100 + 1)
reads_h  = 0.10 × log₂(read_calls + 1)

total    = turns_h + lines_h + reads_h
total    = max(total, 0.25)          # floor at 15 min
total    = round to nearest 0.25h
```

### Worked examples

**Example A — Implementation session**
> 30 turns, 400 logic lines (.py / .ts), 800 boilerplate lines (.html / .json), 8 file reads

```
turns_h  = −0.15 + 0.67 × ln(31) = −0.15 + 0.67 × 3.43 = 2.15h
lines_h  = 0.40 × log₂(400/100 + 1) = 0.40 × log₂(5) = 0.40 × 2.32 = 0.93h
reads_h  = 0.10 × log₂(9) = 0.10 × 3.17 = 0.32h

           boilerplate lines (800) → excluded from formula

total    = 2.15 + 0.93 + 0.32 = 3.40h → rounded to 3.50h
```

**Example B — Research / investigation session**
> 8 turns, 0 logic lines, 40 file reads and searches

```
turns_h  = −0.15 + 0.67 × ln(9) = −0.15 + 0.67 × 2.20 = 1.32h
lines_h  = 0h  (no logic code written)
reads_h  = 0.10 × log₂(41) = 0.10 × 5.36 = 0.54h

total    = 1.32 + 0 + 0.54 = 1.86h → rounded to 2.00h
```

Without the `reads_h` term, this session would estimate at 1.25h — undercounting
the investigation effort by nearly half.

### Calibration basis

The turns and lines terms were fitted by OLS regression against 38 days of AI-analysed
sessions (50 matched goal-level records). The `reads_h` term was added after 30-day
marginal R² analysis (48 records) showed `read_calls` was the only signal that
consistently improved R² (+0.05) beyond the two-term base formula. The best
achievable R² with countable signals is ~0.40 per goal and ~0.55 per day — the
remaining ~0.45–0.60 of variance is explained by the AI's semantic judgment about
business value, work quality, and context, which raw counts cannot capture. This is
why the AI estimate is the primary output and the formula is the floor.

---

## 5. Caps and Floors

| Rule | Rationale |
|---|---|
| Floor at 0.25h | Every session with substantive turns represents at least 15 min of human thinking |
| Boilerplate lines (HTML/CSS/JSON/MD/YAML) → 0 contribution | AI generates these cheaply; they do not represent hand-authored expert effort |
| Logic lines on log scale | Design decisions in the first 100 lines outweigh implementation in the next 900 |
| Read calls on log scale | Early searches orient the whole session; later searches are narrower lookups |

---

## 6. Speed Multiplier

```
speed_multiplier = human_equivalent_hours / active_engagement_hours
```

Active engagement time sums only gaps < 5 minutes between messages, excluding
idle periods. This gives a realistic measure of focused collaboration time,
not wall-clock duration.

A speed multiplier of 6× means that for every hour you actively engaged with AI,
you produced what would have taken 6 hours without it.

---

## 7. How I Collaborated — Methodology

Each user message is classified into one of six collaboration modes using
time-weighted active engagement minutes:

| Mode | Signal |
|---|---|
| **Course-correcting** | User correction phrases or error signals in tool output |
| **Designing** | Design, planning, architecture intent keywords |
| **Researching** | Exploratory or investigative intent keywords |
| **Building** | Creation and implementation keywords |
| **Refining** | Iteration and improvement keywords |
| **Delegating** | Shipping, configuring, routine task keywords |

Two detection layers run in priority order:
1. **Course-correcting** — user dissatisfaction phrases ("no", "wrong", "that's not right") or tool error signals ("Error", "SyntaxError", "exit code 1")
2. **Mode classification** — intent patterns matched against the message content (config: `prompts/active_time_quality.txt`)

Trivial turns (approvals, single-digit selections, "yes"/"ok") are excluded from
the classification — they contribute to the "Include trivial" toggle view but
not to the active collaboration breakdown.

---

## 8. Validation and Limitations

### Known limitations
- **No ground truth.** We lack actual time-tracking data for unassisted work.
- **Tool approval noise (Claude Code).** Every tool execution requires explicit
  user approval, generating `tool_result` records. These are counted as trivial
  approvals and excluded from substantive turn counts.
- **Copilot autonomy difference.** GitHub Copilot runs tools without per-step
  approval. This means Claude sessions have systematically more trivial records,
  which the "Include trivial" toggle makes visible.
- **Non-coding work is harder to estimate.** The signal set is strongest for
  software engineering tasks.

---

## 9. References

1. Alaswad, M., et al. (2026). "Toward LLM-Aware Software Effort Estimation." *Frontiers in AI.*
2. Cambon, J., et al. (2023). "Early LLM-based Tools for Enterprise Information Workers." Microsoft Research.
3. Chen, O., Paas, F., & Sweller, J. (2023). "A Cognitive Load Theory Approach to Defining and Measuring Task Complexity." *Educational Psychology Review.*
4. Forsgren, N., et al. (2021). "The SPACE of Developer Productivity." *CACM*, 64(1).
5. Hao, Z., et al. (2023). "Towards Understanding the Measurement of Code Complexity." *Frontiers in Neuroscience.*
6. Lavazza, L., Morasca, S., & Tosi, D. (2024). "On the Role of Functional Complexity in Software Effort Estimation." *IST.*
7. Morcov, S., Pintelon, L., & Kusters, R. (2020). "Definitions, Characteristics and Measures of IT Project Complexity." *IJITPM.*
8. Peng, S., et al. (2023). "The Impact of AI on Developer Productivity: Evidence from GitHub Copilot." *arXiv:2302.06590.*
9. Santos, N., et al. (2025). "The Impact of AI Code Assistants on Developer Workload." *IEEE Software.*
10. Tregubov, A., et al. (2017). "Impact of Task Switching and Work Interruptions on Software Development Processes." *ICSSP '17.*
11. Vaithilingam, P., Zhang, T., & Glassman, E. L. (2022). "Expectation vs. Experience: Evaluating the Usability of Code Generation Tools." *CHI EA '22.*
12. Ziegler, A., et al. (2024). "Measuring GitHub Copilot's Impact on Productivity." *CACM*, 67(3).

---

*This methodology is open source and evolving. Contributions and calibration data welcome at
[github.com/shailendrahegde/What-I-Did-AI](https://github.com/shailendrahegde/What-I-Did-AI).*
