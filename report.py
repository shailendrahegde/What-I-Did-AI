"""
report.py — Combined HTML analytics report for GitHub Copilot + Claude Code.

Three tabs: All (simplified aggregate), Copilot, Claude.
Individual tabs match the design of the reference Copilot report exactly.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────
HOURLY_RATE          = 72.0
COPILOT_SEAT_MONTHLY = 39.0
CLAUDE_SEAT_MONTHLY  = 19.0
COPILOT_SEAT_DAILY   = COPILOT_SEAT_MONTHLY / 30
CLAUDE_SEAT_DAILY    = CLAUDE_SEAT_MONTHLY  / 30

TOKEN_PRICE_IN       = 3.00    # per 1M
TOKEN_PRICE_OUT      = 15.00
TOKEN_PRICE_CACHE_RD = 0.30
TOKEN_PRICE_CACHE_CR = 3.75

# Accent colors per source
# Copilot: official GitHub Copilot purple (brand.github.com)
# Claude:  claude.ai orange
ACCENT = {
    "copilot":  "#8534F3",
    "claude":   "#DE7356",
    "combined": "#2d6a9f",  # neutral slate-blue — not tied to either tool
}
ACCENT_BG = {
    "copilot":  "#f0e8ff",
    "claude":   "#fdf1ee",
    "combined": "#eaf1f8",  # light slate-blue tint
}
# Banner gradient: dark-to-brand for detail page headers
BANNER_BG = {
    "copilot":  "linear-gradient(135deg,#3b189e,#8534F3)",
    "claude":   "linear-gradient(135deg,#a04028,#DE7356)",
    "combined": "linear-gradient(135deg,#24292f,#1b1f23)",
}
ROI_BG = {
    "copilot":  "linear-gradient(135deg,#6a1fcf,#8534F3)",
    "claude":   "linear-gradient(135deg,#c45a3a,#DE7356)",
    "combined": "linear-gradient(135deg,#6a1fcf,#8534F3)",
}

INTENT_COLORS = {
    "Building":      "#0078d4",
    "Iterating":     "#1a7f37",
    "Investigating": "#cf222e",
    "Shipping":      "#0969da",
    "Designing":     "#7b1fa2",
    "Configuring":   "#e65100",
    "Researching":   "#2ecc71",
    "Planning":      "#3498db",
    "Testing":       "#e74c3c",
    "Navigating":    "#1abc9c",
}

# ── Collaboration mode definitions ────────────────────────────────────────────
# Each entry: (mode_name, icon, description, bar_color, is_high_value, [intents])
_COLLAB_MODES = [
    ("Building",          "🏗",  "Writing code, generating files",                 "#0078d4", True,
     ["Building"]),
    ("Researching",       "🔬",  "Exploring options, investigating",                "#1a7f37", True,
     ["Researching", "Investigating"]),
    ("Designing",         "🎨",  "Design, strategy, architecture",                 "#7b1fa2", True,
     ["Designing", "Planning"]),
    ("Refining",          "✨",  "Iterating, polishing, improving",                "#1565c0", True,
     ["Iterating"]),
    ("Analyzing",         "📊",  "Data analysis, image analysis, synthesizing",    "#b45309", True,
     ["Analyzing"]),
    ("Learning",          "💡",  "How-to, explanations, recommendations",          "#0891b2", True,
     ["Learning"]),
    ("Reviewing",         "🔍",  "Code reviews, PR reviews, validating work",      "#4f46e5", True,
     ["Reviewing"]),
    ("Delegating",        "⚡",  "Git ops, config, installs, routine",             "#6a737d", False,
     ["Shipping", "Configuring", "Navigating"]),
    ("Course-correcting", "🔧",  "Errors, retries, course-correcting AI",          "#e65100", False,
     ["Testing", "Correcting"]),
]


# ── Effort-estimation helpers (deterministic formula) ─────────────────────────
# Formula (OLS-calibrated, R²≈0.40 per goal on 48 days of data):
#   turns_h = max(0, -0.15 + 0.67 × ln(turns + 1))
#   reqs_h  = max(0, -0.10 + 0.45 × ln(reqs + 1))   [fallback when turns == 0]
#   lines_h = 0.40 × log₂(lines_logic / 100 + 1)    [logic code only — .py/.ts/.go/…]
#   reads_h = 0.10 × log₂(read_calls + 1)            [file reads + grep/glob/search]
#   tools_h = 0.07 × log₂(tool_invocations + 1)      [execution work — browser, commands, images]
#   interaction_h = turns_h if turns > 0 else reqs_h
#   total   = max(interaction_h + lines_h + reads_h + tools_h, 0.25), rounded to 0.25h
import math as _math

def _turns_h(turns: int) -> float:
    return max(0.0, -0.15 + 0.67 * _math.log1p(turns))


def _reqs_h(reqs: int) -> float:
    """Fallback when turns == 0. Lower coefficient — premium reqs include automated completions."""
    if reqs <= 0: return 0.0
    return max(0.0, -0.10 + 0.45 * _math.log1p(reqs))


def _lines_h(lines_logic: int) -> float:
    if lines_logic <= 0: return 0.0
    return 0.40 * _math.log2(lines_logic / 100 + 1)


def _reads_h(read_calls: int) -> float:
    if read_calls <= 0: return 0.0
    return 0.10 * _math.log2(read_calls + 1)


def _tools_h(n: int) -> float:
    """Total tool invocations → hours. Low coefficient avoids double-counting reads/edits
    for coding tasks; provides +0.25–0.60h for non-coding tasks where lines_h ≈ 0."""
    if n <= 0: return 0.0
    return 0.07 * _math.log2(n + 1)


def _det_est(turns: int, lines_logic: int, read_calls: int,
             tool_invocations: int = 0, reqs: int = 0) -> dict:
    """Return a dict with all formula components and the rounded total."""
    th = _turns_h(turns)
    rqh = _reqs_h(reqs)
    interaction_h = th if turns > 0 else rqh
    lh  = _lines_h(lines_logic)
    rh  = _reads_h(read_calls)
    toh = _tools_h(tool_invocations)
    total = max(0.25, round((interaction_h + lh + rh + toh) * 4) / 4)
    return {
        "turns_h": th, "reqs_h": rqh, "interaction_h": interaction_h,
        "lines_h": lh, "reads_h": rh, "tools_h": toh, "total": total,
    }


DOMAIN_PILL_BG = "#fff3e0"
DOMAIN_PILL_FG = "#e65100"
TECH_PILL_BG   = "#e3f2fd"
TECH_PILL_FG   = "#1565c0"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _e(s) -> str:
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _fmt_h(h: float) -> str:
    if h == 0:
        return "0h"
    if h < 1:
        return f"{int(round(h * 60))}m"
    return f"{h:.1f}h"


def _fmt_tokens(t) -> str:
    """Format a token count (int or tokens-dict with 'total' key) with K/M suffix."""
    if isinstance(t, dict):
        n = int(t.get("total", 0) or 0)
    else:
        n = int(t or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n) if n else ""


def _sum_goal_hours(analysis: dict | None) -> float:
    if not analysis:
        return 0.0
    h = analysis.get("human_hours")
    if h is not None:
        return float(h)
    return sum(g.get("human_hours") or 0 for g in analysis.get("goals", []))


def _token_cost(tokens: dict) -> float:
    return (
        tokens.get("input", 0)         * TOKEN_PRICE_IN       / 1_000_000 +
        tokens.get("output", 0)        * TOKEN_PRICE_OUT      / 1_000_000 +
        tokens.get("cache_read", 0)    * TOKEN_PRICE_CACHE_RD / 1_000_000 +
        tokens.get("cache_creation", 0)* TOKEN_PRICE_CACHE_CR / 1_000_000
    )


# ── Aggregate helpers ─────────────────────────────────────────────────────────

def _agg(analyses: list, key: str) -> dict | None:
    """Merge all per-day analyses for a given source key."""
    items = [a[key] for a in analyses if a.get(key)]
    if not items:
        return None

    raw_goals = []
    for item in items:
        for g in item.get("goals", []):
            gc = dict(g)
            gc["_date"] = item.get("date", "")
            raw_goals.append(gc)

    def _proj_key(proj: str) -> str:
        """Normalize project to first 2 path components for cross-day merging."""
        if not proj:
            return ""
        parts = proj.strip("/").replace("\\", "/").split("/")
        key = "/".join(parts[:2]).lower()
        # Normalize dashes/underscores/spaces so "frontier-firm" == "Frontier Firm"
        import re as _re2
        key = _re2.sub(r'[-_ ]+', '-', key)
        return key

    def _merge_into(existing: dict, g: dict) -> None:
        existing["human_hours"] = round(
            ((existing.get("human_hours") or 0) + (g.get("human_hours") or 0)) * 4
        ) / 4
        existing.setdefault("tasks", []).extend(g.get("tasks", []))
        if g.get("_date", "") > existing.get("_date", ""):
            existing["_date"] = g["_date"]
        # Promote title/summary from highest-hours day
        if (g.get("human_hours") or 0) > (existing.get("_peak_hours") or 0):
            existing["title"]       = g.get("title", existing["title"])
            existing["summary"]     = g.get("summary", existing.get("summary", ""))
            existing["label"]       = g.get("label", existing.get("label", ""))
            existing["_peak_hours"] = g.get("human_hours") or 0

    # Pass 1 — exact title dedup
    seen_titles: dict[str, int] = {}
    goals: list = []
    for g in raw_goals:
        title_key = (g.get("title") or "").lower().strip()
        if title_key and title_key in seen_titles:
            _merge_into(goals[seen_titles[title_key]], g)
        else:
            gc = dict(g)
            gc["_peak_hours"] = g.get("human_hours") or 0
            seen_titles[title_key] = len(goals)
            goals.append(gc)

    # Pass 2 — project-based dedup (same repo folder = same business goal)
    seen_proj: dict[str, int] = {}
    proj_merged: list = []
    for g in goals:
        pk = _proj_key(g.get("project", ""))
        if pk and pk in seen_proj:
            _merge_into(proj_merged[seen_proj[pk]], g)
        else:
            if pk:
                seen_proj[pk] = len(proj_merged)
            proj_merged.append(dict(g))
    goals = proj_merged

    hours = sum(g.get("human_hours") or 0 for g in goals)

    # Merge dicts
    def _sum_dict(field: str) -> dict:
        merged: dict = {}
        for item in items:
            for k, v in item.get(field, {}).items():
                merged[k] = merged.get(k, 0) + v
        return merged

    def _sum_scalar(field: str) -> int:
        return sum(item.get(field, 0) or 0 for item in items)

    tokens = {k: sum(item.get("tokens", {}).get(k, 0) for item in items)
              for k in ("input", "output", "cache_read", "cache_creation", "total")}

    all_files   = sorted({f for item in items for f in item.get("files_modified", [])})
    all_prs     = list({pr for item in items for pr in item.get("pull_requests", [])})
    all_git_ops = [op for item in items for op in item.get("git_ops", [])]
    session_mets: dict = {}
    for item in items:
        for proj, sm in item.get("session_metrics", {}).items():
            if proj not in session_mets:
                session_mets[proj] = dict(sm)
                # deep-copy the tokens sub-dict so we don't mutate cached data
                session_mets[proj]["tokens"] = dict(sm.get("tokens", {}))
            else:
                for k in sm:
                    if k == "tokens":
                        for tk in sm["tokens"]:
                            session_mets[proj]["tokens"][tk] = (
                                session_mets[proj].get("tokens", {}).get(tk, 0) + sm["tokens"].get(tk, 0)
                            )
                    else:
                        session_mets[proj][k] = session_mets[proj].get(k, 0) + sm.get(k, 0)

    active_days = sum(1 for a in analyses if a.get(key))
    sessions_count = _sum_scalar("sessions_count")
    lines_added    = _sum_scalar("lines_added")
    lines_removed  = _sum_scalar("lines_removed")
    total_files    = _sum_scalar("total_files")
    active_minutes = _sum_scalar("active_minutes")

    # Aggregate intent / time / quality modes
    intent_counts    = _sum_dict("intent_counts")
    time_buckets     = _sum_dict("time_buckets")
    time_buckets_all = _sum_dict("time_buckets_all")
    file_type_counts = _sum_dict("file_type_counts")
    quality_modes    = _sum_dict("quality_modes")

    # Merge sample messages: pool across days, keep 8 longest per intent
    sample_messages: dict = {}
    for item in items:
        for intent, msgs in (item.get("sample_messages") or {}).items():
            sample_messages.setdefault(intent, []).extend(msgs)
    for k in sample_messages:
        sample_messages[k] = sorted(sample_messages[k], key=lambda m: -len(m["text"]))[:8]

    # Headline from last active day
    headline = next(
        (item.get("headline", "") for item in reversed(items) if item.get("headline")), ""
    )

    return {
        "headline":        headline,
        "day_narrative":   (items[-1] if items else {}).get("day_narrative", ""),
        "goals":           goals,
        "human_hours":     round(hours * 4) / 4,
        "tokens":          tokens,
        "lines_added":     lines_added,
        "lines_removed":   lines_removed,
        "pull_requests":   all_prs,
        "git_ops":         all_git_ops,
        "files_modified":  all_files,
        "session_metrics": session_mets,
        "sessions_count":  sessions_count,
        "active_days":     active_days,
        "total_files":     total_files,
        "active_minutes":  active_minutes,
        "intent_counts":    intent_counts,
        "time_buckets":     time_buckets,
        "time_buckets_all": time_buckets_all,
        "file_type_counts": file_type_counts,
        "quality_modes":    quality_modes,
        "sample_messages":  sample_messages,
    }


# ── Section builders ──────────────────────────────────────────────────────────

def _section_header(title: str, subtitle: str = "", extra: str = "") -> str:
    sub = f'<div style="font-size:13px;color:rgba(255,255,255,0.65);margin-top:4px;line-height:1.4">{_e(subtitle)}</div>' if subtitle else ""
    return f"""<table width="100%" cellpadding="0" cellspacing="0"><tbody><tr>
<td style="background:linear-gradient(135deg,#24292f,#1b1f23);padding:14px 24px">
  {extra}<div style="font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:1.2px;
              color:rgba(255,255,255,0.9)">{_e(title)}</div>{sub}
</td></tr></tbody></table>"""


def _wrap_section(inner: str) -> str:
    return f"""<tr><td style="background:#ffffff;padding:0;
    border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">{inner}</td></tr>"""


