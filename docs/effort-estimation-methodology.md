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

### 2.1 "No single metric captures effort" → Multi-signal max() formula

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

**Our response:** We take `max(tools, turns, active)` — each signal measures the
same work from a different angle, and the strongest signal wins as the base. Lines
of code are additive because coding output is independent work beyond research and
iteration.


### 2.2 "LLMs provide 1.4–4× speed-ups" → Active time × 4 multiplier

- **Cambon et al. (2023)** synthesised 30+ experiments and found that participants
  with Copilot tools completed tasks in 26–73% of the time (1.4× to 4× faster).

- **Peng et al. (2023)** found that developers using GitHub Copilot completed a
  programming task **55.8% faster** on average.

**Our response:** `active_minutes × 4 / 60` converts active engagement time to
human-equivalent hours. The 4× multiplier reflects the upper bound of observed
speed-ups, capturing the full productivity gain that AI provides.


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


### 2.5 "Iteration count and prompt efficiency predict true complexity" → Iteration depth multiplier

- **Chen et al. (2023)** introduced "prompt efficiency" — measuring how many
  interactions were needed before the AI produced a correct solution — as an
  indicator of task complexity.

- **Alaswad et al. (2026)** identified **iterative reasoning cycles** as one of
  five key dimensions driving effort in LLM-assisted work.

**Our response:** `iteration_depth` (average edits per file) and
`conversation_turns` both contribute complexity multipliers:

| Signal | Threshold | Multiplier |
|--------|-----------|------------|
| Conversation turns > 15 | Moderate iteration | +15% |
| Conversation turns > 40 | Heavy iteration | +35% cumulative |
| Iteration depth > 5 edits/file | Debugging/refinement | +15% |
| Iteration depth > 12 edits/file | Extensive rework | +35% cumulative |


### 2.6 "Broader scope projects have significantly larger effort overruns" → Files-touched multiplier

- **Morcov et al. (2020)** found that projects with more moving parts had
  significantly larger effort overruns.

- **Tregubov et al. (2017)** measured that engineers working across multiple
  contexts spent **17% of their time** recovering from context switches.

**Our response:** `files_touched_count` adjusts the estimate upward:

| Files touched | Multiplier |
|---|---|
| ≤ 3 | 1.0× |
| 4–10 | 1.1× |
| 11+ | 1.3× |


### 2.7 "Code volume is decoupled from effort in AI-assisted work" → Lines as additive, not primary

- **Alaswad et al. (2026)** emphasise that an LLM can generate 1,000 lines of
  boilerplate in seconds. But an expert human writing 500 lines of production
  code needs 4+ hours.

**Our response:** Lines are additive on top of the base estimate (not part of
the `max()`):

| Lines added | Formula hours |
|---|---|
| 1–150 | 0.75h |
| 151–300 | 1.5h |
| 301–500 | 2.5h |
| 500+ | 4h+ |


### 2.8 "New effort emerges in managing the AI" → Conversation turns as primary interaction signal

- **Vaithilingam et al. (2022)** observed that programmers using a code generator
  spent significant time **iteratively probing and correcting the AI**.

- **Santos et al. (2025)** found that while code-writing effort decreased with AI,
  effort on **debugging and validating AI-generated code remained high**.

**Our response:** Only **substantive turns** count — trivial confirmations like
"yes", "commit", "1", "2" are filtered out. Each substantive turn ≈ 5–7 min
of human thinking:

| Substantive Turns | Formula hours |
|---|---|
| 1–3 | 0.25h |
| 4–8 | 0.75h |
| 9–15 | 1.5h |
| 16–30 | 3h |
| 31–60 | 5h |
| 61–100 | 8h |
| 100+ | 10h |

---

## 3. The Five-Dimension Framework

Grounded in the **Hybrid Intelligence Effort** framework (Alaswad et al. 2026):

| # | Dimension | Our proxy |
|---|---|---|
| 1 | LLM reasoning complexity | `conversation_turns`, conversation depth |
| 2 | Context completeness | File reads, searches (from tool distribution) |
| 3 | Transformation scope | `files_touched`, `lines_added`, `lines_removed` |
| 4 | Iterative reasoning cycles | `conversation_turns`, `iteration_depth` |
| 5 | Human oversight effort | `active_minutes` relative to wall-clock time |

---

## 4. The Complete Formula

```
Step 1 — Primary signals (take the strongest):
    tool_h   = (reads × 0.3min + edits × 1.5min + runs × 0.75min) ÷ 60
    turns_h  = tier_turns(substantive_turns)
    active_h = active_minutes × 4 ÷ 60
    base     = max(tool_h, turns_h, active_h)

Step 2 — Complexity multipliers (capped at 2.2× combined):
    + 0.15 if turns > 15        + 0.20 if turns > 40
    + 0.15 if iter_depth > 5    + 0.20 if iter_depth > 12
    + 0.10 if files > 3         + 0.20 if files > 10

Step 3 — Lines of code (additive):
    lines_h = tier_lines(lines_added)

Step 4 — Total:
    total = (base × combined_multiplier) + lines_h
    total = max(total, 0.25)
    total = round to nearest 0.25h
```

### Worked example

> 150 tools (60 reads / 40 edits / 30 runs), 25 turns, 45m active, +320 lines, 6 files

```
tool_h   = (60×0.3 + 40×1.5 + 30×0.75) ÷ 60 = 100.5min ÷ 60 = 1.7h
turns_h  = 3h  (25 turns → 16–30 tier)
active_h = 45 × 4 ÷ 60 = 3h
base     = max(1.7, 3, 3) = 3h

Multipliers: turns 25>15 (+15%), depth 8>5 (+15%), files 6>3 (+10%)
Combined: 1.15 × 1.15 × 1.10 = 1.45×

lines_h  = 1.5h  (320 lines)

Total = (3h × 1.45) + 1.5h = 4.35 + 1.5 = 5.75h → rounded to 5.75h
```

---

## 5. Caps and Floors

| Rule | Rationale |
|---|---|
| Mechanical tasks → 0.25–0.5h max | Execution, not thinking |
| <5 tool invocations AND <10 total tools → cap at 1h | Inherently lightweight session |
| No single task exceeds 8h | Split into sub-tasks if larger |
| Combined multiplier capped at 2.2× | Prevents compounding on large aggregated goals |

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
