"""
analyze.py — Semantic analysis of AI session activity using Claude API.
Works with sessions from both GitHub Copilot and Claude Code.
"""
from __future__ import annotations
import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

_ANTHROPIC_API_URL  = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_MODEL    = "claude-haiku-4-5-20251001"
_GH_MODELS_API_URL  = "https://models.inference.ai.azure.com/chat/completions"
_GH_MODELS_MODEL    = "gpt-4o-mini"

# Keep legacy names for any external references
API_URL = _ANTHROPIC_API_URL
MODEL   = _ANTHROPIC_MODEL

DOMAIN_SKILLS = (
    "System Architecture", "Product Planning", "Requirements Analysis",
    "Technical Research", "Data Analysis", "Statistical Modelling",
    "UX Design", "Product Management", "Project Management",
    "Technical Writing", "Documentation", "Stakeholder Communication",
    "Prompt Engineering", "Security Review", "Code Review",
)
TECH_SKILLS = (
    "Python", "JavaScript", "TypeScript", "Bash/Shell",
    "HTML/CSS", "SQL", "API Integration", "DevOps/CI-CD",
    "Cloud Infrastructure", "Database Design", "Machine Learning",
    "Data Engineering", "Debugging", "Refactoring", "Frontend Development",
)


def _load_taxonomy() -> tuple:
    path = Path(__file__).parent / "prompts" / "skills_taxonomy.txt"
    if not path.exists():
        return DOMAIN_SKILLS, TECH_SKILLS
    domain, tech = [], []
    section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[domain_skills]":
            section = "domain"
        elif line == "[tech_skills]":
            section = "tech"
        elif section == "domain":
            domain.append(line)
        elif section == "tech":
            tech.append(line)
    return tuple(domain) or DOMAIN_SKILLS, tuple(tech) or TECH_SKILLS


def load_intent_categories() -> list[tuple[str, re.Pattern]]:
    """Load intent classification patterns from prompts/intent_classification.txt.

    Returns a list of (intent_name, compiled_regex) tuples in priority order.
    Falls back to a minimal hardcoded set if the file is missing.
    """
    path = Path(__file__).parent / "prompts" / "intent_classification.txt"
    if not path.exists():
        return []   # callers should handle empty list with their own fallback

    categories = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        # Split on first two pipes only — pattern may contain | for regex alternation
        first, _, rest = line.partition("|")
        if not rest:
            continue
        _color, _, pattern = rest.partition("|")
        intent = first.strip()
        pattern = pattern.strip()
        if intent and pattern:
            try:
                categories.append((intent, re.compile(pattern, re.I)))
            except re.error:
                pass  # skip malformed patterns silently
    return categories


def load_role_classification() -> dict:
    """Load role and task-type heuristics from prompts/role_classification.txt.

    Returns a dict with:
      'roles':      list of (role_name, keywords_list) in match-priority order
      'task_types': dict mapping task_type_name → keywords_list
      'intent_to_task_type': dict mapping intent_name → task_type_name
    """
    path = Path(__file__).parent / "prompts" / "role_classification.txt"
    result = {"roles": [], "task_types": {}, "intent_to_task_type": {}}
    if not path.exists():
        return result

    section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[professional_roles]":
            section = "roles"
            continue
        if line == "[task_types]":
            section = "task_types"
            continue
        if line.startswith("["):
            section = None
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue

        name    = parts[0]
        keywords = [k.strip().lower() for k in parts[1].split(",") if k.strip()]

        if section == "roles":
            result["roles"].append((name, keywords))
        elif section == "task_types":
            result["task_types"][name] = keywords

    # Build intent → task_type lookup from task_type keywords
    # (used by fallback analysis to map message intent → task type)
    _INTENT_TASK_MAP = {
        "Building":      "Development",
        "Iterating":     "Development",
        "Investigating": "Bug Fix & Debug",
        "Testing":       "Bug Fix & Debug",
        "Designing":     "Design & UX",
        "Researching":   "Analysis & Research",
        "Planning":      "Analysis & Research",
        "Shipping":      "Execution & Ops",
        "Configuring":   "Execution & Ops",
        "Navigating":    "Analysis & Research",
    }
    result["intent_to_task_type"] = _INTENT_TASK_MAP
    return result


_DOMAIN_SKILLS, _TECH_SKILLS = _load_taxonomy()