def _kpi_row(data: dict, source: str, active_days: int, vid: str = "") -> str:
    accent     = ACCENT[source]
    goals      = data.get("goals", [])
    hours      = data.get("human_hours", 0) or sum(g.get("human_hours") or 0 for g in goals)
    lines      = data.get("lines_added", 0) or 0
    lines_rem  = data.get("lines_removed", 0) or 0
    prs        = len(data.get("pull_requests", []))
    commits    = sum(1 for op in data.get("git_ops", []) if op == "commit")
    active_min = int(data.get("active_minutes", 0) or 0)

    def _card(value, label, sub="", link_onclick=""):
        sub_html = f'<div style="font-size:10px;color:#6a737d;margin-top:3px;line-height:1.3">{sub}</div>' if sub else ""
        link_html = (
            f'<div style="margin-top:5px"><a href="#" onclick="{link_onclick};return false;" '
            f'style="font-size:10px;color:{accent};text-decoration:none">see evidence &#9658;</a></div>'
        ) if link_onclick else ""
        return f"""<td style="padding:5px;vertical-align:top">
  <div style="background:#ffffff;border:1px solid #dde1e7;border-top:3px solid {accent};
              border-radius:10px;padding:14px 8px;text-align:center;min-height:90px;
              box-shadow:0 1px 4px rgba(0,0,0,0.06)">
    <div style="font-size:24px;font-weight:700;color:{accent};line-height:1;
                letter-spacing:-0.5px">{_e(value)}</div>
    <div style="font-size:9px;font-weight:700;color:#6a737d;text-transform:uppercase;
                letter-spacing:0.7px;margin-top:5px;line-height:1.3">{_e(label)}</div>
    {sub_html}{link_html}
  </div>
</td>"""

    # Active time
    if active_min >= 60:
        act_str = f"{active_min // 60}h {active_min % 60}m"
    else:
        act_str = f"{active_min}m"

    # Speed multiplier: human_hours ÷ active_hours (AI-assisted speedup)
    active_h = active_min / 60
    if active_h >= 0.5 and hours >= 0.5:
        mult = round(hours / active_h * 10) / 10
        speed_str = f"{mult:.1f}\u00d7"
    else:
        speed_str = "\u2014"

    commits_str = f"{commits} commit{'s' if commits != 1 else ''}" if commits else ""
    lines_rem_str = f"{lines_rem:,} removed" if lines_rem else ""

    # "see evidence" scrolls to + opens evidence section
    evid_id   = f"{vid}-" if vid else ""
    evid_click = f"var e=document.getElementById('{evid_id}evidence');if(e){{e.style.display='block';e.scrollIntoView({{behavior:'smooth'}});}}"

    kpi_bg = ACCENT_BG.get(source, "#f0f2f5")
    return f"""<tr><td style="background:{kpi_bg};padding:10px 24px;
    border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">
  <table width="100%" cellpadding="0" cellspacing="0"><tbody><tr>
    {_card(_fmt_h(hours), "Human Effort Equivalent", "", link_onclick=evid_click)}
    {_card(act_str, "Active Time", f"{active_days} active day{'s' if active_days!=1 else ''}")}
    {_card(speed_str, "Speed Multiplier", "vs. unassisted expert")}
    {_card(f"+{lines:,}", "Lines of Code Added", lines_rem_str)}
    {_card(prs if prs else "\u2014", "PRs Merged", commits_str)}
  </tr></tbody></table>
</td></tr>"""


def _roi_assumption_note(source: str) -> str:
    """Small assumption footnote shown beneath the ROI number."""
    if source == "copilot":
        seats = f"GitHub Copilot ${COPILOT_SEAT_MONTHLY:.0f}/mo enterprise seat"
    elif source == "claude":
        seats = f"Claude Max ${CLAUDE_SEAT_MONTHLY:.0f}/mo enterprise seat"
    else:
        seats = (f"GitHub Copilot ${COPILOT_SEAT_MONTHLY:.0f} + "
                 f"Claude ${CLAUDE_SEAT_MONTHLY:.0f} = "
                 f"${COPILOT_SEAT_MONTHLY + CLAUDE_SEAT_MONTHLY:.0f}/mo enterprise seats")
    return (f'<div style="text-align:center;margin-top:14px;font-size:10px;'
            f'color:rgba(255,255,255,0.45);line-height:1.6">'
            f'Assumed: {seats} &nbsp;·&nbsp; ${HOURLY_RATE:.0f}/hr developer rate'
            f'</div>')


def _roi_row(hours: float, n_days: int, source: str, active_min: int = 0) -> str:
    # Always use combined enterprise seat cost as the basis
    combined_monthly = COPILOT_SEAT_MONTHLY + CLAUDE_SEAT_MONTHLY
    seat_cost  = (COPILOT_SEAT_DAILY + CLAUDE_SEAT_DAILY) * n_days

    if seat_cost <= 0 or hours <= 0:
        return ""

    value    = hours * HOURLY_RATE
    leverage = round(value / max(seat_cost, 0.01))
    bg       = ROI_BG.get(source, ROI_BG["copilot"])

    active_h = active_min / 60
    if active_h >= 0.5 and hours >= 0.5:
        speed_mult = round(hours / active_h * 10) / 10
        speed_str  = f"{speed_mult:.1f}\u00d7"
    else:
        speed_str = None

    assumption = _roi_assumption_note(source)

    # Two-metric display when speed multiplier is available
    if speed_str:
        metrics_html = f"""
      <div style="display:flex;justify-content:center;align-items:center;gap:48px">
        <div style="text-align:center">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;
                      color:rgba(255,255,255,0.55);margin-bottom:6px">Return on Investment</div>
          <div style="font-size:52px;font-weight:800;color:#ffffff;line-height:1;letter-spacing:-2px">{leverage}x</div>
          <div style="font-size:11px;color:rgba(255,255,255,0.55);margin-top:4px">value on seat cost</div>
        </div>
        <div style="width:1px;height:60px;background:rgba(255,255,255,0.2)"></div>
        <div style="text-align:center">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;
                      color:rgba(255,255,255,0.55);margin-bottom:6px">Speed Multiplier</div>
          <div style="font-size:52px;font-weight:800;color:#ffffff;line-height:1;letter-spacing:-2px">{speed_str}</div>
          <div style="font-size:11px;color:rgba(255,255,255,0.55);margin-top:4px">vs. unassisted expert</div>
        </div>
      </div>"""
    else:
        metrics_html = f"""
      <div style="text-align:center">
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;
                    color:rgba(255,255,255,0.65);margin-bottom:6px">Return on Investment</div>
        <div style="font-size:52px;font-weight:800;color:#ffffff;line-height:1;letter-spacing:-2px">{leverage}x</div>
        <div style="font-size:11px;color:rgba(255,255,255,0.55);margin-top:4px">value on seat cost</div>
      </div>"""

    return f"""<tr><td style="padding:0;border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:{bg};border-collapse:collapse">
    <tbody><tr><td style="padding:22px 48px">
      {metrics_html}
      {assumption}
    </td></tr></tbody>
  </table>
</td></tr>"""


def _narrative_row(data: dict, accent: str, vid: str = "") -> str:
    """Summary prose — goal titles match 'What Got Accomplished' exactly."""
    goals   = data.get("goals", [])
    hours   = data.get("human_hours", 0) or sum(g.get("human_hours") or 0 for g in goals)
    n_tasks = sum(len(g.get("tasks", [])) for g in goals)
    n_goals = len(goals)

    # Same sort as _goals_section: by hours descending
    sorted_goals = sorted(goals, key=lambda g: -(g.get("human_hours") or 0))

    pfx      = f"{vid}-" if vid else ""
    extra_id = f"{pfx}narr-extra"
    more_id  = f"{pfx}narr-more"

    accent_bg = ACCENT_BG.get("copilot", "#e8f2fb") if accent == ACCENT["copilot"] else ACCENT_BG.get("claude", "#f3eafa")

    def _narr_line(g: dict, i: int) -> str:
        title   = g.get("title", "") or g.get("project", "Unknown")
        summary = g.get("summary", "")
        date    = g.get("_date", "")
        date_badge = (
            f'<span style="font-size:10px;font-weight:600;color:{accent};background:{accent_bg};'
            f'padding:1px 7px;border-radius:8px;margin-right:6px;white-space:nowrap">{_e(date[5:])}</span>'
            if date else ""
        )
        return (
            f'<div style="display:flex;align-items:baseline;margin-bottom:7px;font-size:13px;line-height:1.55">'
            f'<span style="color:{accent};font-weight:700;min-width:18px;margin-right:6px">{i}.</span>'
            f'<span>{date_badge}<span style="font-weight:700;color:#1b1f23">{_e(title)}:</span>'
            f'&nbsp;<span style="color:#6a737d">{_e(summary)}</span></span></div>'
        )

    header = (
        f'<div style="font-size:13px;color:#1b1f23;line-height:1.6;margin-bottom:10px">'
        f'Drove <strong>{n_goals} goal{"s" if n_goals != 1 else ""}</strong> forward, '
        f'spanning {n_tasks} distinct tasks and an estimated '
        f'<strong style="color:{accent}">{_fmt_h(hours)}</strong> of professional effort:</div>'
    )

    visible_html = "".join(_narr_line(g, i + 1) for i, g in enumerate(sorted_goals[:5]))
    extra_goals  = sorted_goals[5:]
    extra_html   = ""
    if extra_goals:
        extra_lines = "".join(_narr_line(g, 5 + i + 1) for i, g in enumerate(extra_goals))
        extra_html = (
            f'<div id="{extra_id}" style="display:none">{extra_lines}</div>'
            f'<div style="padding:6px 0 0">'
            f'<span id="{more_id}" onclick="toggleExtraGoals(\'{extra_id}\',\'{more_id}\',{len(extra_goals)})"'
            f' style="cursor:pointer;font-size:11px;color:{accent};font-weight:600">'
            f'&#9654; Show {len(extra_goals)} more</span></div>'
        )

    return f"""<tr><td style="background:#ffffff;padding:16px 24px 18px;
    border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">
  {header}{visible_html}{extra_html}
</td></tr>"""


def _goals_section(goals: list, source: str, show_date: bool = False, vid: str = "", session_metrics: dict | None = None) -> str:
    if not goals:
        return ""
    accent   = ACCENT[source]
    accent_bg= ACCENT_BG.get(source, "#e8f2fb")
    session_metrics = session_metrics or {}
    rows     = []
    pfx      = f"{vid}-" if vid else ""   # unique prefix per view

    # Sort by hours descending — must match _narrative_row sort so numbered items align
    goals = sorted(goals, key=lambda g: -(g.get("human_hours") or 0))

    # Accumulate totals for footer
    total_goal_hours  = sum(g.get("human_hours") or 0 for g in goals)
    total_goal_tokens = 0

    # Group into top-5 visible + rest collapsible
    for gi, g in enumerate(goals):
        gid   = f"{pfx}goal-{gi}"
        hours = g.get("human_hours") or 0
        title = g.get("title", "")
        proj  = g.get("project","") or g.get("label","")
        date  = g.get("_date", "")
        tasks = g.get("tasks", [])

        # Token column — resolve from session_metrics; handle int or dict
        sm_proj    = session_metrics.get(proj, {})
        tok_raw    = sm_proj.get("tokens", {})
        proj_tokens = tok_raw if isinstance(tok_raw, dict) else {}
        proj_total_tok = proj_tokens.get("total", 0) if isinstance(tok_raw, dict) else int(tok_raw or 0)
        total_goal_tokens += proj_total_tok
        tok_str = _fmt_tokens(proj_total_tok)
        if tok_str:
            token_cell = (
                f'<div style="font-size:14px;font-weight:700;color:#1a7f37">{tok_str}</div>'
                f'<div style="font-size:9px;color:#6a737d;margin-top:1px">tokens</div>'
            )
        else:
            token_cell = ""

        # Domain-skills pills for goal header (top 3, domain only — no tech badges)
        goal_skills: list[str] = []
        for t in tasks:
            for s in t.get("domain_skills", [])[:2]:
                if s not in goal_skills:
                    goal_skills.append(s)
        goal_skills = goal_skills[:3]
        skills_cell = "".join(
            f'<span style="background:{accent_bg};color:{accent};padding:1px 6px;border-radius:7px;'
            f'font-size:10px;font-weight:600;display:inline-block;margin:1px 2px">{_e(s)}</span>'
            for s in goal_skills
        )

        date_badge = f'<span style="font-size:10px;font-weight:600;color:{accent};background:{accent_bg};padding:1px 7px;border-radius:8px;margin-right:6px;white-space:nowrap">{_e(date[5:])}</span>' if (show_date and date) else ""

        goal_row = f"""<tr id="{gid}-hdr" style="cursor:pointer;" onclick="toggleDetail('{gid}')">
  <td style="padding:10px 10px;border-bottom:1px solid #dde1e7;vertical-align:top;width:4%">
    <div style="width:22px;height:22px;background:{accent};border-radius:50%;
                color:#fff;font-size:11px;font-weight:700;text-align:center;line-height:22px">{gi+1}</div>
  </td>
  <td style="padding:10px 8px;border-bottom:1px solid #dde1e7;vertical-align:middle;width:44%">
    <div style="font-size:12px;font-weight:600;color:#1b1f23;line-height:1.35">
      <span id="{gid}-arrow" style="font-size:10px;color:{accent};margin-right:5px">&#9654;</span>
      {date_badge}{_e(title)}
    </div>
  </td>
  <td style="padding:10px 8px;border-bottom:1px solid #dde1e7;vertical-align:middle;width:18%;border-left:1px solid #f0f2f5">
    {skills_cell}
  </td>
  <td style="padding:10px 12px;border-bottom:1px solid #dde1e7;vertical-align:middle;text-align:center;width:18%;border-left:1px solid #f0f2f5">
    {token_cell}
  </td>
  <td style="padding:10px 8px;border-bottom:1px solid #dde1e7;vertical-align:middle;text-align:right;width:16%">
    <div style="font-size:16px;font-weight:700;color:{accent}">{_fmt_h(hours)}</div>
    <div style="font-size:10px;color:#6a737d;margin-top:1px">human est.</div>
  </td>
</tr>"""

        # Task detail rows
        task_rows = ""
        for t in tasks:
            what  = _e(t.get("what_got_done",""))
            th    = t.get("human_hours") or 0
            dp = "".join(
                f'<span style="background:{DOMAIN_PILL_BG};color:{DOMAIN_PILL_FG};padding:1px 6px;border-radius:7px;font-size:10px;font-weight:600;display:inline-block;margin:1px 2px">{_e(s)}</span>'
                for s in t.get("domain_skills",[])[:3]
            )
            tp = "".join(
                f'<span style="background:{TECH_PILL_BG};color:{TECH_PILL_FG};padding:1px 6px;border-radius:7px;font-size:10px;font-weight:600;display:inline-block;margin:1px 2px">{_e(s)}</span>'
                for s in t.get("tech_skills",[])[:3]
            )
            task_rows += f"""<tr>
  <td style="padding:5px 8px;border-bottom:1px solid #e8eaf0;font-size:11px;color:#6a737d;
             padding-left:24px;width:30%;vertical-align:top">{_e(t.get("title",""))}</td>
  <td style="padding:5px 8px;border-bottom:1px solid #e8eaf0;font-size:11px;color:#444;
             width:30%;vertical-align:top">{what}</td>
  <td style="padding:5px 8px;border-bottom:1px solid #e8eaf0;vertical-align:top;width:28%">
    {dp}{tp}
  </td>
  <td style="padding:5px 8px;border-bottom:1px solid #e8eaf0;text-align:right;
             font-size:11px;color:{accent};font-weight:600;width:12%">{_fmt_h(th)}</td>
</tr>"""

        task_section = f"""<tr id="{gid}-tasks" style="display:none">
  <td colspan="5" style="padding:0 8px 8px;background:#f7f9fc">
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border-top:1px solid #dde1e7">
      <tbody>{task_rows}</tbody>
    </table>
  </td>
</tr>"""

        rows.append(goal_row + task_section)

    # Footer row with totals
    footer_tok_str = _fmt_tokens(total_goal_tokens) if total_goal_tokens else "—"
    footer_row = f"""<tr style="background:#f0f2f5;border-top:2px solid {accent}">
  <td colspan="3" style="padding:8px 10px;font-size:11px;font-weight:700;color:#6a737d;text-align:right">Total</td>
  <td style="padding:8px 10px;text-align:center;font-size:13px;font-weight:700;color:#1a7f37">{footer_tok_str}</td>
  <td style="padding:8px 10px;text-align:right;font-size:15px;font-weight:700;color:{accent}">{_fmt_h(total_goal_hours)}</td>
</tr>"""

    # Show more button if >5 goals
    visible    = "".join(rows[:5])
    extra_html = ""
    if len(rows) > 5:
        extra_id = f"{pfx}goals-extra"
        more_id  = f"{pfx}goals-show-more"
        extra_html = f"""<div id="{extra_id}" style="display:none">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tbody>{"".join(rows[5:])}</tbody>
  </table>
</div>
<div style="padding:10px 0 0;text-align:center">
  <span id="{more_id}" onclick="toggleExtraGoals('{extra_id}','{more_id}',{len(rows)-5})"
        style="cursor:pointer;font-size:11px;color:{accent};font-weight:600">
    &#9654; Show {len(rows)-5} more project{"s" if len(rows)-5!=1 else ""}
  </span>
</div>"""

    inner = f"""{_section_header("What Got Accomplished", "Detailed project breakdown with task-level evidence")}
<div style="padding:14px 24px 16px">
  <div id="expand-hint" style="display:none;font-size:10px;color:#6a737d;margin-bottom:8px">
    Click a row to expand tasks
  </div>
  <table width="100%" cellpadding="0" cellspacing="0"
         style="border:1px solid #dde1e7;border-radius:7px;overflow:hidden">
    <tbody>{visible}{footer_row}</tbody>
  </table>
  {extra_html}
</div>"""
    return _wrap_section(inner)


