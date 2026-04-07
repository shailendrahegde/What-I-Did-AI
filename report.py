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
ACCENT = {
    "copilot":  "#0078d4",
    "claude":   "#7B2FBE",
    "combined": "#0078d4",
}
ACCENT_BG = {
    "copilot":  "#e8f2fb",
    "claude":   "#f3eafa",
    "combined": "#e8f2fb",
}
ROI_BG = {
    "copilot":  "linear-gradient(135deg,#1a7f37,#15803d)",
    "claude":   "linear-gradient(135deg,#5a1a9f,#7B2FBE)",
    "combined": "linear-gradient(135deg,#1a7f37,#15803d)",
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
    ("Builder",             "🏗",  "Writing code, generating files",         "#0078d4", True,
     ["Building"]),
    ("Research assistant",  "🔬",  "Exploring options, investigating",        "#1a7f37", True,
     ["Researching", "Investigating"]),
    ("Creative partner",    "🎨",  "Design, strategy, architecture",          "#7b1fa2", True,
     ["Designing", "Planning"]),
    ("Refinement partner",  "✨",  "Iterating, polishing, improving",         "#1565c0", True,
     ["Iterating"]),
    ("Grunt work handled",  "⚡",  "Git ops, config, installs, routine",      "#6a737d", False,
     ["Shipping", "Configuring", "Navigating"]),
    ("Needed hand-holding", "🔧",  "Errors, retries, course-correcting AI",   "#e65100", False,
     ["Testing"]),
]


# ── Effort-estimation helpers (deterministic formula) ─────────────────────────

def _tools_h(reads: int, edits: int, runs: int) -> float:
    return (reads * 0.3 + edits * 1.5 + runs * 0.75) / 60


def _turns_h(turns: int) -> float:
    if turns <= 0:  return 0.0
    if turns <= 3:  return 0.25
    if turns <= 8:  return 0.75
    if turns <= 15: return 1.5
    if turns <= 30: return 3.0
    if turns <= 60: return 5.0
    return 8.0 + (turns - 60) * 0.1


def _active_h(minutes: float) -> float:
    return round(minutes * 4 / 60, 2)


def _lines_h(lines: int) -> float:
    if lines <= 0:   return 0.0
    if lines <= 50:  return 0.25
    if lines <= 150: return 0.75
    if lines <= 300: return 1.5
    if lines <= 500: return 2.5
    return 4.0 + (lines - 500) / 500


def _complexity_mult(turns: int, files: int, edits: int) -> float:
    mult = 1.0
    if turns > 15: mult += 0.15
    if turns > 40: mult += 0.20
    itr = edits / max(files, 1)
    if itr > 5:  mult += 0.15
    if itr > 12: mult += 0.20
    if files > 3:  mult += 0.10
    if files > 10: mult += 0.20
    return min(round(mult, 2), 1.5)


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
        return "/".join(parts[:2]).lower()

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
            else:
                for k in sm:
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
    }


# ── Section builders ──────────────────────────────────────────────────────────

def _section_header(title: str, subtitle: str = "", extra: str = "") -> str:
    sub = f'<div style="font-size:11px;color:rgba(255,255,255,0.5);margin-top:2px">{_e(subtitle)}</div>' if subtitle else ""
    return f"""<table width="100%" cellpadding="0" cellspacing="0"><tbody><tr>
<td style="background:linear-gradient(135deg,#24292f,#1b1f23);padding:10px 24px">
  {extra}<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;
              color:rgba(255,255,255,0.7)">{_e(title)}</div>{sub}
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
  <div style="background:#ffffff;border:1px solid #dde1e7;border-radius:10px;
              padding:14px 8px;text-align:center;min-height:90px;
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

    return f"""<tr><td style="background:#f0f2f5;padding:10px 24px;
    border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">
  <table width="100%" cellpadding="0" cellspacing="0"><tbody><tr>
    {_card(_fmt_h(hours), "Human Effort Equivalent", "", link_onclick=evid_click)}
    {_card(act_str, "Active Time", f"{active_days} active day{'s' if active_days!=1 else ''}")}
    {_card(speed_str, "Speed Multiplier", "vs. unassisted expert")}
    {_card(f"+{lines:,}", "Lines of Code Added", lines_rem_str)}
    {_card(prs if prs else "\u2014", "PRs Merged", commits_str)}
  </tr></tbody></table>
</td></tr>"""


def _roi_row(hours: float, n_days: int, source: str, active_min: int = 0) -> str:
    # Always use combined enterprise seat cost as the basis
    combined_monthly = COPILOT_SEAT_MONTHLY + CLAUDE_SEAT_MONTHLY
    seat_cost  = (COPILOT_SEAT_DAILY + CLAUDE_SEAT_DAILY) * n_days
    seat_label = f"${combined_monthly:.0f}/mo enterprise seats"

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

    # Two-metric display when speed multiplier is available
    if speed_str:
        metrics_html = f"""
      <div style="display:flex;justify-content:center;align-items:center;gap:48px">
        <div style="text-align:center">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;
                      color:rgba(255,255,255,0.55);margin-bottom:6px">ROI · {seat_label}</div>
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
                    color:rgba(255,255,255,0.65);margin-bottom:6px">AI Return on Investment ({seat_label})</div>
        <div style="font-size:52px;font-weight:800;color:#ffffff;line-height:1;letter-spacing:-2px">{leverage}x</div>
      </div>"""

    return f"""<tr><td style="padding:0;border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:{bg};border-collapse:collapse">
    <tbody><tr><td style="padding:22px 48px">
      {metrics_html}
      <div style="text-align:center;margin-top:10px;font-size:12px;color:rgba(255,255,255,0.6)">
        {_fmt_h(hours)} x ${HOURLY_RATE:.0f}/hr = <strong style="color:rgba(255,255,255,0.9)">${value:,.0f} value</strong>
      </div>
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