# ── Backend detection ─────────────────────────────────────────────────────────
# Priority:
#   1. ANTHROPIC_API_KEY env var           → Anthropic API (direct)
#   2. ~/.claude/config.json primaryApiKey  → Anthropic API (direct)
#   3. `claude` CLI on PATH                 → claude -p  (Claude Code OAuth session)
#   4. `gh auth token`                      → GitHub Models API (Copilot context)
#   5. heuristic fallback

def _get_anthropic_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    config = Path.home() / ".claude" / "config.json"
    if config.exists():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            return data.get("primaryApiKey", "").strip()
        except Exception:
            pass
    return ""


def _claude_cli_available() -> bool:
    """Return True if the `claude` CLI is on PATH."""
    import subprocess
    try:
        subprocess.run(["claude", "--version"], capture_output=True, timeout=10, check=True)
        return True
    except Exception:
        return False


def _claude_cli_analyze(prompt: str) -> str:
    """Run `claude -p <prompt>` and return the raw text output."""
    import subprocess
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise subprocess.SubprocessError(
            f"claude -p exited {result.returncode}: {result.stderr[:200]}"
        )
    return result.stdout.strip()


def _get_gh_token() -> str:
    """Return a GitHub token from `gh auth token`, GH_TOKEN, or GITHUB_TOKEN env vars."""
    import subprocess
    for env_var in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_COPILOT_TOKEN"):
        token = os.environ.get(env_var, "").strip()
        if token:
            return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=10,
        )
        token = result.stdout.strip()
        if token and result.returncode == 0:
            return token
    except Exception:
        pass
    return ""