def _produced_section(data: dict, accent: str) -> str:
    ft     = data.get("file_type_counts", {})
    total  = data.get("total_files", 0)
    files  = data.get("files_modified", [])

    def _ft_cell(count, label):
        return (f'<td style="padding:8px 12px;text-align:center;vertical-align:top">'
                f'<div style="font-size:24px;font-weight:700;color:{accent};line-height:1">{count}</div>'
                f'<div style="font-size:10px;font-weight:600;color:#6a737d;margin-top:4px;'
                f'text-transform:uppercase;letter-spacing:0.5px">{label}</div></td>')

    file_cells = (
        _ft_cell(ft.get("Scripts",0),       "💻 Scripts") +
        _ft_cell(ft.get("Reports",0),        "📊 Reports") +
        _ft_cell(ft.get("Documents",0),      "📄 Documents") +
        _ft_cell(ft.get("Data & Config",0),  "⚙ Data & Config")
    )

    # Collapsible file list
    file_list_rows = ""
    for f in sorted(files)[:30]:
        file_list_rows += f'<div style="font-size:10px;color:#1b1f23;padding:2px 0">{_e(f)}</div>'
    file_detail = f"""<div id="deliverables-detail-hdr" style="cursor:pointer;padding:6px 0 0;margin-top:6px"
     onclick="toggleDetail('deliverables-detail')">
  <span id="deliverables-detail-arrow" style="font-size:10px;color:{accent};margin-right:5px">&#9654;</span>
  <span style="font-size:10px;font-weight:600;color:{accent}">Show file names</span>
</div>
<div id="deliverables-detail-tasks" style="display:none;margin-top:8px">
  {file_list_rows}
</div>""" if files else ""

    inner = f"""{_section_header("What Got Produced", "Artifacts created and skills augmented to produce them")}
<div style="padding:14px 24px 18px">
  <div style="font-size:11px;color:#6a737d;margin-bottom:10px">
    <strong style="color:#1b1f23">{total} file{"s" if total!=1 else ""}</strong> created or modified
  </div>
  <table cellpadding="0" cellspacing="0"><tbody><tr>{file_cells}</tr></tbody></table>
  {file_detail}
</div>"""
    return _wrap_section(inner)


def _skills_section(goals: list, accent: str, tool_name: str = "AI") -> str:
    """Skills Mobilized — role rows with bar charts.

    Allocation: primary role (inferred from task_type) gets 65% of task hours;
    secondary roles split the remaining 35%. Tasks with no roles fall back to the
    task_type-derived primary. This ensures bars sum close to total human hours.
    """
    # task_type → primary professional role
    _PRIMARY_ROLE = {
        "Development":        "Software Engineer",
        "Bug Fix & Debug":    "Software Engineer",
        "Analysis & Research":"Data Analyst",
        "Design & UX":        "UX Designer",
        "Execution & Ops":    "DevOps Engineer",
    }
    PRIMARY_SHARE   = 0.65
    SECONDARY_SHARE = 0.35

    role_hours: dict[str, float] = {}

    for g in goals:
        for t in g.get("tasks", []):
            h     = t.get("human_hours") or 0
            if h == 0:
                continue
            roles = t.get("professional_roles", [])
            tt    = t.get("task_type", "")
            inferred_primary = _PRIMARY_ROLE.get(tt, "")

            if not roles:
                # No roles listed — assign entirely to inferred primary if known
                target = inferred_primary or "Software Engineer"
                role_hours[target] = role_hours.get(target, 0) + h

            elif len(roles) == 1:
                role_hours[roles[0]] = role_hours.get(roles[0], 0) + h

            else:
                # Identify primary: prefer the inferred role if present, else first listed
                primary = roles[0]
                if inferred_primary and inferred_primary in roles:
                    primary = inferred_primary
                secondaries = [r for r in roles if r != primary]

                role_hours[primary] = role_hours.get(primary, 0) + h * PRIMARY_SHARE
                if secondaries:
                    share_each = h * SECONDARY_SHARE / len(secondaries)
                    for r in secondaries:
                        role_hours[r] = role_hours.get(r, 0) + share_each

    if not role_hours:
        return ""

    sorted_roles = sorted(role_hours.items(), key=lambda x: -x[1])
    top_roles    = sorted_roles[:10]
    other_h      = sum(h for _, h in sorted_roles[10:])
    if other_h > 0:
        top_roles = top_roles + [("Other roles", other_h)]
    displayed_h  = sum(h for _, h in top_roles)
    max_h        = max(h for _, h in top_roles) if top_roles else 1
    rows         = ""
    for role, h in top_roles:
        bar_pct = int(h / max_h * 100)
        rows += f"""<tr>
  <td style="padding:4px 12px 4px 0;font-size:11px;color:#6a737d;white-space:nowrap;width:180px">{_e(role)}</td>
  <td style="padding:4px 0;width:auto">
    <div style="background:#e8f2fb;border-radius:4px;height:14px;width:100%">
      <div style="background:{accent};border-radius:4px;height:14px;width:{bar_pct}%;min-width:2px"></div>
    </div>
  </td>
  <td style="padding:4px 0 4px 10px;font-size:11px;font-weight:600;color:{accent};white-space:nowrap;width:50px">
    {_fmt_h(h)}
  </td>
</tr>"""

    inner = f"""{_section_header("Skills Mobilized", f"This is the team that {tool_name} assembled for you — at no headcount cost")}
<div style="padding:14px 24px 18px">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tbody>{rows}</tbody>
  </table>
  <div style="font-size:11px;color:#6a737d;margin-top:10px">
    Total attributed: {_fmt_h(displayed_h)} &nbsp;·&nbsp;
    Multi-role tasks weighted by primary contribution (task type &rarr; role)
  </div>
</div>"""
    return _wrap_section(inner)


def _collab_section(data: dict, source: str, tool_name: str) -> str:
    quality_modes = data.get("quality_modes", {})
    if not quality_modes:
        return ""
    accent     = ACCENT[source]
    active_min = int(data.get("active_minutes", 0) or 0)

    # quality_modes: mode_name → minutes (time-weighted, from compute_active_time_quality)
    total_min = sum(quality_modes.values()) or 1

    # Build mode_data from _COLLAB_MODES registry (single source of truth)
    _QUALITY_COLOR_MAP = {n: color for n, _, _, color, _, _ in _COLLAB_MODES}
    _QUALITY_ICON_MAP  = {n: icon  for n, icon, _, _, _, _ in _COLLAB_MODES}
    _HIGH_VALUE        = {n for n, _, _, _, hv, _ in _COLLAB_MODES if hv}

    mode_data = []
    for mode_name, mins in sorted(quality_modes.items(), key=lambda x: -x[1]):
        pct      = mins / total_min * 100
        hrs      = mins / 60
        color    = _QUALITY_COLOR_MAP.get(mode_name, "#6a737d")
        icon     = _QUALITY_ICON_MAP.get(mode_name, "")
        high_val = mode_name in _HIGH_VALUE
        desc     = next((d for n, _i, d, *_ in _COLLAB_MODES if n == mode_name), "")
        if not desc:
            # Fallback desc for modes not in _COLLAB_MODES
            desc = {"Course-correcting": "Errors, retries, course-correcting AI"}.get(mode_name, "")
        mode_data.append((mode_name, icon, desc, color, high_val, mins, pct, hrs))
    mode_data.sort(key=lambda x: -x[6])

    # Summary stats  (m: name, icon, desc, color, high_val, mins, pct, hrs)
    high_val_pct  = sum(m[6] for m in mode_data if m[4])
    grunt_pct     = next((m[6] for m in mode_data if m[0] == "Delegating"), 0)
    handheld_pct  = next((m[6] for m in mode_data if m[0] == "Course-correcting"), 0)
    n_modes       = sum(1 for m in mode_data if m[5] > 0)   # mins > 0

    # High-value description
    hv_names = [m[0].lower().replace(" assistant","").replace(" partner","")
                for m in mode_data if m[4] and m[5] > 0]
    hv_str   = ", ".join(hv_names[:-1]) + (f", and {hv_names[-1]}" if len(hv_names) > 1 else (hv_names[0] if hv_names else ""))

    active_str = f"{active_min // 60}h {active_min % 60}m" if active_min >= 60 else f"{active_min}m"

    summary_html = (
        f'<div style="font-size:15px;font-weight:700;color:#1b1f23;line-height:1.4;margin-bottom:8px">'
        f'{high_val_pct:.0f}% of your collaboration was high-value work'
        f'{(" — " + hv_str + ".") if hv_str else "."}</div>'
        f'<div style="font-size:11px;color:#6a737d;margin-bottom:18px;line-height:1.5">'
        f'{active_str} of active collaboration across {n_modes} modes'
        f'{f" &nbsp;·&nbsp; {tool_name} automated {grunt_pct:.0f}% of routine grunt work" if grunt_pct > 0 else ""}'
        f'{f" &nbsp;·&nbsp; {handheld_pct:.0f}% was spent course-correcting AI output" if handheld_pct > 0 else ""}'
        f'</div>'
    )

    _MODE_EXAMPLES = {
        "Building":          "e.g. implement a feature, scaffold a module, write from scratch",
        "Refining":          "e.g. tweak layout, adjust error handling, rename, polish copy",
        "Designing":         "e.g. plan architecture, choose a pattern, rethink an approach",
        "Researching":       "e.g. compare libraries, investigate a failure, understand behavior",
        "Analyzing":         "e.g. analyze this dataset, summarize results, what does this chart show",
        "Learning":          "e.g. how do I, explain this to me, what's the best practice, suggest an approach",
        "Reviewing":         "e.g. review this code, does this look right, any issues with this PR",
        "Delegating":        "e.g. git commit & push, update README, install packages, configure CI",
        "Course-correcting": "e.g. fix a wrong assumption, undo a bad change, redirect AI",
    }
    _MODE_INTENTS = {n: intents for n, _, _, _, _, intents in _COLLAB_MODES}
    sample_messages = data.get("sample_messages") or {}
    import hashlib as _hl2
    _uid2 = _hl2.md5((source + tool_name + "collab").encode()).hexdigest()[:6]

    def _sessions_toggle(name, color):
        """'From your sessions' expandable for a mode — returns HTML or empty string."""
        mode_intents = _MODE_INTENTS.get(name, [name])
        raw_samples = []
        for intent in mode_intents:
            raw_samples.extend(sample_messages.get(intent, []))
        samples = sorted(raw_samples, key=lambda m: m.get("date", ""), reverse=True)[:3]
        if not samples:
            return ""
        card_id = f"{source}-{_uid2}-{name.lower().replace(' ','').replace('-','')}-ex"
        items_html = "".join(
            f'<div style="padding:5px 0;border-bottom:1px solid #f4f4f4;'
            f'display:flex;gap:10px;align-items:baseline">'
            f'<span style="font-size:9px;color:#aaa;white-space:nowrap;min-width:38px">'
            f'{m["date"][5:] if m.get("date") else ""}</span>'
            f'<span style="font-size:11px;color:#444;font-style:italic">'
            f'"{_e(m["text"][:90].rstrip())}{"…" if len(m["text"]) > 90 else ""}"'
            f'</span></div>'
            for m in samples
        )
        return (
            f'<span style="margin-left:8px">'
            f'<button onclick="var d=document.getElementById(\'{card_id}\');'
            f'var a=this.querySelector(\'.arr{card_id}\');'
            f'if(d.style.display===\'none\'){{d.style.display=\'block\';a.textContent=\'▼\'}}'
            f'else{{d.style.display=\'none\';a.textContent=\'▶\'}};return false" '
            f'style="background:none;border:none;cursor:pointer;font-size:10px;'
            f'color:{color};padding:0;display:inline-flex;align-items:center;gap:3px;'
            f'font-family:inherit;vertical-align:middle">'
            f'<span class="arr{card_id}">▶</span> From your sessions'
            f'</button></span>'
            f'<div id="{card_id}" style="display:none;margin-top:6px;padding-left:4px">'
            f'{items_html}</div>'
        )

    # Build table rows (sorted by minutes desc)
    visible = [m for m in mode_data if m[5] >= 0.1]
    max_pct = max((m[6] for m in visible), default=1)

    def _table_row(name, icon, desc, color, high_val, mins, pct, hrs, hidden=False):
        bar_w   = max(int(pct / max_pct * 100), 2)
        hrs_str = f"{int(mins)}m" if mins < 60 else _fmt_h(hrs)
        example = _MODE_EXAMPLES.get(name, "")
        sessions_html = _sessions_toggle(name, color)
        display = ' style="display:none"' if hidden else ""
        return (
            f'<tr{display}>'
            f'<td style="padding:8px 10px 8px 4px;white-space:nowrap;vertical-align:middle;width:130px">'
            f'  <span style="font-size:13px;margin-right:6px">{icon}</span>'
            f'  <span style="font-size:13px;font-weight:600;color:#1b1f23">{_e(name)}</span>'
            f'</td>'
            f'<td style="padding:8px 10px;vertical-align:middle">'
            f'  <div style="background:#f0f2f5;border-radius:4px;height:8px;width:100%">'
            f'    <div style="background:{color};border-radius:4px;height:8px;width:{bar_w}%;min-width:4px"></div>'
            f'  </div>'
            f'  <div style="font-size:10px;color:#6a737d;margin-top:3px;font-style:italic">{_e(example)}</div>'
            f'  {sessions_html}'
            f'</td>'
            f'<td style="padding:8px 4px 8px 10px;white-space:nowrap;text-align:right;vertical-align:middle;width:60px">'
            f'  <span style="font-size:15px;font-weight:700;color:{color}">{pct:.0f}%</span>'
            f'  <div style="font-size:10px;color:#6a737d">{hrs_str}</div>'
            f'</td>'
            f'</tr>'
        )

    top5    = visible[:5]
    rest    = visible[5:]
    rows_top  = "".join(_table_row(*m) for m in top5)
    rows_rest = "".join(_table_row(*m, hidden=True) for m in rest)

    show_more_btn = ""
    more_id = f"cm{_uid2}"
    if rest:
        # Tag each hidden rest row with a unique class-like id prefix for easy JS selection
        tagged_rows = []
        for i, m in enumerate(rest):
            row = _table_row(*m, hidden=True)
            row = row.replace("<tr ", f'<tr id="{more_id}r{i}" ', 1)
            tagged_rows.append(row)
        rows_rest = "".join(tagged_rows)
        js = (
            f"var c=document.getElementById('{more_id}btn');"
            f"var i=0;var r;"
            f"while((r=document.getElementById('{more_id}r'+i))!==null){{r.style.display='';i++;}}"
            f"c.style.display='none';"
        )
        show_more_btn = (
            f'<tr id="{more_id}btn"><td colspan="3" style="padding:6px 4px;text-align:center">'
            f'<button onclick="{js}" '
            f'style="background:none;border:1px solid #d0d7de;border-radius:4px;cursor:pointer;'
            f'font-size:11px;color:#57606a;padding:4px 14px;font-family:inherit">'
            f'Show {len(rest)} more</button></td></tr>'
        )

    table_html = (
        f'<table width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse">'
        f'<tbody>{rows_top}{rows_rest}{show_more_btn}</tbody></table>'
    )

    inner = f"""{_section_header("How I Collaborated", f"Here\u2019s how you interacted with {tool_name} \u2014 these are the working styles you\u2019ll lean on more as AI becomes central to how you work")}
<div style="padding:16px 24px 18px">
  {summary_html}
  {table_html}
</div>"""
    return _wrap_section(inner)