def _goals_section(goals: list, source: str, show_date: bool = False, vid: str = "") -> str:
    if not goals:
        return ""
    accent   = ACCENT[source]
    accent_bg= ACCENT_BG.get(source, "#e8f2fb")
    rows     = []
    pfx      = f"{vid}-" if vid else ""   # unique prefix per view

    # Group into top-5 visible + rest collapsible
    for gi, g in enumerate(goals):
        gid   = f"{pfx}goal-{gi}"
        hours = g.get("human_hours") or 0
        title = g.get("title", "")
        proj  = g.get("project","") or g.get("label","")
        date  = g.get("_date", "")
        tasks = g.get("tasks", [])

        # Skill pills for the goal row
        domain_pills = "".join(
            f'<span style="background:{DOMAIN_PILL_BG};color:{DOMAIN_PILL_FG};padding:2px 8px;border-radius:9px;font-size:11px;font-weight:600;display:inline-block;margin:2px 3px 2px 0;white-space:nowrap">{_e(s)}</span>'
            for t in tasks for s in t.get("domain_skills", [])[:2]
        )[:3*200]  # cap length
        tech_pills = "".join(
            f'<span style="background:{TECH_PILL_BG};color:{TECH_PILL_FG};padding:2px 8px;border-radius:9px;font-size:11px;font-weight:600;display:inline-block;margin:2px 3px 2px 0;white-space:nowrap">{_e(s)}</span>'
            for t in tasks for s in t.get("tech_skills", [])[:2]
        )[:3*150]

        date_badge = f'<span style="font-size:10px;font-weight:600;color:{accent};background:{accent_bg};padding:1px 7px;border-radius:8px;margin-right:6px;white-space:nowrap">{_e(date[5:])}</span>' if (show_date and date) else ""

        goal_row = f"""<tr id="{gid}-hdr" style="cursor:pointer;" onclick="toggleDetail('{gid}')">
  <td style="padding:10px 10px;border-bottom:1px solid #dde1e7;vertical-align:top;width:4%">
    <div style="width:22px;height:22px;background:{accent};border-radius:50%;
                color:#fff;font-size:11px;font-weight:700;text-align:center;line-height:22px">{gi+1}</div>
  </td>
  <td style="padding:10px 8px;border-bottom:1px solid #dde1e7;vertical-align:top;width:42%">
    <div style="font-size:12px;font-weight:600;color:#1b1f23;line-height:1.35">
      <span id="{gid}-arrow" style="font-size:10px;color:{accent};margin-right:5px">&#9654;</span>
      {date_badge}{_e(title)}
    </div>
  </td>
  <td style="padding:10px 8px;border-bottom:1px solid #dde1e7;vertical-align:middle;width:40%">
    <div>{domain_pills}{tech_pills}</div>
    <div style="font-size:10px;color:#6a737d;margin-top:5px">{len(tasks)} task{"s" if len(tasks)!=1 else ""}</div>
  </td>
  <td style="padding:10px 8px;border-bottom:1px solid #dde1e7;vertical-align:middle;text-align:right;width:14%">
    <div style="font-size:16px;font-weight:700;color:{accent}">{_fmt_h(hours)}</div>
    <div style="font-size:10px;color:#6a737d;margin-top:1px">human est.</div>
  </td>
</tr>"""

        # Task detail rows
        task_rows = ""
        for t in tasks:
            what   = _e(t.get("what_got_done",""))
            ttype  = _e(t.get("task_type",""))
            th     = t.get("human_hours") or 0
            roles  = ", ".join(t.get("professional_roles",[])[:2])
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
  <td colspan="4" style="padding:0 8px 8px;background:#f7f9fc">
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border-top:1px solid #dde1e7">
      <tbody>{task_rows}</tbody>
    </table>
  </td>
</tr>"""

        rows.append(goal_row + task_section)

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
    <tbody>{visible}</tbody>
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


def _skills_section(goals: list, accent: str) -> str:
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

    inner = f"""{_section_header("Skills Mobilized", "Hours by professional role — primary role 65%, secondary 35% of task time")}