def _gh_models_analyze(prompt: str, gh_token: str) -> str:
    """Call GitHub Models API (OpenAI-compatible) and return raw text response."""
    payload = json.dumps({
        "model": _GH_MODELS_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 3000,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        _GH_MODELS_API_URL, data=payload,
        headers={
            "Authorization": f"Bearer {gh_token}",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        response = json.loads(resp.read().decode("utf-8"))
    return response["choices"][0]["message"]["content"].strip()


def _detect_backend() -> tuple[str, str]:
    """
    Detect the best available AI backend.
    Returns (backend, credential) where backend is one of:
      'anthropic'  — direct Anthropic API call
      'claude_cli' — delegate to `claude -p` (Claude Code OAuth session)
      'gh_models'  — GitHub Models API via gh token (Copilot context)
      'none'       — no AI backend available
    """
    key = _get_anthropic_key()
    if key:
        return "anthropic", key

    if _claude_cli_available():
        return "claude_cli", ""

    gh_token = _get_gh_token()
    if gh_token:
        return "gh_models", gh_token

    return "none", ""


def check_api_health() -> tuple[str, str]:
    """Returns (status, message). status: 'ok', 'auth', or 'down'."""
    backend, cred = _detect_backend()

    if backend == "anthropic":
        payload = json.dumps({
            "model": _ANTHROPIC_MODEL,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "ping"}],
        }).encode("utf-8")
        req = urllib.request.Request(
            _ANTHROPIC_API_URL, data=payload,
            headers={
                "x-api-key": cred,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15):
                return "ok", "Anthropic API"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return "auth", f"Anthropic key rejected (HTTP {e.code})."
            return "down", f"Anthropic API returned HTTP {e.code}."
        except Exception as e:
            return "down", f"Anthropic API unreachable: {e}"

    if backend == "claude_cli":
        return "ok", "claude -p  (Claude Code session)"

    if backend == "gh_models":
        return "ok", f"GitHub Models API ({_GH_MODELS_MODEL})"

    return "auth", (
        "No AI backend found. Provide one of:\n"
        "  • ANTHROPIC_API_KEY env var\n"
        "  • Claude Code installed (claude CLI on PATH)\n"
        "  • GitHub CLI authenticated (gh auth login)"
    )


def _build_transcript(sessions: list) -> tuple[str, int]:
    """Build a compact transcript from a list of sessions. Returns (text, total_tool_calls)."""
    lines = []
    grand_total_tools = 0
    for s in sessions:
        source = s.get("source", "unknown").upper()
        proj   = s.get("project", "unknown")
        lines.append(f"\n=== {source} | PROJECT: {proj} | SESSION: {s['session_id'][:8]} ===")

        if s.get("session_start") and s.get("session_end"):
            lines.append(f"Time: {s['session_start'][11:19]} → {s['session_end'][11:19]} UTC")

        user_msgs = [m for m in s.get("messages", []) if m.get("role") == "user"]
        lines_add = s.get("lines_added", 0)
        files     = s.get("files_touched", [])
        active    = round(compute_active_minutes(s.get("messages", [])))

        # Categorize tool invocations for SIGNALS block
        reads = edits = runs = other = 0
        for m in user_msgs:
            for t in m.get("tools_after", []):
                tl = t.lower()
                if tl.startswith(("read", "grep", "glob", "search")):
                    reads += 1
                elif tl.startswith(("edit", "write", "create", "notebook")):
                    edits += 1
                elif tl.startswith(("bash", "run", "execute", "terminal")):
                    runs += 1
                else:
                    other += 1

        n_tools = reads + edits + runs + other
        grand_total_tools += n_tools

        sig = (f"SIGNALS: {len(user_msgs)} turns, {n_tools} tools "
               f"(reads={reads}, edits={edits}, runs={runs}, other={other}), "
               f"active={active}min, files={len(files)}")
        if lines_add:
            sig += f", +{lines_add} lines"
        lines.append(sig)

        for msg in user_msgs[:6]:
            lines.append(f"\n[INSTRUCTION] {msg['text'][:150]}")
            for t in msg.get("tools_after", [])[:3]:
                lines.append(f"  • {t}")

    return "\n".join(lines), grand_total_tools


# ── Quality mode classification (matches reference repos) ─────────────────────

# Multi-intent patterns — ALL matching intents returned (not first-match-wins)
_QUALITY_INTENT_PATTERNS: dict = {
    "Building":      re.compile(r"\b(create|add|generate|implement|write|make|build|produce|include|set up|initialize|scaffold|install|open it|rerun|run)\b", re.I),
    "Investigating": re.compile(r"\b(examine|why does|why is|what.s going on|debug|diagnose|analyze what|look at this|can you examine|what.s wrong|trace|root cause|broken|fails|failing|error|identical.+different)\b", re.I),
    "Designing":     re.compile(r"\b(redesign|prominent|visual|layout|style|look like|look more|distinction|spacing|story|compelling|section|appearance|prototype|mockup|wireframe|branding|banner|instead of|rather than|pivot|rethink|different approach|go with|how about)\b", re.I),
    "Researching":   re.compile(r"\b(what.s the|how does|how do|are there|can i do|do they|what can|what would|how come|cost|limit|explain|compare|difference|option)\b", re.I),
    "Iterating":     re.compile(r"\b(adjust|simplify|change|update|modify|better|improve|also like|refine|tweak|move this|swap|resize|reorder|reduce|remove the|make it|make the|make this|a bit|a little|slightly|smaller|larger|bigger|shorter|longer|cleaner|replace|rename|shorten|widen|a tad|less|more like|also add|also update|also change|also include|also remove|should be|it should|should have)\b", re.I),
    "Shipping":      re.compile(r"\b(commit|push|pr\b|pull request|merge|deploy|ship|tag|release|check.?in)\b", re.I),
    "Planning":      re.compile(r"\b(plan|propose|approach|strategy|stages|phases|priority|before that|options|go ahead|wait for)\b", re.I),
    "Testing":       re.compile(r"\b(test|verify|validate|check if|smoke|does it work|try it|confirm)\b", re.I),
    "Configuring":   re.compile(r"\b(config|setup|auth|login|permission|access|credential|settings|env|alias|profile)\b", re.I),
    "Navigating":    re.compile(r"\b(find|search|where is|show me|list|fetch|locate|get the latest|look for)\b", re.I),
}

_TRIVIAL_TURN_RX = re.compile(
    r'^(yes|no|ok|okay|sure|thanks|thank you|perfect|great|good|looks good|'
    r'go ahead|do it|please|correct|exactly|right|got it|nice|awesome|'
    r'commit|push|open|lgtm|ship it|done|\d+)\s*[.!?]*$', re.I
)

_QUALITY_USER_RX:  re.Pattern | None = None
_QUALITY_TOOL_RX:  re.Pattern | None = None
_QUALITY_GRUNT_RX: re.Pattern | None = None
_QUALITY_MODES:    list = []   # [(name, intents_set, desc)]
_QUALITY_COLORS:   dict = {}


def _load_quality_config() -> None:
    global _QUALITY_USER_RX, _QUALITY_TOOL_RX, _QUALITY_GRUNT_RX, _QUALITY_MODES, _QUALITY_COLORS
    path = Path(__file__).parent / "prompts" / "active_time_quality.txt"
    if not path.exists():
        return
    section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line.strip("[]")
            continue
        if section == "hand_holding_user_patterns":
            _QUALITY_USER_RX = re.compile(line, re.I)
        elif section == "hand_holding_tool_patterns":
            _QUALITY_TOOL_RX = re.compile(line, re.I)
        elif section == "grunt_override_patterns":
            _QUALITY_GRUNT_RX = re.compile(line, re.I)
        elif section == "modes":
            parts = [p.strip() for p in line.split("|", 2)]
            if len(parts) == 3:
                name, intents_str, desc = parts
                _QUALITY_MODES.append((name, {i.strip() for i in intents_str.split(",")}, desc))
        elif section == "mode_colors":
            parts = [p.strip() for p in line.split("|", 1)]
            if len(parts) == 2:
                _QUALITY_COLORS[parts[0]] = parts[1]


_load_quality_config()


def _classify_message_intents(text: str) -> list:
    """Return ALL matching intent categories for a message (multi-intent)."""
    matched = [cat for cat, rx in _QUALITY_INTENT_PATTERNS.items() if rx.search(text[:300])]
    return matched or ["Building"]


def compute_active_time_quality(sessions: list) -> dict:
    """Classify active time into quality modes (minutes per mode).

    Two detection layers:
    1. Hand-holding: user correcting AI OR error signals in tool output
    2. Mode: intent classification (first matching mode wins, Refinement before Building)
    """
    modes: dict = {name: 0.0 for name, _, _ in _QUALITY_MODES}
    modes["Course-correcting"] = 0.0

    for s in sessions:
        user_turns = []
        for m in s.get("messages", []):
            if m.get("role") != "user":
                continue
            ts_str = m.get("timestamp", "")
            ts = None
            try:
                ts = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
            except Exception:
                pass

            text       = m.get("text", "").strip()
            tools_text = " ".join(m.get("tools_after", []))
            intents    = _classify_message_intents(text)

            user_correcting   = bool(_QUALITY_USER_RX  and _QUALITY_USER_RX.search(text[:300]))
            tool_errors       = bool(_QUALITY_TOOL_RX  and _QUALITY_TOOL_RX.search(tools_text))
            is_grunt_override = bool(_QUALITY_GRUNT_RX and _QUALITY_GRUNT_RX.search(text[:300]))
            first_line        = text.split("\n")[0].strip()
            is_trivial        = bool(_TRIVIAL_TURN_RX.match(first_line))

            user_turns.append({
                "ts": ts,
                "intents": intents,
                "needs_handholding": user_correcting or tool_errors,
                "is_grunt_override": is_grunt_override,
                "is_trivial": is_trivial,
            })

        # Time per turn from timestamp gaps — same logic as compute_active_minutes:
        # only count gaps < 5 min (skip idle); last turn in session contributes 0.
        for i in range(len(user_turns)):
            if i < len(user_turns) - 1 and user_turns[i]["ts"] and user_turns[i + 1]["ts"]:
                gap = (user_turns[i + 1]["ts"] - user_turns[i]["ts"]).total_seconds() / 60
                user_turns[i]["minutes"] = gap if gap < 5 else 0.0
            else:
                user_turns[i]["minutes"] = 0.0

        for t in user_turns:
            mins = t["minutes"]
            if t["needs_handholding"]:
                modes["Course-correcting"] += mins
                continue
            if t["is_grunt_override"] or t["is_trivial"]:
                modes["Delegating"] = modes.get("Delegating", 0.0) + mins
                continue
            matched = False
            for mode_name, intent_set, _ in _QUALITY_MODES:
                if any(i in intent_set for i in t["intents"]):
                    modes[mode_name] += mins
                    matched = True
                    break
            if not matched:
                modes["Building"] = modes.get("Building", 0.0) + mins

    return {k: round(v, 1) for k, v in modes.items() if v > 0}


def _compute_extra_metrics(sessions: list) -> dict:
    """Compute time-of-day, file type, and intent breakdowns from raw sessions."""
    from datetime import timezone as _tz
    # Local UTC offset
    try:
        local_offset = datetime.now(timezone.utc).astimezone().utcoffset()
    except Exception:
        local_offset = timedelta(0)

    hourly:         dict[int, int] = {h: 0 for h in range(24)}  # substantive prompts
    hourly_short:   dict[int, int] = {h: 0 for h in range(24)}  # approvals + short keystrokes
    intent_counts:  dict[str, int] = {}

    sample_messages: dict[str, list] = {}

    for s in sessions:
        for m in s.get("messages", []):
            if m.get("role") != "user":
                continue
            ts = m.get("timestamp", "")
            if ts:
                try:
                    dt_utc = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
                    local_h = (dt_utc + local_offset).hour
                    hourly[local_h] = hourly.get(local_h, 0) + 1
                except Exception:
                    pass
            intent = m.get("intent", "Building")
            intent_counts[intent] = intent_counts.get(intent, 0) + 1

            # Collect sample messages per intent (skip trivially short ones)
            text = m.get("text", "").strip()
            if len(text) >= 20:
                sample_messages.setdefault(intent, []).append({
                    "text": text[:200],
                    "date": ts[:10],
                })

        def _add_to(hourly_map, timestamps):
            for ts in timestamps:
                try:
                    dt_utc  = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
                    local_h = (dt_utc + local_offset).hour
                    hourly_map[local_h] = hourly_map.get(local_h, 0) + 1
                except Exception:
                    pass

        # short_timestamps = typed approvals + short-word responses (backward-compat chain)
        _add_to(hourly_short, s.get("short_timestamps",
                               s.get("approval_timestamps",
                               s.get("trivial_timestamps", []))))
        # tool_result_timestamps = Enter-to-approve presses (arrow nav + Enter, default select)
        # these are human actions, count them in the short bucket
        _add_to(hourly_short, s.get("tool_result_timestamps", []))

    # Keep the 8 longest per intent (displayed sorted by recency)
    for k in sample_messages:
        sample_messages[k] = sorted(sample_messages[k], key=lambda m: -len(m["text"]))[:8]

    def _bucket(h_map):
        return {
            "Early Morning (5\u20139am)": sum(h_map[h] for h in range(5, 9)),
            "Morning (9am\u201312pm)":    sum(h_map[h] for h in range(9, 12)),
            "Afternoon (12\u20135pm)":    sum(h_map[h] for h in range(12, 17)),
            "Evening (5\u20139pm)":       sum(h_map[h] for h in range(17, 21)),
            "Night (9pm\u20131am)":       sum(h_map[h] for h in range(21, 24)) + h_map.get(0, 0),
        }

    time_buckets     = _bucket(hourly)        # substantive prompts (4+ words) — default
    time_buckets_all = {                       # substantive + short responses — toggle
        k: time_buckets[k] + _bucket(hourly_short)[k]
        for k in time_buckets
    }

    # File type classification
    file_types: dict[str, int] = {"Scripts": 0, "Reports": 0, "Documents": 0, "Data & Config": 0}
    all_files: set[str] = set()
    for s in sessions:
        all_files.update(s.get("files_touched", []))

    _SCRIPT_EXT  = {".py", ".js", ".ts", ".sh", ".ps1", ".rb", ".go", ".rs",
                    ".jsx", ".tsx", ".cs", ".cpp", ".c", ".java", ".php", ".swift"}
    _REPORT_EXT  = {".html", ".htm", ".pdf", ".pptx", ".xlsx", ".xls"}
    _DOC_EXT     = {".md", ".txt", ".rst", ".doc", ".docx", ".odt"}

    for f in all_files:
        ext = Path(f).suffix.lower()
        if ext in _SCRIPT_EXT:
            file_types["Scripts"] += 1
        elif ext in _REPORT_EXT:
            file_types["Reports"] += 1
        elif ext in _DOC_EXT:
            file_types["Documents"] += 1
        else:
            file_types["Data & Config"] += 1

    quality_modes = compute_active_time_quality(sessions)

    return {
        "hourly_counts":    hourly,
        "time_buckets":     time_buckets,
        "time_buckets_all": time_buckets_all,
        "file_type_counts": file_types,
        "intent_counts":    intent_counts,
        "total_files":      len(all_files),
        "active_minutes":   sum(
            compute_active_minutes(s.get("messages", []))
            for s in sessions
        ),
        "quality_modes":    quality_modes,
        "sample_messages":  sample_messages,
    }


def _compute_active_minutes_simple(sessions: list) -> float:
    return sum(compute_active_minutes(s.get("messages", [])) for s in sessions)


def compute_active_minutes(messages: list) -> float:
    timestamps = []
    for m in messages:
        ts = m.get("timestamp", "")
        if ts:
            try:
                timestamps.append(datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                pass
    if len(timestamps) < 2:
        return len(timestamps) * 2.0
    total = 0.0
    for i in range(1, len(timestamps)):
        gap = (timestamps[i] - timestamps[i - 1]).total_seconds() / 60
        if gap < 5:
            total += gap
    return round(total, 1)


def analyze_day(
    target_date: str,
    sessions: list,
    source: str = "combined",
    refresh: bool = False,
    use_api: bool = True,
    cache_dir: Path | None = None,
) -> dict:
    """
    Analyze a day's sessions and return structured goals/metrics.

    Args:
        target_date: YYYY-MM-DD string
        sessions: list of session dicts from harvest_copilot or harvest_claude
        source: 'copilot', 'claude', or 'combined'
        refresh: bypass cache
        use_api: attempt Claude API analysis (falls back to heuristic)
        cache_dir: where to store cache files
    """
    # Aggregate raw metrics
    total_tokens = {
        "input": sum(s.get("tokens", {}).get("input", 0) for s in sessions),
        "output": sum(s.get("tokens", {}).get("output", 0) for s in sessions),
        "cache_read": sum(s.get("tokens", {}).get("cache_read", 0) for s in sessions),
        "cache_creation": sum(s.get("tokens", {}).get("cache_creation", 0) for s in sessions),
    }
    total_tokens["total"] = sum(total_tokens.values())
    total_lines       = sum(s.get("lines_added",        0) for s in sessions)
    total_lines_logic = sum(s.get("lines_logic",        0) for s in sessions)
    total_lines_rem   = sum(s.get("lines_removed",      0) for s in sessions)
    total_pr     = sum(s.get("premium_requests", 0) for s in sessions)
    all_files    = sorted({f for s in sessions for f in s.get("files_touched", [])})
    all_git_ops  = [op for s in sessions for op in s.get("git_ops", [])]
    all_prs      = [pr for s in sessions for pr in s.get("pull_requests", [])]
    all_repos    = list({r for s in sessions for r in s.get("git_repos", []) if r})

    # Per-project session metrics
    session_metrics: dict = {}
    _proj_files_acc: dict[str, set] = {}
    _proj_active_acc: dict[str, float] = {}
    for s in sessions:
        proj = s.get("project", "unknown")
        n_tools = sum(len(m.get("tools_after", [])) for m in s.get("messages", []) if m.get("role") == "user")
        n_turns = sum(1 for m in s.get("messages", []) if m.get("role") == "user")
        if proj not in _proj_files_acc:
            _proj_files_acc[proj] = set()
        _proj_files_acc[proj].update(s.get("files_touched", []))
        _proj_active_acc[proj] = _proj_active_acc.get(proj, 0.0) + compute_active_minutes(s.get("messages", []))
        if proj not in session_metrics:
            session_metrics[proj] = {
                "tool_invocations": 0, "conversation_turns": 0,
                "lines_added": 0, "lines_logic": 0, "lines_boilerplate": 0, "sessions": 0,
                "reads": 0, "edits": 0, "runs": 0, "searches": 0,
                "files_touched": 0, "active_minutes": 0.0,
            }
        sm = session_metrics[proj]
        sm["tool_invocations"] += n_tools
        sm["conversation_turns"] += n_turns
        sm["lines_added"]       += s.get("lines_added",       0)
        sm["lines_logic"]       += s.get("lines_logic",       0)
        sm["lines_boilerplate"] += s.get("lines_boilerplate", 0)
        sm["sessions"] += 1
        # Categorize tool types
        for m in s.get("messages", []):
            for t in m.get("tools_after", []):
                tl = t.lower()
                if tl.startswith("read"):
                    sm["reads"] += 1
                elif "grep" in tl or "glob" in tl or "search" in tl or "find" in tl:
                    sm["searches"] += 1
                elif tl.startswith("edit") or tl.startswith("write"):
                    sm["edits"] += 1
                elif tl.startswith("run") or tl.startswith("bash"):
                    sm["runs"] += 1
    # Set derived per-project metrics
    for proj in session_metrics:
        session_metrics[proj]["files_touched"] = len(_proj_files_acc.get(proj, set()))
        session_metrics[proj]["active_minutes"] = round(_proj_active_acc.get(proj, 0.0), 1)

    # Always compute extra metrics from raw sessions (time, file types, intents)
    extra = _compute_extra_metrics(sessions)

    def _attach_metrics(result: dict) -> dict:
        result["source"]           = source
        result["tokens"]           = total_tokens
        result["premium_requests"] = total_pr
        result["lines_added"]       = total_lines
        result["lines_logic"]       = total_lines_logic
        result["lines_removed"]     = total_lines_rem
        result["files_modified"]   = all_files
        result["git_ops"]          = all_git_ops
        result["pull_requests"]    = all_prs
        result["git_repos"]        = all_repos
        result["session_metrics"]  = session_metrics
        result["sessions_count"]   = len(sessions)
        result["projects"]         = list(session_metrics.keys())
        result["time_buckets"]     = extra["time_buckets"]
        result["time_buckets_all"] = extra["time_buckets_all"]
        result["intent_counts"]    = extra["intent_counts"]
        result["file_type_counts"] = extra["file_type_counts"]
        result["total_files"]      = extra["total_files"]
        result["active_minutes"]   = extra["active_minutes"]
        result["quality_modes"]    = extra["quality_modes"]
        result["sample_messages"]  = extra["sample_messages"]
        return result

    # Check cache
    if cache_dir:
        cache_file = Path(cache_dir) / f"{source}_{target_date}.json"
        if cache_file.exists() and not refresh:
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if not cached.get("locked"):
                    return _attach_metrics(cached)
            except Exception:
                pass

    # Attempt AI analysis
    if use_api:
        backend, cred = _detect_backend()
        if backend != "none":
            result = _api_analyze(sessions, backend, cred)
            if result:
                result["analysis_method"] = "ai"
                result["analysis_backend"] = backend
                _attach_metrics(result)
                if cache_dir:
                    Path(cache_dir).mkdir(parents=True, exist_ok=True)
                    (Path(cache_dir) / f"{source}_{target_date}.json").write_text(
                        json.dumps(result, indent=2), encoding="utf-8"
                    )
                return result

    # Heuristic fallback
    result = _fallback_analysis(target_date, sessions)
    result["analysis_method"] = "heuristic"
    _attach_metrics(result)
    if cache_dir:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        (Path(cache_dir) / f"{source}_{target_date}.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
    return result


def _build_prompt(sessions: list) -> tuple[str, int]:
    """Build the analysis prompt. Returns (prompt_text, total_tool_calls)."""
    transcript, total_tool_calls = _build_transcript(sessions)
    domain_list = ", ".join(_DOMAIN_SKILLS[:8]) + ", ..."
    tech_list   = ", ".join(_TECH_SKILLS[:8]) + ", ..."
    total_tokens_all = sum(s.get("tokens", {}).get("total", 0) for s in sessions)

    prompt_path = Path(__file__).parent / "prompts" / "analysis.txt"
    if prompt_path.exists():
        template = prompt_path.read_text(encoding="utf-8")
        prompt = template.format(
            transcript=transcript,
            domain_list=domain_list,
            tech_list=tech_list,
            total_tool_calls=total_tool_calls,
            total_tokens_total=f"{total_tokens_all:,}",
        )
    else:
        prompt = (
            "Analyze this AI work session transcript and return JSON "
            "with headline, day_narrative, and goals array:\n\n" + transcript
        )
    return prompt, total_tool_calls


def _parse_raw_response(raw: str) -> dict:
    """Strip markdown fences and parse JSON from a model response."""
    if raw.startswith("```"):
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw.strip())
    return json.loads(raw.strip())


def _api_analyze(sessions: list, backend: str, cred: str) -> dict | None:
    """
    Call the detected AI backend to analyse sessions.
    backend: 'anthropic' | 'claude_cli' | 'gh_models'
    Returns parsed JSON dict or None on failure.
    """
    prompt, _ = _build_prompt(sessions)
    try:
        if backend == "anthropic":
            payload = json.dumps({
                "model": _ANTHROPIC_MODEL,
                "max_tokens": 3000,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }).encode("utf-8")
            req = urllib.request.Request(
                _ANTHROPIC_API_URL, data=payload,
                headers={
                    "x-api-key": cred,
                    "content-type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                response = json.loads(resp.read().decode("utf-8"))
            raw = response["content"][0]["text"].strip()

        elif backend == "claude_cli":
            raw = _claude_cli_analyze(prompt)

        elif backend == "gh_models":
            raw = _gh_models_analyze(prompt, cred)

        else:
            return None

        result = _parse_raw_response(raw)
        result["sessions_count"] = len(sessions)
        result["projects"] = list({s.get("project", "") for s in sessions})
        return result

    except Exception as e:
        print(f"  WARNING: AI analysis failed ({type(e).__name__}: {e}). Using heuristic fallback.")
        return None


def _fallback_analysis(target_date: str, sessions: list) -> dict:
    """Heuristic fallback when API is unavailable.

    Formula (calibrated against 48 days of AI estimates, R²≈0.40 per goal):

        turns_h  = max(0, −0.15 + 0.67 × ln(turns + 1))
        lines_h  = 0.40 × log₂(lines_logic / 100 + 1)
        reads_h  = 0.10 × log₂(read_calls + 1)
        total    = round(turns_h + lines_h + reads_h, nearest 0.25h), min 0.25h

    Where:
        turns       = substantive conversation turns (trivial approvals excluded)
        lines_logic = lines written to logic code files (.py/.js/.ts/.go/… and
                      similar); boilerplate (.html/.css/.json/.md/.yaml/…) is
                      excluded — AI generates it cheaply and the estimator
                      discounts it entirely. Only logic code represents the kind
                      of thinking a human expert would bill for.
        read_calls  = file reads + grep/glob/search tool invocations; captures
                      investigation and analysis sessions where the work is
                      exploration rather than code output.

    The logarithmic turns scale was derived from OLS regression on the full session
    dataset. The tiered step-function previously used overestimated by 5–10× on
    high-turn sessions (e.g. 108 turns → formula gave 26h, AI gives 2.75h).
    read_calls was added after 30-day marginal R² analysis showed it as the only
    signal that consistently improved R² (+0.05) beyond turns + lines_logic.
    """
    import math as _math

    _role_cfg      = load_role_classification()
    _intent_to_tt  = _role_cfg.get("intent_to_task_type", {})
    _role_keywords = _role_cfg.get("roles", [])

    def _infer_role(text: str) -> str:
        tl = text.lower()
        for role, keywords in _role_keywords:
            if any(kw in tl for kw in keywords):
                return role
        return "Software Engineer"

    def _estimate_hours(turns: int, lines_logic: int, read_calls: int = 0) -> float:
        """Calibrated effort estimate from conversation turns + logic lines + read calls."""
        turns_h = max(0.0, -0.15 + 0.67 * _math.log1p(turns))
        lines_h = 0.40 * _math.log(lines_logic / 100 + 1, 2) if lines_logic > 0 else 0.0
        reads_h = 0.10 * _math.log2(read_calls + 1) if read_calls > 0 else 0.0
        total   = turns_h + lines_h + reads_h
        return max(0.25, round(total * 4) / 4)

    goals = []
    for s in sessions:
        proj      = s.get("project", "unknown")
        source    = s.get("source", "ai")
        user_msgs = [m for m in s.get("messages", []) if m.get("role") == "user"]
        if not user_msgs:
            continue

        turns       = len(user_msgs)
        lines_logic = s.get("lines_logic", 0) or 0
        read_calls  = sum(
            1 for m in s.get("messages", []) if m.get("role") == "user"
            for t in m.get("tools_after", [])
            if t.lower().startswith("read") or any(k in t.lower() for k in ("grep", "glob", "search", "find"))
        )
        total_hours = _estimate_hours(turns, lines_logic, read_calls)

        tasks = []
        for msg in user_msgs[:5]:
            text      = msg.get("text", "")[:80]
            intent    = msg.get("intent", "Building")
            task_type = _intent_to_tt.get(intent, "Development")
            role      = _infer_role(text)
            tasks.append({
                "title":              text[:50] or f"Worked on {proj}",
                "what_got_done":      text[:80] or f"Completed task in {proj}",
                "domain_skills":      ["Technical Research"],
                "tech_skills":        [],
                "task_type":          task_type,
                "professional_roles": [role],
                "human_hours":        round(total_hours / max(len(user_msgs[:5]), 1) * 4) / 4,
            })

        goals.append({
            "title": f"Worked on {proj} via {source.title()}",
            "label": proj[:30],
            "summary": f"{len(tasks)} task(s) completed in {proj}.",
            "human_hours": total_hours,
            "project": proj,
            "docs_referenced": [],
            "tasks": tasks,
        })

    first_proj = sessions[0].get("project", "").split("/")[-1].title() if sessions else "Work"
    source_label = sessions[0].get("source", "AI").title() if sessions else "AI"

    return {
        "headline": f"{source_label} activity on {target_date}",
        "primary_focus": first_proj,
        "day_narrative": f"Worked on {first_proj} and related projects using {source_label}. Heuristic analysis — API unavailable.",
        "goals": goals,
    }