def _collab_comparison_section(copilot_modes: dict, claude_modes: dict,
                               copilot_active_min: float, claude_active_min: float) -> str:
    """Stacked top/bottom comparison of collaboration modes between Copilot and Claude."""
    if not copilot_modes and not claude_modes:
        return ""

    # Derive color/icon/examples from _COLLAB_MODES (single source of truth)
    _COLOR    = {n: color for n, _, _, color, _, _ in _COLLAB_MODES}
    _ICON     = {n: icon  for n, icon, _, _, _, _ in _COLLAB_MODES}
    _EXAMPLES = {
        "Building":          "e.g. implement a feature, scaffold a module, write from scratch",
        "Refining":          "e.g. tweak layout, adjust error handling, rename, polish copy",
        "Designing":         "e.g. plan architecture, choose a pattern, rethink an approach",
        "Researching":       "e.g. compare libraries, investigate a failure, understand behavior",
        "Analyzing":         "e.g. analyze this dataset, summarize results, what does this chart show",
        "Learning":          "e.g. how do I, explain this to me, what's the best practice",
        "Reviewing":         "e.g. review this code, does this look right, any issues with this PR",
        "Delegating":        "e.g. git commit & push, update README, install packages, configure CI",
        "Course-correcting": "e.g. fix a wrong assumption, undo a bad change, redirect AI",
    }

    all_modes = sorted(
        set(copilot_modes) | set(claude_modes),
        key=lambda m: -(copilot_modes.get(m, 0) + claude_modes.get(m, 0))
    )

    cop_total = sum(copilot_modes.values()) or 1
    cla_total = sum(claude_modes.values()) or 1

    # Insight: biggest divergence between the two tools
    divergences = []
    for m in all_modes:
        cop_pct = copilot_modes.get(m, 0) / cop_total * 100
        cla_pct = claude_modes.get(m, 0) / cla_total * 100
        divergences.append((m, cop_pct, cla_pct, abs(cop_pct - cla_pct)))
    divergences.sort(key=lambda x: -x[3])
    top_div = divergences[0] if divergences else None

    insight_html = ""
    if top_div:
        mode_name, cop_p, cla_p, _ = top_div
        leader       = "GitHub Copilot" if cop_p > cla_p else "Claude"
        follower     = "Claude"         if cop_p > cla_p else "GitHub Copilot"
        leader_pct   = max(cop_p, cla_p)
        follower_pct = min(cop_p, cla_p)
        insight_html = (
            f'<div style="background:#f6f8fa;border:1px solid #e1e4e8;border-radius:8px;'
            f'padding:12px 16px;margin-bottom:16px;font-size:12px;color:#1b1f23;line-height:1.5">'
            f'<strong>Biggest difference:</strong> <em>{_e(mode_name)}</em> — '
            f'{_e(leader)} {leader_pct:.0f}% vs {_e(follower)} {follower_pct:.0f}%'
            f'</div>'
        )

    cop_active = f"{int(copilot_active_min)//60}h {int(copilot_active_min)%60}m" if copilot_active_min >= 60 else f"{int(copilot_active_min)}m"
    cla_active = f"{int(claude_active_min)//60}h {int(claude_active_min)%60}m" if claude_active_min >= 60 else f"{int(claude_active_min)}m"

    # Legend row
    legend_html = (
        f'<div style="display:flex;gap:24px;margin-bottom:12px;font-size:11px;color:#6a737d">'
        f'<span><span style="font-weight:700;color:{ACCENT["copilot"]}">● GitHub Copilot</span>'
        f' &nbsp;{cop_active} active</span>'
        f'<span><span style="font-weight:700;color:{ACCENT["claude"]}">● Claude</span>'
        f' &nbsp;{cla_active} active</span>'
        f'</div>'
    )

    # Table header
    th = (
        f'<tr style="border-bottom:2px solid #e1e4e8">'
        f'<th style="padding:6px 8px;font-size:11px;font-weight:600;color:#6a737d;text-align:left;width:120px">Mode</th>'
        f'<th style="padding:6px 8px;font-size:11px;font-weight:600;color:{ACCENT["copilot"]};text-align:center;width:80px">Copilot</th>'
        f'<th style="padding:6px 8px;font-size:11px;font-weight:600;color:{ACCENT["claude"]};text-align:center;width:80px">Claude</th>'
        f'<th style="padding:6px 8px;font-size:11px;font-weight:600;color:#6a737d;text-align:left">Activity type</th>'
        f'</tr>'
    )

    import hashlib as _hl3
    _cmid = _hl3.md5(b"comparison").hexdigest()[:6]

    def _cmp_row(mode, hidden=False, row_id=""):
        cop_pct = copilot_modes.get(mode, 0) / cop_total * 100
        cla_pct = claude_modes.get(mode, 0) / cla_total * 100
        cop_min = copilot_modes.get(mode, 0)
        cla_min = claude_modes.get(mode, 0)
        color   = _COLOR.get(mode, "#6a737d")
        icon    = _ICON.get(mode, "")
        example = _EXAMPLES.get(mode, "")

        def _mini_bar(pct, color, mins):
            w = max(int(pct), 1) if pct > 0 else 0
            hrs = f"{int(mins)}m" if mins < 60 else f"{mins/60:.1f}h"
            bar = f'<div style="background:#f0f2f5;border-radius:3px;height:8px;margin-bottom:3px"><div style="background:{color};border-radius:3px;height:8px;width:{w}%;min-width:2px"></div></div>' if pct > 0 else '<div style="height:8px;margin-bottom:3px"></div>'
            val = f'<span style="font-size:12px;font-weight:700;color:{color}">{pct:.0f}%</span><span style="font-size:10px;color:#6a737d"> · {hrs}</span>' if pct > 0 else '<span style="font-size:11px;color:#d0d7de">—</span>'
            return f'{bar}{val}'

        id_attr = f' id="{row_id}"' if row_id else ""
        disp    = ' style="display:none"' if hidden else ""
        return (
            f'<tr{id_attr}{disp} style="border-bottom:1px solid #f0f2f5">'
            f'<td style="padding:10px 8px;vertical-align:middle">'
            f'  <span style="font-size:14px;margin-right:5px">{icon}</span>'
            f'  <span style="font-size:13px;font-weight:600;color:{color}">{_e(mode)}</span>'
            f'</td>'
            f'<td style="padding:10px 8px;vertical-align:middle;min-width:90px">{_mini_bar(cop_pct, ACCENT["copilot"], cop_min)}</td>'
            f'<td style="padding:10px 8px;vertical-align:middle;min-width:90px">{_mini_bar(cla_pct, ACCENT["claude"], cla_min)}</td>'
            f'<td style="padding:10px 8px;vertical-align:middle;font-size:10px;color:#8a8a8a;font-style:italic">{_e(example)}</td>'
            f'</tr>'
        )

    top5 = all_modes[:5]
    rest = all_modes[5:]
    rows_top  = "".join(_cmp_row(m) for m in top5)
    rows_rest = "".join(_cmp_row(m, hidden=True, row_id=f"{_cmid}r{i}") for i, m in enumerate(rest))

    show_more = ""
    if rest:
        js = (f"var i=0;var r;while((r=document.getElementById('{_cmid}r'+i))!==null){{r.style.display='';i++;}}"
              f"document.getElementById('{_cmid}btn').style.display='none';")
        show_more = (
            f'<tr id="{_cmid}btn"><td colspan="4" style="padding:6px;text-align:center">'
            f'<button onclick="{js}" style="background:none;border:1px solid #d0d7de;border-radius:4px;'
            f'cursor:pointer;font-size:11px;color:#57606a;padding:4px 14px;font-family:inherit">'
            f'Show {len(rest)} more</button></td></tr>'
        )

    table_html = (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
        f'<thead>{th}</thead>'
        f'<tbody>{rows_top}{rows_rest}{show_more}</tbody>'
        f'</table>'
    )

    inner = f"""{_section_header("How I Collaborated", "How your working style differs across tools \u2014 each column shows % of that tool\u2019s own active time")}
<div style="padding:16px 24px 16px">
  {insight_html}
  {legend_html}
  {table_html}
</div>"""
    return _wrap_section(inner)