<div style="padding:14px 24px 18px">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tbody>{rows}</tbody>
  </table>
  <div style="font-size:10px;color:#6a737d;margin-top:10px">
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

    # Build mode_data tuples aligned with _COLLAB_MODES for card rendering
    # Tuple: (name, icon, desc, color, high_val, mins, pct, hrs_display)
    _QUALITY_COLOR_MAP = {
        "Creative partner":    "#7b1fa2",
        "Research assistant":  "#1a7f37",
        "Builder":             "#0078d4",
        "Refinement partner":  "#0969da",
        "Needed hand-holding": "#e65100",
        "Grunt work handled":  "#6a737d",
    }
    _QUALITY_ICON_MAP = {
        "Creative partner":    "🎨",
        "Research assistant":  "🔬",
        "Builder":             "🏗",
        "Refinement partner":  "✨",
        "Needed hand-holding": "🔧",
        "Grunt work handled":  "⚡",
    }
    _HIGH_VALUE = {"Creative partner", "Research assistant", "Builder", "Refinement partner"}

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
            desc = {"Needed hand-holding": "Errors, retries, course-correcting AI"}.get(mode_name, "")
        mode_data.append((mode_name, icon, desc, color, high_val, mins, pct, hrs))
    mode_data.sort(key=lambda x: -x[6])

    # Summary stats  (m: name, icon, desc, color, high_val, mins, pct, hrs)
    high_val_pct  = sum(m[6] for m in mode_data if m[4])
    grunt_pct     = next((m[6] for m in mode_data if m[0] == "Grunt work handled"), 0)
    handheld_pct  = next((m[6] for m in mode_data if m[0] == "Needed hand-holding"), 0)
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

    def _mode_card(name, icon, desc, color, high_val, mins, pct, hrs):
        if mins == 0:
            return ""
        bar_w = max(int(pct), 2)
        # Display as minutes if < 60, otherwise hours
        hrs_str = f"{int(mins)}m" if mins < 60 else _fmt_h(hrs)
        left_border = f"border-left:3px solid {color};" if high_val else f"border-left:3px solid {color};"
        return (
            f'<div style="background:#fff;border:1px solid #dde1e7;border-radius:9px;'
            f'{left_border}padding:14px 16px;margin-bottom:10px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            f'<div style="font-size:13px;font-weight:700;color:#1b1f23">'
            f'<span style="margin-right:8px">{icon}</span>{_e(name)}</div>'
            f'<div style="font-size:16px;font-weight:700;color:{color}">{pct:.0f}%</div>'
            f'</div>'
            f'<div style="background:#f0f2f5;border-radius:4px;height:6px;width:100%;margin-bottom:8px">'
            f'<div style="background:{color};border-radius:4px;height:6px;width:{bar_w}%;min-width:4px"></div>'
            f'</div>'
            f'<div style="font-size:11px;color:#6a737d">{_e(desc)} &nbsp;·&nbsp; '
            f'<strong style="color:#1b1f23">{_e(hrs_str)}</strong></div>'
            f'</div>'
        )

    # Build 2-column layout (left col: even indices, right col: odd indices)
    visible = [m for m in mode_data if m[5] >= 0.1]   # mins > 0.1
    left_col  = "".join(_mode_card(*m) for m in visible[0::2])
    right_col = "".join(_mode_card(*m) for m in visible[1::2])

    inner = f"""{_section_header("How I Collaborated", f"The different types of work {tool_name} handled for you")}
<div style="padding:16px 24px 18px">
  {summary_html}
  <table width="100%" cellpadding="0" cellspacing="0"><tbody><tr>
    <td style="width:50%;vertical-align:top;padding-right:8px">{left_col}</td>
    <td style="width:50%;vertical-align:top;padding-left:8px">{right_col}</td>
  </tr></tbody></table>
</div>"""
    return _wrap_section(inner)


