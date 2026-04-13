"""
whatidid.py — Combined analytics for GitHub Copilot + Claude Code.

Usage:
  python whatidid.py               # Last 7 days, both sources
  python whatidid.py --date today  # Today only
  python whatidid.py --7D          # Last 7 days
  python whatidid.py --30D         # Last 30 days
  python whatidid.py --from 2026-04-01 --to 2026-04-06
  python whatidid.py --copilot     # Copilot only
  python whatidid.py --claude      # Claude only
  python whatidid.py --email       # Send via Outlook
  python whatidid.py --refresh     # Bypass cache, re-analyze
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

from harvest_copilot import get_sessions_for_date as _harvest_copilot
from harvest_claude  import get_sessions_for_date as _harvest_claude
from analyze import analyze_day, check_api_health
from report  import generate_report
from email_send import send_email

# ─── Paths ────────────────────────────────────────────────────────────────────
STORE_DIR  = Path.home() / ".claude" / "whatidid_ai"
CACHE_DIR  = STORE_DIR / "cache"
REPORT_DIR = STORE_DIR

DEFAULT_EMAIL = ""   # override with your email address if desired


# ─── Date helpers ─────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _resolve_dates(args) -> list[str]:
    today = date.today()

    if getattr(args, "_7D", False):
        return [(today - timedelta(days=i)).isoformat() for i in range(7)]
    if getattr(args, "_30D", False):
        return [(today - timedelta(days=i)).isoformat() for i in range(30)]

    if args.date_from:
        d0 = _parse_date(args.date_from)
        d1 = _parse_date(args.date_to) if args.date_to else today
        if d0 and d1:
            out, cur = [], d0
            while cur <= d1:
                out.append(cur.isoformat())
                cur += timedelta(days=1)
            return out

    if args.date:
        s = args.date.strip()
        if s.lower() in ("today", "now"):
            return [today.isoformat()]
        m = re.fullmatch(r"(\d+)[Dd]", s)
        if m:
            n = int(m.group(1))
            return [(today - timedelta(days=i)).isoformat() for i in range(n)]
        d = _parse_date(s)
        if d:
            return [d.isoformat()]

    # Default: last 7 days
    return [(today - timedelta(days=i)).isoformat() for i in range(7)]


def _detect_email() -> str:
    """Try to detect the user's email from git config."""
    try:
        r = subprocess.run(
            ["git", "config", "--global", "user.email"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip()
    except Exception:
        return ""


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="What-I-Did AI: Combined analytics for GitHub Copilot + Claude Code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--date", metavar="DATE",
                        help="Date, lookback, or keyword (YYYY-MM-DD, 7D, 30D, today)")
    parser.add_argument("--from", dest="date_from", metavar="DATE", help="Date range start")
    parser.add_argument("--to",   dest="date_to",   metavar="DATE", help="Date range end")
    parser.add_argument("--7D",  dest="_7D",  action="store_true", help="Last 7 days")
    parser.add_argument("--30D", dest="_30D", action="store_true", help="Last 30 days")
    parser.add_argument("--copilot", action="store_true",
                        help="Show only GitHub Copilot data (skip Claude)")
    parser.add_argument("--claude", action="store_true",
                        help="Show only Claude data (skip Copilot)")
    parser.add_argument("--email", nargs="?", const=True, metavar="ADDRESS",
                        help="Send report via Outlook (optional: recipient address)")
    parser.add_argument("--refresh", action="store_true",
                        help="Force re-analysis, bypass cache")
    parser.add_argument("--lock", action="store_true",
                        help="Freeze estimates in cache (survive future --refresh)")
    parser.add_argument("--html", action="store_true",
                        help="Save HTML to disk only, don't open browser")
    args = parser.parse_args()

    # Which sources to include
    include_copilot = not args.claude
    include_claude  = not args.copilot

    dates = _resolve_dates(args)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # AI backend detection
    print("Checking AI analysis API … ", end="", flush=True)
    api_status, api_msg = check_api_health()
    if api_status == "ok":
        print(f"OK  [{api_msg}]")
    else:
        print(f"unavailable\n  {api_msg}")

    use_api = (api_status == "ok")

    # ── Per-date processing ─────────────────────────────────────────────────
    analyses: list[dict] = []

    for d in sorted(dates):
        copilot_cache = CACHE_DIR / f"copilot_{d}.json"
        claude_cache  = CACHE_DIR / f"claude_{d}.json"

        copilot_analysis: dict | None = None
        claude_analysis:  dict | None = None

        # Copilot
        if include_copilot:
            if copilot_cache.exists() and not args.refresh:
                try:
                    cached = json.loads(copilot_cache.read_text(encoding="utf-8"))
                    if not cached.get("locked"):
                        copilot_analysis = cached
                        print(f"  {d} [Copilot] loaded from cache")
                except Exception:
                    pass

            if copilot_analysis is None:
                print(f"  {d} [Copilot] harvesting … ", end="", flush=True)
                sessions = _harvest_copilot(d)
                print(f"{len(sessions)} session(s)")
                if sessions:
                    print(f"         analyzing … ", end="", flush=True)
                    copilot_analysis = analyze_day(
                        d, sessions, source="copilot",
                        refresh=args.refresh, use_api=use_api, cache_dir=CACHE_DIR,
                    )
                    if args.lock:
                        copilot_analysis["locked"] = True
                    copilot_cache.write_text(
                        json.dumps(copilot_analysis, indent=2), encoding="utf-8"
                    )
                    method = copilot_analysis.get("analysis_method", "?")
                    print(f"done ({method})")

        # Claude
        if include_claude:
            if claude_cache.exists() and not args.refresh:
                try:
                    cached = json.loads(claude_cache.read_text(encoding="utf-8"))
                    if not cached.get("locked"):
                        claude_analysis = cached
                        print(f"  {d} [Claude]  loaded from cache")
                except Exception:
                    pass

            if claude_analysis is None:
                print(f"  {d} [Claude]  harvesting … ", end="", flush=True)
                sessions = _harvest_claude(d)
                print(f"{len(sessions)} session(s)")
                if sessions:
                    print(f"         analyzing … ", end="", flush=True)
                    claude_analysis = analyze_day(
                        d, sessions, source="claude",
                        refresh=args.refresh, use_api=use_api, cache_dir=CACHE_DIR,
                    )
                    if args.lock:
                        claude_analysis["locked"] = True
                    claude_cache.write_text(
                        json.dumps(claude_analysis, indent=2), encoding="utf-8"
                    )
                    method = claude_analysis.get("analysis_method", "?")
                    print(f"done ({method})")

        if copilot_analysis or claude_analysis:
            analyses.append({
                "date":    d,
                "copilot": copilot_analysis,
                "claude":  claude_analysis,
            })

    if not analyses:
        print("\nNo activity found in the specified date range.")
        sys.exit(0)

    # ── Console summary ────────────────────────────────────────────────────────
    def _sum_goal_hours(analysis: dict | None) -> float:
        if not analysis:
            return 0.0
        # Prefer top-level human_hours if set; otherwise sum from goals
        h = analysis.get("human_hours")
        if h is not None:
            return float(h)
        return sum(g.get("human_hours", 0) for g in analysis.get("goals", []))

    total_copilot_h = sum(_sum_goal_hours(a.get("copilot")) for a in analyses)
    total_claude_h  = sum(_sum_goal_hours(a.get("claude"))  for a in analyses)
    total_h = total_copilot_h + total_claude_h

    print(f"\n{'-' * 50}")
    sorted_dates = sorted(dates)
    print(f"  Date range   : {sorted_dates[0]} to {sorted_dates[-1]}")
    print(f"  Days covered : {len(analyses)}")
    if include_copilot:
        print(f"  Copilot work : {total_copilot_h:.1f}h human-equivalent")
    if include_claude:
        print(f"  Claude work  : {total_claude_h:.1f}h human-equivalent")
    print(f"  Combined     : {total_h:.1f}h total")
    print(f"{'-' * 50}\n")

    # ── Generate report ────────────────────────────────────────────────────────
    print("Generating report … ", end="", flush=True)
    html = generate_report(
        analyses=analyses,
        include_copilot=include_copilot,
        include_claude=include_claude,
    )
    report_path = REPORT_DIR / "report.html"
    report_path.write_text(html, encoding="utf-8")
    print(f"saved: {report_path}")

    if not args.html:
        webbrowser.open(report_path.as_uri())

    # ── Email ──────────────────────────────────────────────────────────────────
    if args.email:
        recipient = args.email if isinstance(args.email, str) else None
        if not recipient:
            recipient = DEFAULT_EMAIL or _detect_email()
        if recipient:
            date_str  = f"{dates[0]} to {dates[-1]}" if len(dates) > 1 else dates[0]
            subject   = f"What I Did AI — {date_str}"
            print(f"Sending email to {recipient} … ", end="", flush=True)
            ok = send_email(recipient, subject, html)
            print("sent!" if ok else "FAILED (check Outlook is installed).")
        else:
            print("No email recipient found. Use --email your@address.com")


if __name__ == "__main__":
    main()