def _timing_section(data: dict, source: str, tool_name: str,
                    copilot_buckets:        dict | None = None,
                    claude_buckets:         dict | None = None,
                    copilot_buckets_all:    dict | None = None,
                    claude_buckets_all:     dict | None = None,
                    copilot_active_minutes: float = 0,
                    claude_active_minutes:  float = 0) -> str:
    buckets = data.get("time_buckets", {})
    if not buckets or not any(buckets.values()):
        return ""

    split = copilot_buckets is not None and claude_buckets is not None
    has_all = (split and copilot_buckets_all is not None and claude_buckets_all is not None) \
              or (not split and bool(data.get("time_buckets_all")))

    # Unique DOM id so multiple sections on the same page don't collide
    import hashlib as _hl
    _uid = _hl.md5((source + tool_name).encode()).hexdigest()[:6]

    def _make_rows(cop_b, cla_b, single_b, include_all_label=False):
        """Render table rows for one dataset (split or single-source)."""
        if split:
            combined = {k: cop_b.get(k, 0) + cla_b.get(k, 0) for k in buckets}
        else:
            combined = single_b
        max_v = max(combined.values()) or 1
        rows = ""
        for bucket in buckets:
            total = combined.get(bucket, 0)
            if split:
                cop_n = cop_b.get(bucket, 0)
                cla_n = cla_b.get(bucket, 0)
                cop_w = int(cop_n / max_v * 100)
                cla_w = int(cla_n / max_v * 100)
                cop_label = f'<span style="font-size:10px;font-weight:600;color:#fff;padding:0 4px;white-space:nowrap">{cop_n}</span>' if cop_w >= 8 else ""
                cla_label = f'<span style="font-size:10px;font-weight:600;color:#fff;padding:0 4px;white-space:nowrap">{cla_n}</span>' if cla_w >= 8 else ""
                rows += f"""<tr>
  <td style="padding:4px 12px 4px 0;font-size:11px;color:#6a737d;white-space:nowrap;width:160px">{_e(bucket)}</td>
  <td style="padding:4px 0;width:auto">
    <div style="background:#f0f0f5;border-radius:4px;height:20px;width:100%;display:flex;overflow:hidden;align-items:center">
      <div style="background:{ACCENT["copilot"]};height:20px;width:{cop_w}%;min-width:{2 if cop_n else 0}px;display:flex;align-items:center;justify-content:flex-end">{cop_label}</div>
      <div style="background:{ACCENT["claude"]};height:20px;width:{cla_w}%;min-width:{2 if cla_n else 0}px;display:flex;align-items:center;justify-content:flex-start">{cla_label}</div>
    </div>
  </td>
  <td style="padding:4px 0 4px 0;width:0"></td>
</tr>"""
            else:
                accent = ACCENT[source]
                w = int(total / max_v * 100)
                rows += f"""<tr>
  <td style="padding:3px 12px 3px 0;font-size:11px;color:#6a737d;white-space:nowrap;width:160px">{_e(bucket)}</td>
  <td style="padding:3px 0;width:auto">
    <div style="background:#e8f2fb;border-radius:4px;height:16px;width:100%">
      <div style="background:{accent};border-radius:4px;height:16px;width:{max(w,1)}%;min-width:2px"></div>
    </div>
  </td>
  <td style="padding:3px 0 3px 10px;font-size:11px;color:#6a737d;white-space:nowrap;width:80px">
    {total} msg{"s" if total!=1 else ""}
  </td>
</tr>"""
        return rows

    rows_filtered = _make_rows(copilot_buckets, claude_buckets, buckets)
    rows_all      = _make_rows(copilot_buckets_all, claude_buckets_all,
                               data.get("time_buckets_all", buckets)) if has_all else ""

    legend = ""
    kpi_filtered = ""
    kpi_all = ""

    if not split and has_all:
        # Per-source tab: show simple interaction counts as KPI chips
        accent = ACCENT.get(source, "#0969da")
        active_h = data.get("active_minutes", 0) / 60
        f_total = sum(buckets.values())
        a_total = sum(data.get("time_buckets_all", buckets).values())
        def _simple_chip(label, value, color):
            return f"""<div style="background:#f6f8fa;border:1px solid #e1e4e8;border-radius:6px;
                           padding:8px 14px;display:inline-block;margin-right:10px">
  <div style="font-size:10px;color:#6a737d;margin-bottom:2px">{label}</div>
  <div style="font-size:16px;font-weight:700;color:{color}">{value}<span style="font-size:10px;font-weight:400;color:#6a737d;margin-left:3px">interactions</span></div>
</div>"""
        kpi_filtered = f'<div style="margin-bottom:14px">{_simple_chip("Substantive prompts", f_total, accent)}</div>'
        kpi_all      = f'<div style="margin-bottom:14px">{_simple_chip("All human interactions", a_total, accent)}</div>'

    if split:
        legend = f"""<div style="display:flex;gap:16px;margin-bottom:10px;font-size:11px;color:#6a737d">
  <span><span style="display:inline-block;width:10px;height:10px;background:{ACCENT["copilot"]};border-radius:2px;margin-right:4px"></span>GitHub Copilot</span>
  <span><span style="display:inline-block;width:10px;height:10px;background:{ACCENT["claude"]};border-radius:2px;margin-right:4px"></span>Claude</span>
</div>"""

        def _kpi_chip(label, value, color):
            return f"""<div style="background:#f6f8fa;border:1px solid #e1e4e8;border-radius:6px;
                           padding:8px 14px;display:inline-block;margin-right:10px">
  <div style="font-size:10px;color:#6a737d;margin-bottom:2px">{label}</div>
  <div style="font-size:16px;font-weight:700;color:{color}">{value}<span style="font-size:10px;font-weight:400;color:#6a737d;margin-left:3px">msgs/active hr</span></div>
</div>"""

        cop_h = copilot_active_minutes / 60 if copilot_active_minutes else 0
        cla_h = claude_active_minutes  / 60 if claude_active_minutes  else 0

        cop_f = sum(copilot_buckets.values())
        cla_f = sum(claude_buckets.values())
        kpi_filtered = f"""<div style="margin-bottom:14px;display:flex;flex-wrap:wrap;gap:4px">
  {_kpi_chip("GitHub Copilot engagement", str(round(cop_f/cop_h)) if cop_h else "—", ACCENT["copilot"])}
  {_kpi_chip("Claude engagement", str(round(cla_f/cla_h)) if cla_h else "—", ACCENT["claude"])}
</div>"""

        if has_all:
            cop_a = sum(copilot_buckets_all.values())
            cla_a = sum(claude_buckets_all.values())
            kpi_all = f"""<div style="margin-bottom:14px;display:flex;flex-wrap:wrap;gap:4px">
  {_kpi_chip("GitHub Copilot engagement", str(round(cop_a/cop_h)) if cop_h else "—", ACCENT["copilot"])}
  {_kpi_chip("Claude engagement", str(round(cla_a/cla_h)) if cla_h else "—", ACCENT["claude"])}
</div>"""

    # Toggle button (only shown when we have the "all" dataset)
    toggle_html = ""
    if has_all:
        toggle_html = f"""
<div style="float:right;margin-top:-2px">
  <button id="tog-{_uid}"
    onclick="(function(){{
      var f=document.getElementById('rows-f-{_uid}');
      var a=document.getElementById('rows-a-{_uid}');
      var kf=document.getElementById('kpi-f-{_uid}');
      var ka=document.getElementById('kpi-a-{_uid}');
      var b=document.getElementById('tog-{_uid}');
      var showAll=f.style.display!=='none';
      f.style.display=showAll?'none':'';
      a.style.display=showAll?'':'none';
      kf.style.display=showAll?'none':'';
      ka.style.display=showAll?'':'none';
      b.textContent=showAll?'Substantive only':'Include short responses';
    }})()"
    style="font-size:10px;padding:3px 10px;border:1px solid #d0d7de;border-radius:4px;
           background:#f6f8fa;color:#57606a;cursor:pointer;white-space:nowrap">
    Include short responses
  </button>
</div>"""

    subtitle = "Substantive prompts (4+ words) by time of day — toggle adds approvals, short responses, and Enter-to-approve interactions"
    inner = f"""{_section_header("When I Worked", subtitle, extra=toggle_html)}
<div style="padding:14px 24px 18px">
  <div id="kpi-f-{_uid}">{kpi_filtered}</div>
  <div id="kpi-a-{_uid}" style="display:none">{kpi_all}</div>
  {legend}
  <table width="100%" cellpadding="0" cellspacing="0">
    <tbody id="rows-f-{_uid}">{rows_filtered}</tbody>
    <tbody id="rows-a-{_uid}" style="display:none">{rows_all}</tbody>
  </table>
</div>"""
    return _wrap_section(inner)


def _numbers_section(data: dict, source: str, n_days: int) -> str:
    tokens = data.get("tokens", {})
    prem   = data.get("premium_requests", 0)
    total_tok = tokens.get("total", 0)

    if source == "copilot":
        seat_html = f"""<span style="font-size:10px;font-weight:700;text-transform:uppercase;
                    letter-spacing:0.7px;color:#6a737d;margin-right:10px">Cost</span>
      <span style="font-size:11px;color:#1b1f23">
        <span style="color:#6a737d">Copilot seat</span> <strong>$39/mo</strong>
        <span style="font-size:10px;color:#6a737d">(Enterprise, fixed)</span>
      </span>"""
        model_html = f"""<span style="font-size:10px;font-weight:700;text-transform:uppercase;
                    letter-spacing:0.7px;color:#6a737d;margin-right:10px">Copilot</span>
      <span style="font-size:11px;color:#1b1f23">
        <span style="color:#6a737d">Premium requests</span> <strong>{prem:,}</strong>
        &nbsp;&nbsp;<span style="color:#6a737d">Total tokens</span> <strong>{total_tok:,}</strong>
      </span>""" if (prem or total_tok) else ""
    elif source == "claude":
        api_cost = _token_cost(tokens)
        inp  = tokens.get("input", 0)
        out  = tokens.get("output", 0)
        cr   = tokens.get("cache_read", 0)
        cc   = tokens.get("cache_creation", 0)
        seat_html = f"""<span style="font-size:10px;font-weight:700;text-transform:uppercase;
                    letter-spacing:0.7px;color:#6a737d;margin-right:10px">Cost</span>
      <span style="font-size:11px;color:#1b1f23">
        <span style="color:#6a737d">Claude seat</span> <strong>$19/mo</strong>
        <span style="font-size:10px;color:#6a737d">(Enterprise, fixed)</span>
      </span>
      &nbsp;&nbsp;&middot;&nbsp;&nbsp;
      <span style="font-size:11px;color:#1b1f23">
        <span style="color:#6a737d">API cost</span> <strong>${api_cost:.4f}</strong>
        <span style="font-size:10px;color:#6a737d">({total_tok:,} tokens)</span>
      </span>"""
        model_html = f"""<span style="font-size:10px;font-weight:700;text-transform:uppercase;
                    letter-spacing:0.7px;color:#6a737d;margin-right:10px">Tokens</span>
      <span style="font-size:11px;color:#1b1f23">
        In <strong>{inp:,}</strong> &nbsp;
        Out <strong>{out:,}</strong> &nbsp;
        Cache-read <strong>{cr:,}</strong> &nbsp;
        Cache-write <strong>{cc:,}</strong>
      </span>""" if total_tok else ""
    else:
        seat_html = f"""<span style="font-size:10px;font-weight:700;text-transform:uppercase;
                    letter-spacing:0.7px;color:#6a737d;margin-right:10px">Cost</span>
      <span style="font-size:11px;color:#1b1f23">
        <span style="color:#6a737d">Copilot seat</span> <strong>$39/mo</strong> +
        <span style="color:#6a737d">Claude seat</span> <strong>$19/mo</strong>
        <span style="font-size:10px;color:#6a737d">(Enterprise, fixed)</span>
      </span>"""
        model_html = ""

    def _row(content):
        return f'<tr><td style="background:#f7f9fc;padding:9px 24px;border:1px solid #dde1e7">{content}</td></tr>'

    inner = f"""{_section_header("By the Numbers", "Cost and AI usage metrics")}"""
    rows  = _row(seat_html)
    if model_html:
        rows += _row(model_html)
    return f"""<tr><td style="background:#ffffff;padding:0;
    border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">{inner}</td></tr>
{rows}"""


def _evidence_section(data: dict, accent: str, vid: str = "") -> str:
    sm = data.get("session_metrics", {})
    if not sm:
        return ""

    pfx = f"{vid}-" if vid else ""
    eid = f"{pfx}evidence"

    # ── Per-project computation ──────────────────────────────────────────────
    proj_rows = []
    total_ai_est = 0.0
    total_det    = 0.0

    for proj, s in sorted(sm.items(), key=lambda x: -x[1].get("tool_invocations", 0)):
        reads      = s.get("reads", 0)
        searches   = s.get("searches", 0)
        edits      = s.get("edits", 0)
        runs       = s.get("runs", 0)
        turns      = s.get("conversation_turns", 0)
        lines_lg   = s.get("lines_logic", s.get("lines_added", 0))
        lines_bp   = s.get("lines_boilerplate", 0)
        files      = s.get("files_touched", 0)
        act_m      = s.get("active_minutes", 0.0)
        itr        = round(edits / max(files, 1), 1) if files else edits
        read_calls = reads + searches
        tool_inv   = s.get("tool_invocations", read_calls + edits + runs)
        day_reqs   = data.get("premium_requests", 0)

        est = _det_est(turns, lines_lg, read_calls, tool_inv, day_reqs)
        det = est["total"]
        total_det += det

        # Match to an AI estimate from goals
        goals_for_proj = [g for g in data.get("goals", [])
                          if (g.get("project") or "") == proj
                          or proj in (g.get("title", "") + g.get("summary", ""))]
        ai_est = sum(g.get("human_hours") or 0 for g in goals_for_proj) if goals_for_proj else None
        if ai_est is not None:
            total_ai_est += ai_est

        proj_rows.append((proj, reads, searches, edits, runs, turns, lines_lg, lines_bp,
                          files, itr, act_m, tool_inv, est, det, ai_est))

    # ── Table header ────────────────────────────────────────────────────────
    th_style = (f'style="padding:5px 8px;font-size:9px;font-weight:700;color:{accent};'
                f'text-transform:uppercase;letter-spacing:0.5px;white-space:nowrap;'
                f'border-bottom:2px solid {accent};background:#f7f9fc"')
    table_header = f"""<tr>
  <th {th_style} style="text-align:left">Project</th>
  <th {th_style}>Tools</th>
  <th {th_style}>Active</th>
  <th {th_style}>Lines</th>
  <th {th_style}>Turns</th>
  <th {th_style}>Files</th>
  <th {th_style}>Iter.</th>
  <th {th_style} style="text-align:right">Det. Est.</th>
  <th {th_style} style="text-align:right;color:#1a7f37">AI Est.</th>
</tr>"""

    # ── Per-project rows ─────────────────────────────────────────────────────
    def _comp_cell(val_h: float, label: str) -> str:
        """Small formula-component cell shown in the signal-hours sub-row."""
        if val_h <= 0:
            return f'<td style="padding:2px 8px 6px;text-align:center;font-size:10px;color:#6a737d">—</td>'
        return (f'<td style="padding:2px 8px 6px;text-align:center;font-size:10px;color:{accent}">'
                f'{_fmt_h(val_h)}<span style="font-size:8px;color:#6a737d;margin-left:2px">({label})</span></td>')

    table_rows = ""
    for (proj, reads, searches, edits, runs, turns, lines_lg, lines_bp,
         files, itr, act_m, tool_inv, est, det, ai_est) in proj_rows:

        th   = est["turns_h"];  rqh = est["reqs_h"]
        lh   = est["lines_h"];  rh  = est["reads_h"];  toh = est["tools_h"]
        inh  = est["interaction_h"]

        tools_breakdown = (f'<div style="font-size:9px;color:#6a737d;margin-top:2px">'
                           f'{reads + searches}r · {edits}e · {runs}x</div>')
        ai_cell    = _fmt_h(ai_est) if ai_est is not None else "—"
        lines_disp = f"+{lines_lg:,}" if lines_lg >= 0 else f"{lines_lg:,}"
        if lines_bp:
            lines_disp += f'<span style="font-size:9px;color:#6a737d"> +{lines_bp:,}bp</span>'
        act_disp   = f"{int(act_m)}m" if act_m else "—"
        files_disp = str(files) if files else "—"
        itr_disp   = str(itr)   if files else "—"

        int_label = f"turns {_fmt_h(th)}" if turns > 0 else f"reqs {_fmt_h(rqh)}"
        formula_parts = f"{int_label} + lines {_fmt_h(lh)} + reads {_fmt_h(rh)} + tools {_fmt_h(toh)}"

        table_rows += f"""<tr style="border-bottom:1px solid #f0f2f5">
  <td style="padding:8px 8px;vertical-align:top;font-size:11px;font-weight:600;color:#1b1f23;max-width:200px">
    {_e(proj)}
    {tools_breakdown}
  </td>
  <td style="padding:8px 8px;text-align:center;font-size:12px;font-weight:700;color:#1b1f23;vertical-align:top">{tool_inv}</td>
  <td style="padding:8px 8px;text-align:center;font-size:11px;color:#6a737d;vertical-align:top">{act_disp}</td>
  <td style="padding:8px 8px;text-align:center;font-size:11px;color:#6a737d;vertical-align:top">{lines_disp}</td>
  <td style="padding:8px 8px;text-align:center;font-size:11px;color:#6a737d;vertical-align:top">{turns}</td>
  <td style="padding:8px 8px;text-align:center;font-size:11px;color:#6a737d;vertical-align:top">{files_disp}</td>
  <td style="padding:8px 8px;text-align:center;font-size:11px;color:#6a737d;vertical-align:top">{itr_disp}</td>
  <td style="padding:8px 8px;text-align:right;font-size:15px;font-weight:700;color:{accent};vertical-align:top;white-space:nowrap">{_fmt_h(det)}</td>
  <td style="padding:8px 8px;text-align:right;font-size:15px;font-weight:700;color:#1a7f37;vertical-align:top;white-space:nowrap">
    {_e(ai_cell)}
  </td>
</tr>
<tr style="border-bottom:1px solid #e8eaf0;background:#fafbfc">
  <td style="padding:2px 8px 6px;font-size:10px;color:#6a737d">formula components</td>
  <td colspan="3" style="padding:2px 8px 6px;font-size:10px;color:#6a737d">{formula_parts}</td>
  <td colspan="5" style="padding:2px 8px 6px;text-align:right;font-size:11px;font-weight:700;color:{accent}">{_fmt_h(det)}</td>
</tr>"""

    total_row = f"""<tr style="background:#f0f2f5;border-top:2px solid {accent}">
  <td colspan="7" style="padding:10px 10px;font-size:12px;font-weight:700;color:#1b1f23;text-align:right">Total</td>
  <td style="padding:10px 10px;text-align:right;font-size:17px;font-weight:700;color:{accent}">{_fmt_h(total_det)}</td>
  <td style="padding:10px 10px;text-align:right;font-size:17px;font-weight:700;color:#1a7f37">{_fmt_h(total_ai_est) if total_ai_est else "—"}</td>
</tr>"""

    # ── Methodology (collapsible) ─────────────────────────────────────────────
    meth_id = f"{pfx}evid-meth"
    methodology = f"""<div id="{meth_id}-hdr" style="cursor:pointer;padding:10px 0 6px;margin-top:10px"
     onclick="toggleDetail('{meth_id}')">
  <span id="{meth_id}-arrow" style="font-size:10px;color:{accent};margin-right:5px">&#9654;</span>
  <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#6a737d">
    How the Effort Estimate Is Calculated</span>
</div>
<div id="{meth_id}-tasks" style="display:none;margin-top:4px">
  <div style="font-size:12px;color:{accent};font-family:monospace;background:#f0f2f5;padding:8px 12px;border-radius:6px;margin-bottom:6px">
    total = interaction_h + lines_h + reads_h + tools_h
  </div>
  <div style="font-size:11px;color:#6a737d;margin-bottom:10px;line-height:1.6">
    Four signals, added together:
    <strong>How deep was the collaboration?</strong> (turns log curve — or premium requests as fallback when turn data unavailable)&nbsp;&nbsp;+&nbsp;&nbsp;
    <strong>How much logic code was written?</strong> (lines in .py/.ts/.go/… — <em>not</em> HTML/CSS/JSON/MD)&nbsp;&nbsp;+&nbsp;&nbsp;
    <strong>How much investigation happened?</strong> (file reads + grep/glob/search — captures research with no code output)&nbsp;&nbsp;+&nbsp;&nbsp;
    <strong>How much execution work ran?</strong> (total tool invocations — browser automation, commands, image processing, non-coding tasks)
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #dde1e7;border-radius:7px;overflow:hidden;margin-bottom:10px">
    <thead><tr style="background:#e8f2fb">
      <th style="padding:6px 10px;text-align:left;font-size:9px;font-weight:700;color:{accent};text-transform:uppercase">Term</th>
      <th style="padding:6px 10px;font-size:9px;font-weight:700;color:{accent};text-transform:uppercase">Formula</th>
      <th style="padding:6px 10px;font-size:9px;font-weight:700;color:{accent};text-transform:uppercase">Scale (sample values)</th>
    </tr></thead>
    <tbody>
      <tr>
        <td style="padding:5px 10px;font-size:11px;font-weight:600">turns_h</td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d;font-family:monospace">max(0, &minus;0.15 + 0.67 &times; ln(turns+1))</td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d">5t→0.9h &nbsp;15t→1.6h &nbsp;30t→2.0h &nbsp;60t→2.5h &nbsp;100t→2.8h</td>
      </tr>
      <tr style="background:#fafbfc">
        <td style="padding:5px 10px;font-size:11px;font-weight:600">reqs_h <span style="font-size:9px;color:#6a737d">[fallback when turns=0]</span></td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d;font-family:monospace">max(0, &minus;0.10 + 0.45 &times; ln(reqs+1))</td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d">3→0.52h &nbsp;8→0.89h &nbsp;15→1.16h &nbsp;30→1.44h &nbsp;60→1.75h</td>
      </tr>
      <tr>
        <td style="padding:5px 10px;font-size:11px;font-weight:600">lines_h</td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d;font-family:monospace">0.40 &times; log&#8322;(lines_logic&divide;100 + 1)</td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d">logic code only &mdash; .py .ts .go .rs .java .sh&hellip; &nbsp;|&nbsp; HTML/CSS/JSON/MD excluded &nbsp;|&nbsp; 100L→0.4h &nbsp;500L→1.0h &nbsp;2000L→1.6h</td>
      </tr>
      <tr style="background:#fafbfc">
        <td style="padding:5px 10px;font-size:11px;font-weight:600">reads_h</td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d;font-family:monospace">0.10 &times; log&#8322;(read_calls + 1)</td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d">file reads + grep/glob/search &nbsp;|&nbsp; 10→+0.35h &nbsp;50→+0.57h &nbsp;100→+0.67h</td>
      </tr>
      <tr>
        <td style="padding:5px 10px;font-size:11px;font-weight:600">tools_h</td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d;font-family:monospace">0.07 &times; log&#8322;(tool_invocations + 1)</td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d">all tool calls &nbsp;|&nbsp; 10→+0.24h &nbsp;50→+0.40h &nbsp;100→+0.47h &nbsp;200→+0.54h &nbsp;500→+0.63h</td>
      </tr>
      <tr style="background:#fafbfc">
        <td style="padding:5px 10px;font-size:11px;font-weight:600;color:{accent}">total</td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d;font-family:monospace">max(interaction_h + lines_h + reads_h + tools_h, 0.25h)</td>
        <td style="padding:5px 10px;font-size:11px;color:#6a737d">floor 0.25h &nbsp;·&nbsp; rounded to nearest 0.25h &nbsp;·&nbsp; OLS-calibrated R²≈0.40 on 48 days</td>
      </tr>
    </tbody>
  </table>
</div>"""

    # ── Outer collapsible wrapper ─────────────────────────────────────────────
    inner = f"""<div id="{eid}-hdr" style="cursor:pointer;padding:10px 24px 6px"
     onclick="toggleDetail('{eid}')">
  <span id="{eid}-arrow" style="font-size:10px;color:{accent};margin-right:5px">&#9654;</span>
  <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#6a737d">
    Estimation Evidence — How These Numbers Were Calculated</span>
</div>
<div id="{eid}-tasks" style="display:none;padding:0 24px 16px">
  <div style="font-size:12px;color:#1b1f23;line-height:1.6;margin-bottom:10px">
    <strong>Why we lead with AI estimation:</strong> The AI reads your full session transcript — every instruction,
    every tool action, every code change — and understands <em>what</em> was accomplished, not just how many
    actions were taken. It distinguishes a 200-line boilerplate scaffold from a 50-line algorithm that required
    deep design thinking. This contextual understanding produces more accurate estimates than counting actions alone.
    The deterministic formula below is shown for transparency.
  </div>
  <div style="font-size:10px;color:#6a737d;margin-bottom:12px">
    &#9632; Det. Est. = interaction_h + lines_h + reads_h + tools_h (deterministic formula) &nbsp;·&nbsp;
    Lines = logic code only (.py/.ts/.go/… — HTML/CSS/JSON/MD excluded) &nbsp;·&nbsp;
    interaction_h = turns_h, or reqs_h when turns unavailable &nbsp;·&nbsp;
    AI Est. = semantic AI analysis
  </div>
  <div style="overflow-x:auto">
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border:1px solid #dde1e7;border-radius:7px;overflow:hidden;min-width:600px">
      <thead>{table_header}</thead>
      <tbody>{table_rows}</tbody>
      <tfoot>{total_row}</tfoot>
    </table>
  </div>
  {methodology}
</div>"""

    return f"""<tr><td style="background:#ffffff;padding:0;
    border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">{inner}</td></tr>"""