def _collab_comparison_section(copilot_modes: dict, claude_modes: dict,
                               copilot_active_min: float, claude_active_min: float) -> str:
    """Side-by-side collaboration mode comparison between Copilot and Claude."""
    if not copilot_modes and not claude_modes:
        return ""

    _COLOR = {
        "Creative partner":    "#7b1fa2",
        "Research assistant":  "#1a7f37",
        "Builder":             "#0078d4",
        "Refinement partner":  "#0969da",
        "Needed hand-holding": "#e65100",
        "Grunt work handled":  "#6a737d",
    }
    _ICON = {
        "Creative partner":    "🎨",
        "Research assistant":  "🔬",
        "Builder":             "🏗",
        "Refinement partner":  "✨",
        "Needed hand-holding": "🔧",
        "Grunt work handled":  "⚡",
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
        leader   = "GitHub Copilot" if cop_p > cla_p else "Claude"
        follower = "Claude" if cop_p > cla_p else "GitHub Copilot"
        leader_pct   = max(cop_p, cla_p)
        follower_pct = min(cop_p, cla_p)
        insight_html = (
            f'<div style="background:#f6f8fa;border:1px solid #e1e4e8;border-radius:8px;'
            f'padding:12px 16px;margin-bottom:18px;font-size:12px;color:#1b1f23;line-height:1.5">'
            f'<strong>Biggest difference:</strong> <em>{_e(mode_name)}</em> — '
            f'{_e(leader)} {leader_pct:.0f}% vs {_e(follower)} {follower_pct:.0f}%'
            f'</div>'
        )

    rows = ""
    for mode in all_modes:
        cop_pct = copilot_modes.get(mode, 0) / cop_total * 100
        cla_pct = claude_modes.get(mode, 0) / cla_total * 100
        cop_min = copilot_modes.get(mode, 0)
        cla_min = claude_modes.get(mode, 0)
        color   = _COLOR.get(mode, "#6a737d")
        icon    = _ICON.get(mode, "")

        def _bar(pct, color, mins, align="left"):
            w       = max(int(pct), 1) if pct > 0 else 0
            hrs_str = f"{int(mins)}m" if mins < 60 else f"{mins/60:.1f}h"
            label   = f'<span style="font-size:10px;color:{color};font-weight:700;white-space:nowrap">{pct:.0f}% · {hrs_str}</span>'
            if align == "right":
                return (
                    f'<div style="display:flex;align-items:center;gap:6px;justify-content:flex-end">'
                    f'{label}'
                    f'<div style="background:#f0f2f5;border-radius:4px;height:10px;width:80px;flex-shrink:0">'
                    f'<div style="background:{color};border-radius:4px;height:10px;width:{w}%;min-width:{2 if w else 0}px;float:right"></div>'
                    f'</div></div>'
                ) if pct > 0 else f'<div style="text-align:right;font-size:10px;color:#d0d7de">—</div>'
            else:
                return (
                    f'<div style="display:flex;align-items:center;gap:6px">'
                    f'<div style="background:#f0f2f5;border-radius:4px;height:10px;width:80px;flex-shrink:0">'
                    f'<div style="background:{color};border-radius:4px;height:10px;width:{w}%;min-width:{2 if w else 0}px"></div>'
                    f'</div>'
                    f'{label}'
                    f'</div>'
                ) if pct > 0 else f'<div style="font-size:10px;color:#d0d7de">—</div>'

        rows += f"""<tr style="border-bottom:1px solid #f0f2f5">
  <td style="text-align:right;padding:7px 8px 7px 0;width:38%">{_bar(cop_pct, "#0078d4", cop_min, "right")}</td>
  <td style="padding:7px 10px;text-align:center;white-space:nowrap;width:24%">
    <span style="font-size:10px;margin-right:4px">{icon}</span>
    <span style="font-size:11px;font-weight:600;color:{color}">{_e(mode)}</span>
  </td>
  <td style="padding:7px 0 7px 8px;width:38%">{_bar(cla_pct, "#7B2FBE", cla_min, "left")}</td>
</tr>"""

    cop_active = f"{int(copilot_active_min)//60}h {int(copilot_active_min)%60}m" if copilot_active_min >= 60 else f"{int(copilot_active_min)}m"
    cla_active = f"{int(claude_active_min)//60}h {int(claude_active_min)%60}m" if claude_active_min >= 60 else f"{int(claude_active_min)}m"

    header_row = f"""<tr style="border-bottom:2px solid #e1e4e8">
  <td style="text-align:right;padding:0 8px 10px 0">
    <span style="font-size:11px;font-weight:700;color:#0078d4">● GitHub Copilot</span>
    <span style="font-size:10px;color:#6a737d;margin-left:6px">{cop_active} active</span>
  </td>
  <td style="padding:0 10px 10px;text-align:center">
    <span style="font-size:10px;color:#6a737d;text-transform:uppercase;letter-spacing:0.5px">Mode</span>
  </td>
  <td style="padding:0 0 10px 8px">
    <span style="font-size:11px;font-weight:700;color:#7B2FBE">● Claude</span>
    <span style="font-size:10px;color:#6a737d;margin-left:6px">{cla_active} active</span>
  </td>
</tr>"""

    inner = f"""{_section_header("How I Collaborated", "Comparing collaboration style — % of active engagement time per tool")}
<div style="padding:16px 24px 18px">
  {insight_html}
  <table width="100%" cellpadding="0" cellspacing="0">
    <tbody>{header_row}{rows}</tbody>
  </table>
  <div style="font-size:10px;color:#6a737d;margin-top:12px">
    Bars show each tool's % of its own active engagement time. Compare shapes, not lengths.
  </div>
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
    has_all = split and copilot_buckets_all is not None and claude_buckets_all is not None

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
      <div style="background:#0078d4;height:20px;width:{cop_w}%;min-width:{2 if cop_n else 0}px;display:flex;align-items:center;justify-content:flex-end">{cop_label}</div>
      <div style="background:#7B2FBE;height:20px;width:{cla_w}%;min-width:{2 if cla_n else 0}px;display:flex;align-items:center;justify-content:flex-start">{cla_label}</div>
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

    if split:
        legend = """<div style="display:flex;gap:16px;margin-bottom:10px;font-size:11px;color:#6a737d">
  <span><span style="display:inline-block;width:10px;height:10px;background:#0078d4;border-radius:2px;margin-right:4px"></span>GitHub Copilot</span>
  <span><span style="display:inline-block;width:10px;height:10px;background:#7B2FBE;border-radius:2px;margin-right:4px"></span>Claude</span>
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
  {_kpi_chip("GitHub Copilot engagement", str(round(cop_f/cop_h)) if cop_h else "—", "#0078d4")}
  {_kpi_chip("Claude engagement", str(round(cla_f/cla_h)) if cla_h else "—", "#7B2FBE")}
</div>"""

        if has_all:
            cop_a = sum(copilot_buckets_all.values())
            cla_a = sum(claude_buckets_all.values())
            kpi_all = f"""<div style="margin-bottom:14px;display:flex;flex-wrap:wrap;gap:4px">
  {_kpi_chip("GitHub Copilot engagement", str(round(cop_a/cop_h)) if cop_h else "—", "#0078d4")}
  {_kpi_chip("Claude engagement", str(round(cla_a/cla_h)) if cla_h else "—", "#7B2FBE")}
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
      b.textContent=showAll?'Exclude trivial':'Include trivial';
    }})()"
    style="font-size:10px;padding:3px 10px;border:1px solid #d0d7de;border-radius:4px;
           background:#f6f8fa;color:#57606a;cursor:pointer;white-space:nowrap">
    Include trivial
  </button>