# ── Daily heatmap (shared between all-view and per-source views) ───────────────

def _heatmap_row(day_rows: list, heatmap_id: str) -> str:
    """Build a collapsible daily heatmap row from [(date_str, {period: count})] data."""
    if not day_rows:
        return ""

    # Keys must match exactly what analyze.py writes (en-dash \u2013)
    _PERIODS = [
        ("Early Morning (5\u20139am)",  "5–9am"),
        ("Morning (9am\u201312pm)",     "9am–12pm"),
        ("Afternoon (12\u20135pm)",     "12–5pm"),
        ("Evening (5\u20139pm)",        "5–9pm"),
        ("Night (9pm\u20131am)",        "9pm–1am"),
    ]
    _SCALE = ["#dde8f5", "#a8c4e0", "#5a90c8", "#2660a4", "#0d3a6e"]

    all_vals = [v for _, b in day_rows for v in b.values() if v > 0]
    max_val  = max(all_vals) if all_vals else 1

    def _cell_color(n):
        if n == 0: return "#f0f2f5"
        idx = min(int(n / max_val * len(_SCALE)), len(_SCALE) - 1)
        return _SCALE[idx]

    def _text_color(n):
        return "#ffffff" if (n / max_val if max_val else 0) >= 0.5 else "#2d4a6e"

    col_headers = "".join(
        f'<th style="padding:6px 4px;text-align:center;font-size:9px;font-weight:700;'
        f'color:#6a737d;text-transform:uppercase;letter-spacing:0.5px;width:18%">'
        f'{name}<br><span style="font-weight:400;font-size:8px">{sub}</span></th>'
        for name, sub in _PERIODS
    )

    rows_html = ""
    for date_str, buckets in day_rows:
        try:
            dt  = datetime.strptime(date_str, "%Y-%m-%d")
            lbl = (f'<span style="font-size:11px;font-weight:700">{dt.strftime("%b %d")}</span>'
                   f'<br><span style="font-size:9px;color:#8a8a8a">{dt.strftime("%a")}</span>')
        except Exception:
            lbl = date_str

        row_total = sum(buckets.values())
        cells = ""
        for period, _ in _PERIODS:
            n  = buckets.get(period, 0)
            bg = _cell_color(n)
            tc = _text_color(n)
            label = f'<span style="font-size:11px;font-weight:700;color:{tc}">{n}</span>' if n > 0 else ""
            cells += (
                f'<td style="padding:3px 4px">'
                f'<div style="background:{bg};border-radius:6px;height:36px;'
                f'display:flex;align-items:center;justify-content:center">'
                f'{label}</div></td>'
            )
        rows_html += f"""<tr>
  <td style="padding:3px 8px 3px 0;white-space:nowrap;text-align:right">{lbl}</td>
  {cells}
  <td style="padding:3px 0 3px 8px;font-size:10px;color:#6a737d;white-space:nowrap">{row_total}</td>
</tr>"""

    legend = (
        f'<div style="display:flex;justify-content:flex-end;align-items:center;'
        f'gap:4px;margin-top:10px;font-size:9px;color:#8a8a8a">'
        f'Less &nbsp;'
        + "".join(f'<span style="display:inline-block;width:14px;height:14px;background:{c};'
                  f'border-radius:3px;vertical-align:middle"></span>'
                  for c in ["#f0f2f5"] + _SCALE)
        + '&nbsp; More</div>'
    )

    return f"""<tr><td style="padding:0;border-left:1px solid #dde1e7;border-right:1px solid #dde1e7;background:#ffffff">
  <div style="padding:0 24px 4px">
    <button onclick="var h=document.getElementById('{heatmap_id}');var a=document.getElementById('{heatmap_id}-arrow');if(h.style.display==='none'){{h.style.display='block';a.textContent='▼'}}else{{h.style.display='none';a.textContent='▶'}};return false"
            style="background:#f0f4fa;border:1px solid #dde1e7;border-radius:6px;padding:8px 14px;
                   cursor:pointer;font-size:12px;color:#2d6a9f;font-weight:600;width:100%;text-align:left;
                   display:flex;align-items:center;gap:8px">
      <span id="{heatmap_id}-arrow">▶</span>
      See daily breakdown
      <span style="font-weight:400;color:#8a8a8a;font-size:11px">— message counts by time of day</span>
    </button>
    <div id="{heatmap_id}" style="display:none;margin-top:12px;overflow-x:auto">
      <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;min-width:500px">
        <thead><tr>
          <th style="width:70px"></th>{col_headers}<th style="width:30px"></th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      {legend}
    </div>
  </div>
</td></tr>"""


# ── Individual source tab ─────────────────────────────────────────────────────

def _source_view(view_id: str, data: dict | None, source: str,
                 tool_name: str,
                 n_days: int, active_days: int,
                 date_range_str: str,
                 analyses: list | None = None) -> str:
    accent     = ACCENT[source]
    banner_bg  = BANNER_BG[source]
    accent_bg  = ACCENT_BG[source]
    if not data:
        return f"""<div id="view-{view_id}" class="view">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:24px 16px">
<tbody><tr><td align="center">
<table width="960" cellpadding="0" cellspacing="0" style="max-width:960px;width:100%">
<tbody>
<tr><td style="background:#ffffff;padding:40px 24px;text-align:center;
    border:1px solid #dde1e7;border-radius:9px">
  <div style="font-size:15px;color:#6a737d">No {tool_name} activity found in this date range.</div>
</td></tr>
</tbody></table></td></tr></tbody></table></div>"""

    goals        = data.get("goals", [])
    hours        = data.get("human_hours", 0) or sum(g.get("human_hours") or 0 for g in goals)
    active_min_v = int(data.get("active_minutes", 0) or 0)
    def _norm_proj(p: str) -> str:
        import re as _re3
        return _re3.sub(r'[-_ ]+', '-', p.strip().lower()) if p else ""
    projects     = len({_norm_proj(g.get("project","")) for g in goals if g.get("project")})
    sessions     = data.get("sessions_count", 0)
    headline     = data.get("headline", f"{tool_name} activity")

    # Show year only in the banner sub-line (e.g. "2026")
    _year_str = date_range_str[:4] if len(date_range_str) >= 4 else date_range_str

    header_row = f"""<tr>
  <td style="background:{banner_bg};border-radius:9px 9px 0 0;padding:22px 24px">
    <div style="font-size:10px;color:rgba(255,255,255,0.6);letter-spacing:1.2px;
                text-transform:uppercase;margin-bottom:4px">
      {_e(date_range_str)} &nbsp;·&nbsp; {_e(tool_name)} Impact Report
    </div>
    <div style="font-size:20px;font-weight:700;color:#fff;line-height:1.3">
      {_e(active_days)} active day{"s" if active_days!=1 else ""}
      ({_e(_year_str)}):
      {_e(projects)} project{"s" if projects!=1 else ""} delivered
    </div>
  </td>
</tr>"""

    hint_row = f"""<tr>
  <td style="background:#ffffff;padding:7px 24px;
             border-left:1px solid #dde1e7;border-right:1px solid #dde1e7;
             border-bottom:1px solid #dde1e7">
    <span style="font-size:10px;color:#6a737d">
      Run with <code style="font-size:10px;background:#f6f8fa;padding:1px 4px;border-radius:3px">--email</code>
      to send this report via Outlook
    </span>
  </td>
</tr>"""

    narrative = _narrative_row(data, accent, vid=view_id)
    kpis      = _kpi_row(data, source, active_days, vid=view_id)
    roi       = _roi_row(hours, n_days, source, active_min=active_min_v)
    goals_sec = _goals_section(goals, source, show_date=True, vid=view_id, session_metrics=data.get("session_metrics", {}))
    produced  = _produced_section(data, accent)
    skills    = _skills_section(goals, accent, tool_name)
    collab    = _collab_section(data, source, tool_name)
    timing    = _timing_section(data, source, tool_name)
    numbers   = _numbers_section(data, source, n_days)
    evidence  = _evidence_section(data, accent, vid=view_id)

    # Per-source daily heatmap
    _PERIODS_KEYS = ["Early Morning (5\u20139am)", "Morning (9am\u201312pm)", "Afternoon (12\u20135pm)", "Evening (5\u20139pm)", "Night (9pm\u20131am)"]
    heatmap_day_rows = []
    if analyses:
        for a in sorted(analyses, key=lambda x: x["date"]):
            src_data = a.get(source) or {}
            tb = src_data.get("time_buckets") or {}
            if sum(tb.get(p, 0) for p in _PERIODS_KEYS) == 0:
                continue
            heatmap_day_rows.append((a["date"], {p: tb.get(p, 0) for p in _PERIODS_KEYS}))
    heatmap = _heatmap_row(heatmap_day_rows, f"{view_id}-heatmap")

    footer = f"""<tr>
  <td style="background:#ffffff;padding:12px 24px;
             border:1px solid #dde1e7;border-radius:0 0 9px 9px;
             text-align:center">
    <span style="font-size:10px;color:#6a737d">
      Generated by What I Did AI &nbsp;·&nbsp; {datetime.now().strftime("%Y-%m-%d %H:%M")}
    </span>
  </td>
</tr>"""

    return f"""<div id="view-{view_id}" class="view">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:24px 16px">
<tbody><tr><td align="center">
<table width="960" cellpadding="0" cellspacing="0" style="max-width:960px;width:100%">
<tbody>
{header_row}
{hint_row}
{narrative}
{kpis}
{roi}
{goals_sec}
{produced}
{skills}
{collab}
{timing}
{heatmap}
{numbers}
{evidence}
{footer}
</tbody></table></td></tr></tbody></table>
</div>"""