</div>"""

    subtitle = "Non-trivial prompts by time of day — approvals, single-key responses, and idle time excluded"
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
        reads  = s.get("reads", 0)
        edits  = s.get("edits", 0)
        runs   = s.get("runs", 0)
        turns  = s.get("conversation_turns", 0)
        lines  = s.get("lines_added", 0)
        files  = s.get("files_touched", 0)   # may be 0 for old cache
        act_m  = s.get("active_minutes", 0.0)
        itr    = round(edits / max(files, 1), 1) if files else edits

        th = _tools_h(reads, edits, runs)
        ah = _active_h(act_m)
        lh = _lines_h(abs(lines))
        nh = _turns_h(turns)
        cx = _complexity_mult(turns, files if files else max(1, edits // 5), edits)

        # Winning signal = max(tools, active, turns); lines is additive
        signals = {"tools": th, "active": ah, "turns": nh}
        win_key = max(signals, key=signals.get)
        win_h   = signals[win_key]
        det_est = round((win_h * cx + lh) * 4) / 4

        cx_pct  = int((cx - 1.0) * 100)
        total_det += det_est

        # Match to an AI estimate from goals
        goals_for_proj = [g for g in data.get("goals", [])
                          if (g.get("project") or "") == proj
                          or proj in (g.get("title", "") + g.get("summary", ""))]
        ai_est = sum(g.get("human_hours") or 0 for g in goals_for_proj) if goals_for_proj else None
        if ai_est is not None:
            total_ai_est += ai_est

        proj_rows.append((proj, reads, edits, runs, turns, lines, files, itr,
                          act_m, th, ah, lh, nh, cx, cx_pct, win_key,
                          det_est, ai_est))

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
    def _sig_cell(val_h: float, is_winner: bool) -> str:
        s_txt = _fmt_h(val_h) if val_h > 0 else "—"
        style = (f'font-size:11px;font-weight:{"800" if is_winner else "400"};'
                 f'color:{accent if is_winner else "#6a737d"};'
                 f'{"text-decoration:underline;" if is_winner else ""}')
        return f'<td style="padding:4px 8px;text-align:center;{style}">{s_txt}</td>'

    table_rows = ""
    for (proj, reads, edits, runs, turns, lines, files, itr,
         act_m, th, ah, lh, nh, cx, cx_pct, win_key,
         det_est, ai_est) in proj_rows:

        cx_badge = (
            f'<span style="font-size:9px;font-weight:700;color:{accent};background:{ACCENT_BG.get("copilot","#e8f2fb")};'
            f'padding:1px 5px;border-radius:6px;margin-left:6px">+{cx_pct}%</span>'
            if cx_pct > 0 else ""
        )
        tools_breakdown = f'<div style="font-size:9px;color:#6a737d;margin-top:2px">{reads}r · {edits}e · {runs}x</div>'
        ai_cell = _fmt_h(ai_est) if ai_est is not None else "—"
        lines_disp = f"+{lines:,}" if lines >= 0 else f"{lines:,}"
        act_disp   = f"{int(act_m)}m" if act_m else "—"
        files_disp = str(files) if files else "—"
        itr_disp   = str(itr)   if files else "—"

        table_rows += f"""<tr style="border-bottom:1px solid #f0f2f5">
  <td style="padding:8px 8px;vertical-align:top;font-size:11px;font-weight:600;color:#1b1f23;max-width:200px">
    {_e(proj)}{cx_badge}
    {tools_breakdown}
  </td>
  <td style="padding:8px 8px;text-align:center;font-size:12px;font-weight:700;color:#1b1f23;vertical-align:top">{reads+edits+runs}</td>
  <td style="padding:8px 8px;text-align:center;font-size:11px;color:#6a737d;vertical-align:top">{act_disp}</td>
  <td style="padding:8px 8px;text-align:center;font-size:11px;color:#6a737d;vertical-align:top">{lines_disp}</td>
  <td style="padding:8px 8px;text-align:center;font-size:11px;color:#6a737d;vertical-align:top">{turns}</td>
  <td style="padding:8px 8px;text-align:center;font-size:11px;color:#6a737d;vertical-align:top">{files_disp}</td>
  <td style="padding:8px 8px;text-align:center;font-size:11px;color:#6a737d;vertical-align:top">{itr_disp}</td>
  {_sig_cell(det_est, win_key == "tools" or True)}
  <td style="padding:8px 8px;text-align:right;font-size:13px;font-weight:700;color:#1a7f37;vertical-align:top;white-space:nowrap">
    {_e(ai_cell)}
  </td>