# ── Aggregate "All" tab ───────────────────────────────────────────────────────

def _top_projects_card(goals: list, source: str, tool_name: str, top_n: int = 5) -> str:
    accent   = ACCENT[source]
    bg       = ACCENT_BG.get(source, "#e8f2fb")
    border   = ACCENT.get(source, ACCENT["copilot"])
    extra_id = f"{source}-summary-extra"
    more_id  = f"{source}-summary-more"

    # Sort goals by hours — same order as detail tab goal rows
    sorted_goals = sorted(goals, key=lambda g: -(g.get("human_hours") or 0))

    if not sorted_goals:
        return f"""<td style="width:50%;padding:8px;vertical-align:top">
  <div style="background:#fff;border:1px solid #dde1e7;border-radius:9px;padding:16px;
              border-top:3px solid {border}">
    <div style="font-size:11px;font-weight:700;color:#6a737d;text-transform:uppercase;
                letter-spacing:0.8px;margin-bottom:10px">{_e(tool_name)} — Top Projects</div>
    <div style="font-size:12px;color:#999">No activity found.</div>
  </div>
</td>"""

    def _goal_row(g: dict, idx: int) -> str:
        title   = g.get("title", "") or g.get("project", "") or "Unknown"
        hours   = g.get("human_hours") or 0
        date    = g.get("_date", "")
        tasks   = g.get("tasks", [])
        summary = g.get("summary", "")
        # Collect domain skills only (no tech/language badges)
        skills: list[str] = []
        for t in tasks:
            for s in t.get("domain_skills", [])[:2]:
                if s not in skills:
                    skills.append(s)
        skills = skills[:3]
        skill_pills = "".join(
            f'<span style="background:{bg};color:{accent};padding:1px 7px;border-radius:8px;'
            f'font-size:10px;font-weight:600;margin:1px 2px 1px 0;display:inline-block">{_e(s)}</span>'
            for s in skills
        )
        date_badge = (
            f'<span style="font-size:10px;color:{accent};background:{bg};padding:1px 6px;'
            f'border-radius:7px;margin-right:5px;white-space:nowrap">{_e(date[5:])}</span>'
            if date else ""
        )
        summary_html = (
            f'<div style="font-size:11px;color:#6a737d;margin-bottom:4px">{_e(summary)}</div>'
            if summary else ""
        )
        return (
            f'<div style="padding:9px 0;border-bottom:1px solid #f0f2f5">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:3px">'
            f'<div style="font-size:12px;font-weight:700;color:#1b1f23;flex:1;min-width:0;padding-right:8px">'
            f'<span style="color:{accent};font-weight:700;margin-right:5px">{idx}.</span>'
            f'{date_badge}{_e(title)}</div>'
            f'<div style="font-size:13px;font-weight:700;color:{accent};white-space:nowrap">{_fmt_h(hours)}</div>'
            f'</div>{summary_html}<div>{skill_pills}</div></div>'
        )

    visible_html = "".join(_goal_row(g, i + 1)         for i, g in enumerate(sorted_goals[:top_n]))
    extra_goals  = sorted_goals[top_n:]
    extra_html   = ""
    if extra_goals:
        extra_rows = "".join(_goal_row(g, top_n + i + 1) for i, g in enumerate(extra_goals))
        extra_html = (
            f'<div id="{extra_id}" style="display:none">{extra_rows}</div>'
            f'<div style="padding:10px 0 4px;text-align:center">'
            f'<span id="{more_id}" onclick="toggleExtraGoals(\'{extra_id}\',\'{more_id}\',{len(extra_goals)})"'
            f' style="cursor:pointer;font-size:11px;color:{accent};font-weight:600">'
            f'&#9654; See {len(extra_goals)} more</span></div>'
        )

    return f"""<td style="width:50%;padding:8px;vertical-align:top">
  <div style="background:#fff;border:1px solid #dde1e7;border-radius:9px;padding:16px;
              border-top:3px solid {border}">
    <div style="font-size:11px;font-weight:700;color:{accent};text-transform:uppercase;
                letter-spacing:0.8px;margin-bottom:10px">{_e(tool_name)} — Top Projects</div>
    {visible_html}
    {extra_html}
  </div>
</td>"""


def _all_view(copilot_agg: dict | None, claude_agg: dict | None,
              n_days: int, date_range_str: str, include_copilot: bool, include_claude: bool,
              analyses: list | None = None) -> str:
    c_hours  = _sum_goal_hours(copilot_agg) if include_copilot else 0
    cl_hours = _sum_goal_hours(claude_agg)  if include_claude  else 0
    total_h  = c_hours + cl_hours

    c_lines  = (copilot_agg or {}).get("lines_added", 0) or 0
    cl_lines = (claude_agg or {}).get("lines_added", 0) or 0
    total_lines = c_lines + cl_lines

    c_days  = (copilot_agg or {}).get("active_days", 0) or 0
    cl_days = (claude_agg or {}).get("active_days", 0) or 0

    c_prs   = len((copilot_agg or {}).get("pull_requests", []))
    cl_prs  = len((claude_agg  or {}).get("pull_requests", []))
    total_prs = c_prs + cl_prs

    c_sess  = (copilot_agg or {}).get("sessions_count", 0) or 0
    cl_sess = (claude_agg  or {}).get("sessions_count", 0) or 0
    total_sess = c_sess + cl_sess

    c_goals_list  = (copilot_agg or {}).get("goals", [])
    cl_goals_list = (claude_agg  or {}).get("goals", [])
    c_n_goals  = len(c_goals_list)
    cl_n_goals = len(cl_goals_list)
    total_proj = c_n_goals + cl_n_goals   # total work items across both tools

    c_active_min  = int((copilot_agg or {}).get("active_minutes", 0) or 0)
    cl_active_min = int((claude_agg  or {}).get("active_minutes", 0) or 0)

    def _fmt_active(m: int) -> str:
        if m >= 60:
            return f"{m // 60}h {m % 60}m"
        return f"{m}m"

    def _agg_card(value, label, sub="", accent="#0078d4", border_top="",
                  bg="#fff", val_color=None, lbl_color="#6a737d", sub_color="#6a737d"):
        """Uniform KPI card — no icons, consistent height."""
        sub_html     = f'<div style="font-size:10px;color:{sub_color};margin-top:4px;line-height:1.4">{sub}</div>' if sub else ""
        border_style = f"border-top:3px solid {border_top};" if border_top else ""
        v_color      = val_color or accent
        return f"""<td style="padding:5px;vertical-align:top">
  <div style="background:{bg};border:1px solid #dde1e7;border-radius:10px;{border_style}
              padding:16px 8px;text-align:center;height:88px;box-sizing:border-box;
              display:flex;flex-direction:column;justify-content:center;
              box-shadow:0 1px 4px rgba(0,0,0,0.06)">
    <div style="font-size:22px;font-weight:700;color:{v_color};line-height:1;letter-spacing:-0.5px">{_e(value)}</div>
    <div style="font-size:9px;font-weight:700;color:{lbl_color};text-transform:uppercase;
                letter-spacing:0.8px;margin-top:6px;line-height:1.3">{_e(label)}</div>
    {sub_html}
  </div>
</td>"""

    # Human effort card: Copilot/Claude split as sub-text
    hours_split_parts = []
    if include_copilot and c_hours:
        hours_split_parts.append(f'<span style="color:{ACCENT["copilot"]};font-weight:600">{_fmt_h(c_hours)} Copilot</span>')
    if include_claude and cl_hours:
        hours_split_parts.append(f'<span style="color:{ACCENT["claude"]};font-weight:600">{_fmt_h(cl_hours)} Claude</span>')
    hours_split_html = " &nbsp;+&nbsp; ".join(hours_split_parts)

    # Dark charcoal for overall summary cards — distinct from Copilot (blue) / Claude (purple)
    _dk = "#24292f"

    kpi_row = f"""<tr><td style="background:#f0f2f5;padding:12px 24px;
    border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">
  <table width="100%" cellpadding="0" cellspacing="0"><tbody><tr>
    {_agg_card(total_proj, "Work Items Delivered", f"{total_sess} sessions",
               bg=_dk, val_color="#ffffff", lbl_color="rgba(255,255,255,0.55)", sub_color="rgba(255,255,255,0.4)")}
    {_agg_card(_fmt_h(total_h), "Human Effort Equivalent", hours_split_html,
               bg=_dk, val_color="#ffffff", lbl_color="rgba(255,255,255,0.55)", sub_color="rgba(255,255,255,0.4)")}
    {_agg_card(_fmt_h(c_hours), "Copilot Human Est.",
               f"{c_n_goals} goals · {c_sess} sessions", ACCENT["copilot"], ACCENT["copilot"]) if include_copilot else ""}
    {_agg_card(_fmt_active(c_active_min), "Copilot Active Time",
               "engaged time with AI", ACCENT["copilot"], ACCENT["copilot"]) if include_copilot else ""}
    {_agg_card(_fmt_h(cl_hours), "Claude Human Est.",
               f"{cl_n_goals} goals · {cl_sess} sessions", ACCENT["claude"], ACCENT["claude"]) if include_claude else ""}
    {_agg_card(_fmt_active(cl_active_min), "Claude Active Time",
               "engaged time with AI", ACCENT["claude"], ACCENT["claude"]) if include_claude else ""}
  </tr></tbody></table>
</td></tr>"""

    # Combined ROI + Speed Multiplier
    seat_cost = 0
    if include_copilot:
        seat_cost += COPILOT_SEAT_DAILY * n_days
    if include_claude:
        seat_cost += CLAUDE_SEAT_DAILY * n_days
    combined_monthly  = COPILOT_SEAT_MONTHLY + CLAUDE_SEAT_MONTHLY
    leverage          = round(total_h * HOURLY_RATE / max(seat_cost, 0.01)) if seat_cost else 0
    total_active_min  = c_active_min + cl_active_min
    total_active_h    = total_active_min / 60
    all_speed_str     = f"{round(total_h / total_active_h * 10) / 10:.1f}\u00d7" if total_active_h >= 0.5 else None

    if all_speed_str:
        all_metrics_html = f"""
      <div style="display:flex;justify-content:center;align-items:center;gap:48px">
        <div style="text-align:center">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;
                      color:rgba(255,255,255,0.55);margin-bottom:6px">Return on Investment</div>
          <div style="font-size:52px;font-weight:800;color:#ffffff;line-height:1;letter-spacing:-2px">{leverage}x</div>
          <div style="font-size:11px;color:rgba(255,255,255,0.55);margin-top:4px">value on seat cost</div>
        </div>
        <div style="width:1px;height:60px;background:rgba(255,255,255,0.2)"></div>
        <div style="text-align:center">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;
                      color:rgba(255,255,255,0.55);margin-bottom:6px">Speed Multiplier</div>
          <div style="font-size:52px;font-weight:800;color:#ffffff;line-height:1;letter-spacing:-2px">{all_speed_str}</div>
          <div style="font-size:11px;color:rgba(255,255,255,0.55);margin-top:4px">vs. unassisted expert</div>
        </div>
      </div>"""
    else:
        all_metrics_html = f"""
      <div style="text-align:center">
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;
                    color:rgba(255,255,255,0.65);margin-bottom:8px">Return on Investment</div>
        <div style="font-size:52px;font-weight:800;color:#ffffff;line-height:1;letter-spacing:-2px">{leverage}x</div>
        <div style="font-size:11px;color:rgba(255,255,255,0.55);margin-top:4px">value on seat cost</div>
      </div>"""

    roi_row = f"""<tr><td style="padding:0;border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:linear-gradient(135deg,#1a1a2e,#0F3460);border-collapse:collapse">
    <tbody><tr><td style="padding:22px 48px">
      {all_metrics_html}
      {_roi_assumption_note("combined")}
    </td></tr></tbody>
  </table>
</td></tr>"""

    # Top projects grid
    c_goals  = (copilot_agg or {}).get("goals", [])
    cl_goals = (claude_agg  or {}).get("goals", [])
    projects_row = f"""<tr><td style="background:#f0f2f5;padding:12px 24px;
    border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">
  <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;
              color:#6a737d;margin-bottom:10px">Top Projects by Tool</div>
  <table width="100%" cellpadding="0" cellspacing="0"><tbody><tr>
    {_top_projects_card(c_goals,  "copilot", "GitHub Copilot") if include_copilot else ""}
    {_top_projects_card(cl_goals, "claude",  "Claude Code")    if include_claude  else ""}
  </tr></tbody></table>
</td></tr>"""

    # Combined data dict for shared sections (skills, collab, timing)
    combined_goals = list(c_goals) + list(cl_goals)

    def _sum_dicts(*aggs, field):
        merged: dict = {}
        for agg in aggs:
            for k, v in (agg or {}).get(field, {}).items():
                merged[k] = merged.get(k, 0) + v
        return merged

    combined_data = {
        "goals":            combined_goals,
        "quality_modes":    _sum_dicts(copilot_agg, claude_agg, field="quality_modes"),
        "time_buckets":     _sum_dicts(copilot_agg, claude_agg, field="time_buckets"),
        "time_buckets_all": _sum_dicts(copilot_agg, claude_agg, field="time_buckets_all"),
        "active_minutes":   ((copilot_agg or {}).get("active_minutes", 0)
                           + (claude_agg  or {}).get("active_minutes", 0)),
    }

    skills_row = _skills_section(combined_goals, "#2d6a9f")  # neutral slate-blue for combined view
    collab_row = _collab_comparison_section(
        copilot_modes=(copilot_agg or {}).get("quality_modes", {}),
        claude_modes=(claude_agg   or {}).get("quality_modes", {}),
        copilot_active_min=(copilot_agg or {}).get("active_minutes", 0),
        claude_active_min=(claude_agg   or {}).get("active_minutes", 0),
    ) if include_copilot and include_claude else _collab_section(combined_data, "copilot", "AI tools")
    timing_row = _timing_section(
        combined_data, "copilot", "AI tools",
        copilot_buckets=(copilot_agg or {}).get("time_buckets", {}),
        claude_buckets=(claude_agg   or {}).get("time_buckets", {}),
        copilot_buckets_all=(copilot_agg or {}).get("time_buckets_all", {}),
        claude_buckets_all=(claude_agg   or {}).get("time_buckets_all", {}),
        copilot_active_minutes=(copilot_agg or {}).get("active_minutes", 0),
        claude_active_minutes=(claude_agg   or {}).get("active_minutes", 0),
    )

    header = f"""<tr>
  <td style="background:linear-gradient(135deg,#1a1a2e,#0F3460);border-radius:9px 9px 0 0;padding:22px 24px">
    <div style="font-size:10px;color:rgba(255,255,255,0.6);letter-spacing:1.2px;
                text-transform:uppercase;margin-bottom:4px">
      {_e(date_range_str)} &nbsp;·&nbsp; GitHub Copilot + Claude Combined Report
    </div>
    <div style="font-size:20px;font-weight:700;color:#fff;line-height:1.3">
      {_fmt_h(total_h)} of human effort equivalent assistance provided by AI across {total_proj} projects
    </div>
  </td>
</tr>"""

    # ── Token consumption section ──────────────────────────────────────────────
    def _tok(agg):
        return (agg or {}).get("tokens", {})

    def _prem(agg):
        return (agg or {}).get("premium_requests", 0) or 0

    c_tok  = _tok(copilot_agg)
    cl_tok = _tok(claude_agg)

    def _fmt_tok(n):
        if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
        if n >= 1_000:     return f"{n/1_000:.0f}K"
        return str(n)

    def _tok_card(label, cop_val, cla_val, cop_color, cla_color, note=""):
        note_html = f'<div style="font-size:9px;color:#8a8a8a;margin-top:3px;font-style:italic">{note}</div>' if note else ""
        cop_html = (f'<div style="font-size:13px;font-weight:700;color:{cop_color}">{_fmt_tok(cop_val)}</div>'
                    f'<div style="font-size:9px;color:#6a737d;text-transform:uppercase;letter-spacing:0.5px">Copilot</div>') if include_copilot else ""
        cla_html = (f'<div style="font-size:13px;font-weight:700;color:{cla_color}">{_fmt_tok(cla_val)}</div>'
                    f'<div style="font-size:9px;color:#6a737d;text-transform:uppercase;letter-spacing:0.5px">Claude</div>') if include_claude else ""
        divider  = '<div style="width:1px;background:#e1e4e8;margin:0 12px;align-self:stretch"></div>' if include_copilot and include_claude else ""
        return f"""<td style="padding:6px;vertical-align:top">
  <div style="background:#ffffff;border:1px solid #dde1e7;border-radius:9px;
              padding:12px 14px;text-align:center;min-height:80px;
              box-shadow:0 1px 3px rgba(0,0,0,0.05)">
    <div style="font-size:9px;font-weight:700;color:#6a737d;text-transform:uppercase;
                letter-spacing:0.8px;margin-bottom:8px">{_e(label)}</div>
    <div style="display:flex;justify-content:center;align-items:center">
      {cop_html}{divider}{cla_html}
    </div>
    {note_html}
  </div>
</td>"""

    c_inp  = c_tok.get("input", 0);          cl_inp  = cl_tok.get("input", 0)
    c_out  = c_tok.get("output", 0);         cl_out  = cl_tok.get("output", 0)
    c_cr   = c_tok.get("cache_read", 0);     cl_cr   = cl_tok.get("cache_read", 0)
    c_cc   = c_tok.get("cache_creation", 0); cl_cc   = cl_tok.get("cache_creation", 0)
    c_prem = _prem(copilot_agg);             cl_prem = _prem(claude_agg)

    cop_acc = ACCENT["copilot"]; cla_acc = ACCENT["claude"]

    token_row = f"""<tr><td style="padding:0;border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">
  {_section_header("Token Consumption", "AI tokens used across both tools for this period")}
  <div style="background:#f8f9fb;padding:10px 24px 16px">
    <table width="100%" cellpadding="0" cellspacing="0"><tbody><tr>
      {_tok_card("Input Tokens",    c_inp,  cl_inp,  cop_acc, cla_acc, "prompts & context sent to AI")}
      {_tok_card("Output Tokens",   c_out,  cl_out,  cop_acc, cla_acc, "AI-generated response tokens")}
      {_tok_card("Cache Read",      c_cr,   cl_cr,   cop_acc, cla_acc, "retrieved from prompt cache")}
      {_tok_card("Cache Creation",  c_cc,   cl_cc,   cop_acc, cla_acc, "written to prompt cache")}
      {_tok_card("Premium Requests", c_prem, cl_prem, cop_acc, cla_acc, "requests using premium quota")}
    </tr></tbody></table>
  </div>
</td></tr>"""

    # ── Daily heatmap ─────────────────────────────────────────────────────────
    _PERIODS_KEYS = ["Early Morning (5\u20139am)", "Morning (9am\u201312pm)", "Afternoon (12\u20135pm)", "Evening (5\u20139pm)", "Night (9pm\u20131am)"]
    all_day_rows = []
    if analyses:
        for a in sorted(analyses, key=lambda x: x["date"]):
            buckets = {}
            for src in ("copilot", "claude"):
                tb = (a.get(src) or {}).get("time_buckets") or {}
                for p in _PERIODS_KEYS:
                    buckets[p] = buckets.get(p, 0) + tb.get(p, 0)
            if sum(buckets.values()) > 0:
                all_day_rows.append((a["date"], buckets))
    heatmap_row = _heatmap_row(all_day_rows, "all-heatmap")

    footer = f"""<tr>
  <td style="background:#ffffff;padding:12px 24px;
             border:1px solid #dde1e7;border-radius:0 0 9px 9px;text-align:center">
    <span style="font-size:10px;color:#6a737d">
      Generated by What I Did AI &nbsp;·&nbsp; {datetime.now().strftime("%Y-%m-%d %H:%M")} &nbsp;·&nbsp;
      Switch tabs for full detail per tool
    </span>
  </td>
</tr>"""

    return f"""<div id="view-all" class="view">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:24px 16px">
<tbody><tr><td align="center">
<table width="960" cellpadding="0" cellspacing="0" style="max-width:960px;width:100%">
<tbody>
{header}
{kpi_row}
{roi_row}
{skills_row}
{collab_row}
{timing_row}
{token_row}
{footer}
</tbody></table></td></tr></tbody></table>
</div>"""


# ── Tab shell ─────────────────────────────────────────────────────────────────

def generate_report(
    analyses: list,
    include_copilot: bool = True,
    include_claude:  bool = True,
) -> str:
    n_days = len(analyses)
    dates  = sorted(a["date"] for a in analyses)
    date_range_str = dates[0] if len(dates) == 1 else f"{dates[0]}_to_{dates[-1]}"

    copilot_agg = _agg(analyses, "copilot") if include_copilot else None
    claude_agg  = _agg(analyses, "claude")  if include_claude  else None

    c_hours  = _sum_goal_hours(copilot_agg)
    cl_hours = _sum_goal_hours(claude_agg)

    c_days  = (copilot_agg or {}).get("active_days", 0)
    cl_days = (claude_agg  or {}).get("active_days", 0)

    show_all = include_copilot and include_claude
    default_tab = "all" if show_all else ("copilot" if include_copilot else "claude")

    # Format date range for masthead: "Mar 15 – Apr 13, 2026"
    def _fmt_date_pretty(d: str) -> str:
        try:
            from datetime import datetime as _dt
            dt = _dt.strptime(d, "%Y-%m-%d")
            return dt.strftime("%b %-d") if _platform.system() != "Windows" else dt.strftime("%b %d").lstrip("0").replace(" 0", " ")
        except Exception:
            return d
    import platform as _platform
    d0, d1 = dates[0], dates[-1]
    if d0 == d1:
        date_pretty = _fmt_date_pretty(d0) + ", " + d0[:4]
    else:
        y0, y1 = d0[:4], d1[:4]
        date_pretty = (_fmt_date_pretty(d0) + " – " + _fmt_date_pretty(d1) +
                       (f", {y1}" if y0 == y1 else f", {y0} – {y1}"))
    n_days_str = f"{len(dates)} day{'s' if len(dates) != 1 else ''}"
    total_h_str = _fmt_h(c_hours + cl_hours)

    tab_buttons = ""
    if show_all:
        tab_buttons += (
            f'<button class="tab-btn" id="tab-all" onclick="showTab(\'all\')">'
            f'<span class="tab-label">Summary</span>'
            f'<span class="tab-sub">{_fmt_h(c_hours + cl_hours)}</span>'
            f'</button>'
        )
    if include_copilot:
        tab_buttons += (
            f'<button class="tab-btn" id="tab-copilot" onclick="showTab(\'copilot\')">'
            f'<span class="tab-label">GitHub Copilot</span>'
            f'<span class="tab-sub">{_fmt_h(c_hours)}</span>'
            f'</button>'
        )
    if include_claude:
        tab_buttons += (
            f'<button class="tab-btn" id="tab-claude" onclick="showTab(\'claude\')">'
            f'<span class="tab-label">Claude</span>'
            f'<span class="tab-sub">{_fmt_h(cl_hours)}</span>'
            f'</button>'
        )

    all_view     = _all_view(copilot_agg, claude_agg, n_days, date_range_str,
                             include_copilot, include_claude, analyses) if show_all else ""
    copilot_view = _source_view("copilot", copilot_agg, "copilot",
                                "GitHub Copilot",
                                n_days, c_days, date_range_str, analyses) if include_copilot else ""
    claude_view  = _source_view("claude",  claude_agg,  "claude",
                                "Claude Code",
                                n_days, cl_days, date_range_str, analyses) if include_claude else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>What I Did AI — {_e(date_range_str)}</title>
<script>
function toggleDetail(id) {{
  var tasks = document.getElementById(id + '-tasks');
  var arrow = document.getElementById(id + '-arrow');
  var hdr   = document.getElementById(id + '-hdr');
  if (!tasks) return;
  var openDisplay = tasks.tagName.toLowerCase() === 'tr' ? 'table-row' : 'block';
  var open = tasks.style.display === openDisplay;
  tasks.style.display  = open ? 'none' : openDisplay;
  if (hdr) hdr.style.background = open ? '' : '#e8f2fb';
  if (arrow) arrow.innerHTML = open ? '&#9654;' : '&#9660;';
}}
function toggleExtraGoals(extraId, btnId, count) {{
  var extra = document.getElementById(extraId);
  var btn   = document.getElementById(btnId);
  if (!extra) return;
  var showing = extra.style.display !== 'none';
  extra.style.display = showing ? 'none' : '';
  if (btn) btn.innerHTML = showing
    ? '&#9654; Show ' + count + ' more project' + (count === 1 ? '' : 's')
    : '&#9660; Show fewer';
}}
function showTab(tab) {{
  document.querySelectorAll('.view').forEach(function(v) {{ v.style.display = 'none'; }});
  document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
  var v = document.getElementById('view-' + tab);
  var b = document.getElementById('tab-' + tab);
  if (v) v.style.display = 'block';
  if (b) b.classList.add('active');
}}
window.onload = function() {{
  showTab('{default_tab}');
  var hint = document.getElementById('expand-hint');
  if (hint) hint.style.display = 'block';
}};
</script>
<style>
body {{ margin:0;padding:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif; }}
.masthead {{
  position:sticky; top:0; z-index:100;
  background:#0d1117;
  border-bottom:1px solid #21262d;
  box-shadow:0 2px 8px rgba(0,0,0,0.35);
}}
.masthead-top {{
  display:flex; align-items:center; justify-content:space-between;
  padding:7px 24px 6px;
  max-width:960px; margin:0 auto;
}}
.masthead-brand {{
  display:flex; align-items:center; gap:10px;
}}
.masthead-title {{
  font-size:15px; font-weight:800; color:#ffffff;
  letter-spacing:-0.3px; white-space:nowrap;
}}
.masthead-title span {{
  background:linear-gradient(90deg,#a78bfa,#60a5fa);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text;
}}
.masthead-tagline {{
  font-size:10px; color:rgba(255,255,255,0.35);
  border-left:1px solid rgba(255,255,255,0.15);
  padding-left:10px; font-style:italic;
}}
.masthead-meta {{
  display:flex; align-items:center; gap:8px;
}}
.masthead-pill {{
  font-size:10px; font-weight:500; color:rgba(255,255,255,0.45);
  background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1);
  border-radius:20px; padding:2px 9px; white-space:nowrap;
}}
.masthead-total {{
  font-size:12px; font-weight:700; color:rgba(255,255,255,0.8);
  white-space:nowrap;
}}
.masthead-total-sub {{
  font-size:9px; color:rgba(255,255,255,0.3);
}}
.tab-bar-wrap {{
  background:#161b22; border-top:1px solid #21262d;
}}
.tab-bar {{
  display:flex; align-items:center; padding:0 16px;
  gap:4px; max-width:960px; margin:0 auto;
}}
.tab-btn {{
  padding:7px 16px; font-size:12px; font-weight:500;
  color:rgba(255,255,255,0.45);
  background:none; border:none; border-bottom:2px solid transparent;
  cursor:pointer; transition:all 0.15s;
  display:flex; align-items:center; gap:6px; white-space:nowrap;
  border-radius:0;
}}
.tab-btn:hover {{ color:rgba(255,255,255,0.8); background:rgba(255,255,255,0.05); }}
.tab-label {{ font-size:12px; font-weight:600; line-height:1; }}
.tab-sub {{
  font-size:10px; font-weight:400; color:rgba(255,255,255,0.35);
  background:rgba(255,255,255,0.08); border-radius:10px;
  padding:1px 7px;
}}
.tab-btn.active {{ color:#fff; border-bottom:2px solid currentColor; }}
#tab-all.active {{ color:#c4b5fd; }}
#tab-all.active .tab-sub {{ background:rgba(167,139,250,0.15); color:#c4b5fd; }}
#tab-copilot.active {{ color:#b084f7; }}
#tab-copilot.active .tab-sub {{ background:rgba(133,52,243,0.15); color:#b084f7; }}
#tab-claude.active {{ color:#f0a080; }}
#tab-claude.active .tab-sub {{ background:rgba(222,115,86,0.15); color:#f0a080; }}
.view {{ display:none; }}
</style>
</head>
<body>
<div class="masthead">
  <div class="masthead-top">
    <div class="masthead-brand">
      <div class="masthead-title">What I Did <span>AI</span></div>
      <div class="masthead-tagline">Analytics report on how you used AI in your work</div>
    </div>
    <div class="masthead-meta">
      <div class="masthead-pill">{date_pretty}</div>
      <div class="masthead-pill">{n_days_str}</div>
      <div class="masthead-pill" style="color:rgba(255,255,255,0.7);font-weight:700">{total_h_str} human equiv.</div>
    </div>
  </div>
  <div class="tab-bar-wrap">
    <div class="tab-bar">
      {tab_buttons}
    </div>
  </div>
</div>
{all_view}
{copilot_view}
{claude_view}
</body>
</html>"""