</tr>
<tr style="border-bottom:1px solid #e8eaf0;background:#fafbfc">
  <td style="padding:2px 8px 6px;font-size:9px;color:#6a737d">signal hours</td>
  <td style="padding:2px 8px 6px;text-align:center;font-size:10px;{"font-weight:700;color:"+accent if win_key=="tools" else "color:#6a737d"}">{_fmt_h(th) if th else "—"}</td>
  <td style="padding:2px 8px 6px;text-align:center;font-size:10px;{"font-weight:700;color:"+accent if win_key=="active" else "color:#6a737d"}">{_fmt_h(ah) if ah else "—"}</td>
  <td style="padding:2px 8px 6px;text-align:center;font-size:10px;color:#6a737d">{_fmt_h(lh) if lh else "—"}</td>
  <td style="padding:2px 8px 6px;text-align:center;font-size:10px;{"font-weight:700;color:"+accent if win_key=="turns" else "color:#6a737d"}">{_fmt_h(nh) if nh else "—"}</td>
  <td colspan="2" style="padding:2px 8px 6px;font-size:9px;color:#6a737d">complexity signals</td>
  <td colspan="2" style="padding:2px 8px 6px;text-align:right;font-size:10px;font-weight:700;color:{accent}">{_fmt_h(det_est)}</td>
</tr>"""

    total_row = f"""<tr style="background:#f0f2f5;border-top:2px solid {accent}">
  <td colspan="7" style="padding:8px 10px;font-size:11px;font-weight:700;color:#1b1f23;text-align:right">Total</td>
  <td style="padding:8px 10px;text-align:right;font-size:14px;font-weight:700;color:{accent}">{_fmt_h(total_det)}</td>
  <td style="padding:8px 10px;text-align:right;font-size:14px;font-weight:700;color:#1a7f37">{_fmt_h(total_ai_est) if total_ai_est else "—"}</td>
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
  <div style="font-size:12px;color:{accent};font-family:monospace;background:#f0f2f5;padding:8px 12px;border-radius:6px;margin-bottom:10px">
    total = (max(tools, active, turns) × complexity) + lines
    <span style="font-size:10px;color:#6a737d;font-family:sans-serif;margin-left:10px">— strongest signal wins, plus lines as additive</span>
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #dde1e7;border-radius:7px;overflow:hidden;margin-bottom:10px">
    <thead><tr style="background:#e8f2fb">
      <th style="padding:6px 10px;text-align:left;font-size:9px;font-weight:700;color:{accent};text-transform:uppercase">Signal</th>
      <th style="padding:6px 10px;font-size:9px;font-weight:700;color:{accent};text-transform:uppercase">Rate</th>
      <th style="padding:6px 10px;font-size:9px;font-weight:700;color:{accent};text-transform:uppercase">Tier Mapping</th>
    </tr></thead>
    <tbody>
      <tr><td style="padding:5px 10px;font-size:11px">Tool invocations</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">weighted by type</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">reads×0.3m &nbsp;edits×1.5m &nbsp;runs×0.75m</td></tr>
      <tr style="background:#fafbfc"><td style="padding:5px 10px;font-size:11px">Active time</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">×4 multiplier</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">active_minutes × 4 ÷ 60  (upper bound of 1.4–4× research range)</td></tr>
      <tr><td style="padding:5px 10px;font-size:11px">Conversation turns</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">~5–7 min/turn</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">1–3→0.25h &nbsp;4–8→0.75h &nbsp;9–15→1.5h &nbsp;16–30→3h &nbsp;31–60→5h &nbsp;61+→8h+</td></tr>
      <tr style="background:#fafbfc"><td style="padding:5px 10px;font-size:11px">Lines of code</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">100–150 LoC/hr</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">1–50→0.25h &nbsp;51–150→0.75h &nbsp;151–300→1.5h &nbsp;301–500→2.5h &nbsp;500+→4h+ (additive)</td></tr>
    </tbody>
  </table>
  <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#6a737d;margin-bottom:6px">Complexity Multipliers</div>
  <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #dde1e7;border-radius:7px;overflow:hidden">
    <thead><tr style="background:#e8f2fb">
      <th style="padding:6px 10px;text-align:left;font-size:9px;font-weight:700;color:{accent};text-transform:uppercase">Signal</th>
      <th style="padding:6px 10px;font-size:9px;font-weight:700;color:{accent};text-transform:uppercase">When</th>
      <th style="padding:6px 10px;font-size:9px;font-weight:700;color:{accent};text-transform:uppercase">Adjust</th>
    </tr></thead>
    <tbody>
      <tr><td style="padding:5px 10px;font-size:11px">Conversation turns</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">&gt; 15</td>
          <td style="padding:5px 10px;font-size:11px;font-weight:600;color:{accent}">+15%</td></tr>
      <tr style="background:#fafbfc"><td style="padding:5px 10px;font-size:11px">Conversation turns</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">&gt; 40</td>
          <td style="padding:5px 10px;font-size:11px;font-weight:600;color:{accent}">+20% more</td></tr>
      <tr><td style="padding:5px 10px;font-size:11px">Iteration depth</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">&gt; 5 edits/file</td>
          <td style="padding:5px 10px;font-size:11px;font-weight:600;color:{accent}">+15%</td></tr>
      <tr style="background:#fafbfc"><td style="padding:5px 10px;font-size:11px">Iteration depth</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">&gt; 12 edits/file</td>
          <td style="padding:5px 10px;font-size:11px;font-weight:600;color:{accent}">+20% more</td></tr>
      <tr><td style="padding:5px 10px;font-size:11px">Files touched</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">&gt; 3</td>
          <td style="padding:5px 10px;font-size:11px;font-weight:600;color:{accent}">+10%</td></tr>
      <tr style="background:#fafbfc"><td style="padding:5px 10px;font-size:11px">Files touched</td>
          <td style="padding:5px 10px;font-size:11px;color:#6a737d">&gt; 10</td>
          <td style="padding:5px 10px;font-size:11px;font-weight:600;color:{accent}">+20% more (cap 1.5×)</td></tr>
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
    &#9632; Det. Est. = deterministic formula &nbsp;·&nbsp;
    <strong>Bold</strong> = highest signal &nbsp;·&nbsp;
    +N% = complexity multiplier &nbsp;·&nbsp;
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


# ── Individual source tab ─────────────────────────────────────────────────────

def _source_view(view_id: str, data: dict | None, source: str,
                 tool_name: str, tool_icon: str,
                 n_days: int, active_days: int,
                 date_range_str: str) -> str:
    accent = ACCENT[source]
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
    projects     = len({g.get("project","") for g in goals if g.get("project")})
    sessions     = data.get("sessions_count", 0)
    headline     = data.get("headline", f"{tool_name} activity")

    header_row = f"""<tr>
  <td style="background:linear-gradient(135deg,#24292f,#1b1f23);border-radius:9px 9px 0 0;padding:22px 24px">
    <div style="font-size:10px;color:rgba(255,255,255,0.6);letter-spacing:1.2px;
                text-transform:uppercase;margin-bottom:4px">
      {_e(date_range_str)} &nbsp;·&nbsp; {_e(tool_name)} Impact Report
    </div>
    <div style="font-size:20px;font-weight:700;color:#fff;line-height:1.3">
      {tool_icon}&nbsp;{_e(active_days)} active day{"s" if active_days!=1 else ""}
      ({_e(date_range_str[:5] if len(date_range_str)>10 else date_range_str)}):
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
    goals_sec = _goals_section(goals, source, show_date=True, vid=view_id)
    produced  = _produced_section(data, accent)
    skills    = _skills_section(goals, accent)
    collab    = _collab_section(data, source, tool_name)
    timing    = _timing_section(data, source, tool_name)
    numbers   = _numbers_section(data, source, n_days)
    evidence  = _evidence_section(data, accent, vid=view_id)

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
{numbers}
{evidence}
{footer}
</tbody></table></td></tr></tbody></table>
</div>"""


# ── Aggregate "All" tab ───────────────────────────────────────────────────────

def _top_projects_card(goals: list, source: str, tool_name: str, top_n: int = 5) -> str:
    accent   = ACCENT[source]
    bg       = ACCENT_BG.get(source, "#e8f2fb")
    border   = "#0078d4" if source == "copilot" else "#7B2FBE"
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
        # Collect skills (deduped)
        skills: list[str] = []
        for t in tasks:
            for s in t.get("domain_skills", [])[:1] + t.get("tech_skills", [])[:1]:
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
              n_days: int, date_range_str: str, include_copilot: bool, include_claude: bool) -> str:
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
        hours_split_parts.append(f'<span style="color:#0078d4;font-weight:600">{_fmt_h(c_hours)} Copilot</span>')
    if include_claude and cl_hours:
        hours_split_parts.append(f'<span style="color:#7B2FBE;font-weight:600">{_fmt_h(cl_hours)} Claude</span>')
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
               f"{c_n_goals} goals · {c_sess} sessions", "#0078d4", "#0078d4") if include_copilot else ""}
    {_agg_card(_fmt_active(c_active_min), "Copilot Active Time",
               "engaged time with AI", "#0078d4", "#0078d4") if include_copilot else ""}
    {_agg_card(_fmt_h(cl_hours), "Claude Human Est.",
               f"{cl_n_goals} goals · {cl_sess} sessions", "#7B2FBE", "#7B2FBE") if include_claude else ""}
    {_agg_card(_fmt_active(cl_active_min), "Claude Active Time",
               "engaged time with AI", "#7B2FBE", "#7B2FBE") if include_claude else ""}
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
                      color:rgba(255,255,255,0.55);margin-bottom:6px">ROI · ${combined_monthly:.0f}/mo enterprise seats</div>
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
                    color:rgba(255,255,255,0.65);margin-bottom:8px">
          Combined AI Return on Investment (${combined_monthly:.0f}/mo enterprise seats)
        </div>
        <div style="font-size:52px;font-weight:800;color:#ffffff;line-height:1;letter-spacing:-2px">{leverage}x</div>
      </div>"""

    roi_row = f"""<tr><td style="padding:0;border-left:1px solid #dde1e7;border-right:1px solid #dde1e7">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:linear-gradient(135deg,#1a1a2e,#0F3460);border-collapse:collapse">
    <tbody><tr><td style="padding:22px 48px">
      {all_metrics_html}
      <div style="text-align:center;margin-top:10px;font-size:12px;color:rgba(255,255,255,0.6)">
        {_fmt_h(total_h)} x ${HOURLY_RATE:.0f}/hr = <strong style="color:rgba(255,255,255,0.9)">${total_h*HOURLY_RATE:,.0f} value</strong>
      </div>
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

    skills_row = _skills_section(combined_goals, "#0078d4")
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
      {_fmt_h(total_h)} of AI-assisted work across {total_proj} projects
    </div>
  </td>
</tr>"""

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

    # Logos — Copilot (official two-path SVG: helmet body + visor eyes) and Claude (Anthropic "A")
    copilot_svg_sm = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" style="vertical-align:middle;flex-shrink:0">'
        '<path fill="currentColor" d="M23.922 16.992c-.861 1.495-5.859 5.023-11.922 5.023-6.063 0-11.061-3.528-11.922-5.023A.641.641 0 0 1 0 16.736v-2.869a.841.841 0 0 1 .053-.22c.372-.935 1.347-2.292 2.605-2.656.167-.429.414-1.055.644-1.517a10.195 10.195 0 0 1-.052-1.086c0-1.331.282-2.499 1.132-3.368.397-.406.89-.717 1.474-.952C7.333 2.952 9.326 2 12.056 2c2.731 0 4.767.952 6.166 2.088.584.235 1.077.546 1.474.952.85.869 1.132 2.037 1.132 3.368 0 .368-.014.733-.052 1.086.23.462.477 1.088.644 1.517 1.258.364 2.233 1.721 2.605 2.656.034.069.053.143.053.22v2.869a.641.641 0 0 1-.078.256z"/>'
        '<path fill="currentColor" d="M14.5 14.25a1 1 0 0 1 1 1v2a1 1 0 0 1-2 0v-2a1 1 0 0 1 1-1Zm-5 0a1 1 0 0 1 1 1v2a1 1 0 0 1-2 0v-2a1 1 0 0 1 1-1Z"/>'
        '</svg>'
    )
    # Anthropic / Claude logo — official starburst (11 rounded rays)
    _cl_rays = "".join(
        f'<rect x="-5.5" y="-44" width="11" height="30" rx="4.5" transform="rotate({round(i*360/11,1)})"/>'
        for i in range(11)
    )
    claude_svg_sm = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 100 100" style="vertical-align:middle;flex-shrink:0">'
        f'<g fill="currentColor" transform="translate(50,50)">{_cl_rays}</g>'
        '</svg>'
    )

    tab_buttons = ""
    if show_all:
        tab_buttons += f'<button class="tab-btn" id="tab-all" onclick="showTab(\'all\')">All</button>'
    if include_copilot:
        tab_buttons += (
            f'<button class="tab-btn" id="tab-copilot" onclick="showTab(\'copilot\')" style="color:#0078d4">'
            f'{copilot_svg_sm} GitHub Copilot <span class="tab-hours">· {_fmt_h(c_hours)}</span></button>'
        )
    if include_claude:
        tab_buttons += (
            f'<button class="tab-btn" id="tab-claude" onclick="showTab(\'claude\')" style="color:#7B2FBE">'
            f'{claude_svg_sm} Claude <span class="tab-hours">· {_fmt_h(cl_hours)}</span></button>'
        )

    all_view     = _all_view(copilot_agg, claude_agg, n_days, date_range_str,
                             include_copilot, include_claude) if show_all else ""
    copilot_view = _source_view("copilot", copilot_agg, "copilot",
                                "GitHub Copilot", copilot_svg_sm,
                                n_days, c_days, date_range_str) if include_copilot else ""
    claude_view  = _source_view("claude",  claude_agg,  "claude",
                                "Claude Code", claude_svg_sm,
                                n_days, cl_days, date_range_str) if include_claude else ""

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
.tab-bar {{
  position:sticky; top:0; z-index:100;
  background:#fff; border-bottom:2px solid #e1e4e8;
  display:flex; align-items:stretch; padding:0 16px;
  box-shadow:0 1px 4px rgba(0,0,0,0.08);
}}
.tab-btn {{
  padding:12px 16px; font-size:13px; font-weight:500; color:#586069;
  background:none; border:none; border-bottom:3px solid transparent;
  cursor:pointer; margin-bottom:-2px; transition:all 0.15s;
  display:flex; align-items:center; gap:6px; white-space:nowrap;
}}
.tab-btn:hover {{ color:#24292f; background:#f6f8fa; }}
.tab-btn.active {{ color:#0969da; border-bottom-color:#0969da; font-weight:600; }}
.tab-hours {{ font-size:11px; font-weight:400; opacity:0.7; }}
.view {{ display:none; }}
</style>
</head>
<body>
<div class="tab-bar">
  {tab_buttons}
</div>
{all_view}
{copilot_view}
{claude_view}
</body>
</html>"""
