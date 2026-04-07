"""
harvest_claude.py — Read Claude Code session JSONL files and extract structured activity data.

Sessions are stored at ~/.claude/projects/<encoded-path>/<session-uuid>.jsonl
Each line in the file is a JSON message (user, assistant, or metadata).
"""
from __future__ import annotations
import json
import re as _re
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR   = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"

_APPROVALS = {
    "yes", "y", "yep", "yeah", "yup", "no", "n", "nope",
    "ok", "okay", "sure", "fine", "right", "correct",
    "proceed", "go ahead", "go for it", "do it", "do that",
    "looks good", "sounds good", "that's fine", "that works",
    "approved", "continue", "perfect", "great", "good",
    "got it", "understood", "makes sense",
}

# Checked in priority order — most specific first, Building last.
# Default fallback is "Iterating" (most residual Claude interactions are refinements).
_INTENT_CATEGORIES = [
    ("Shipping",      _re.compile(r"\b(commit|push|pr|pull.request|merge|deploy|ship|release|branch|tag)\b", _re.I)),
    ("Investigating", _re.compile(r"\b(why does|why is|what.s going on|debug|diagnose|look at this error|what.s wrong|trace|root cause|broken|fails|failing|error|exception|issue|not working)\b", _re.I)),
    ("Testing",       _re.compile(r"\b(test|run|execute|rerun|verify|validate|smoke|try it|does it work|assert|confirm)\b", _re.I)),
    ("Researching",   _re.compile(r"\b(what.s the|how does|how do|are there|can i|cost|limit|explain|compare|difference|option|which|best way|recommend|what is)\b", _re.I)),
    ("Planning",      _re.compile(r"\b(plan|propose|approach|strategy|phase|prioriti|outline|roadmap|next step|think about|before we|should we)\b", _re.I)),
    ("Designing",     _re.compile(r"\b(redesign|design|visual|layout|style|look like|look more|spacing|appearance|prototype|mockup|branding|rethink|different approach|organiz|arrange|the layout|the design)\b", _re.I)),
    ("Configuring",   _re.compile(r"\b(config|configure|setting|enable|disable|turn on|turn off|parameter|env|environment|credential|auth|alias|install)\b", _re.I)),
    ("Navigating",    _re.compile(r"\b(open|find|search|list|show me|where is|navigate|look at|read|browse|display|what.s in)\b", _re.I)),
    ("Building",      _re.compile(r"\b(create|generate|implement|write|build|produce|initialize|scaffold|new feature|from scratch|set up|add new)\b", _re.I)),
    ("Iterating",     _re.compile(r"\b(fix|adjust|improve|refine|tweak|change|move|swap|resize|make it|slightly|update|modify|correct|revise|redo|clean|rename|remove|replace|add)\b", _re.I)),
]

_GIT_COMMIT_RE = _re.compile(r'\bgit\s+commit\b', _re.I)
_GIT_PUSH_RE   = _re.compile(r'\bgit\s+push\b', _re.I)
_PR_RE         = _re.compile(r'\bgh\s+pr\s+create\b|\bpull.request\b|\bcreate\s+pr\b', _re.I)
_REPO_SLUG_RE  = _re.compile(r'github\.com[/:]([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+?)(?:\.git)?(?:/|$)')


def _classify_intent(text: str) -> str:
    for intent, pattern in _INTENT_CATEGORIES:
        if pattern.search(text):
            return intent
    return "Iterating"   # most residual interactions in an agentic session are refinements


def _decode_project_name(encoded: str) -> str:
    """Convert encoded folder name to a human-readable project name via filesystem probing.

    Claude encodes the project path as: drive-letter + '--' + path-with-dashes
    e.g. C--Users-jsmith-projects-my-repo
    We probe the filesystem to find the longest real path, then return the path
    relative to the user's home directory.
    """
    from pathlib import Path as _Path
    home = _Path.home()

    # Try to match  X--rest  (Windows drive encoding)
    import re as _re2
    m = _re2.match(r'^([A-Za-z])--(.+)$', encoded)
    if m:
        drive  = m.group(1).upper()
        tokens = m.group(2).split("-")
        base   = _Path(f"{drive}:/")
        # Greedily build the longest real path by trying all dash-split combinations
        resolved = _probe_path(base, tokens)
        if resolved:
            try:
                rel = resolved.relative_to(home)
                return str(rel) if str(rel) != "." else resolved.name
            except ValueError:
                return str(resolved)

    # Unix-style: /home/user/... or just a plain folder name
    if encoded.startswith("/"):
        p = _Path(encoded)
        try:
            rel = p.relative_to(home)
            return str(rel) if str(rel) != "." else p.name
        except ValueError:
            return p.name

    # Fallback: strip known noise prefixes and return last meaningful part
    decoded = encoded.replace("--", "/").replace("-", "/")
    parts   = [p for p in decoded.split("/") if p and p.lower() not in ("users", "home", "c", "d")]
    return parts[-1] if parts else encoded


def _probe_path(base: "Path", tokens: list[str]) -> "Path | None":
    """Try every possible greedy combination of dash-separated tokens as folder names."""
    from pathlib import Path as _Path
    if not tokens:
        return base if base.exists() else None

    # Try joining 1..len(tokens) tokens into a single folder name at this level
    for width in range(len(tokens), 0, -1):
        candidate_name = "-".join(tokens[:width])
        candidate      = base / candidate_name
        if candidate.exists():
            result = _probe_path(candidate, tokens[width:])
            if result is not None:
                return result
    return base if base.exists() else None


def _project_name_from_cwd(cwd: str) -> str:
    """Return a human-readable project label from a working-directory path.

    Uses the path relative to the home directory so sessions from
    ~/claude/What-I-Did-AI appear as 'claude/What-I-Did-AI', not just 'What-I-Did-AI'.

    Exception: if cwd IS the home directory (or a top-level user folder like
    /home/user or C:\\Users\\user), fall back to just the leaf folder name so we
    don't swallow all home-dir sessions into one giant group.
    """
    from pathlib import Path as _Path
    home = _Path.home()
    p    = _Path(cwd)
    try:
        rel = p.relative_to(home)
        rel_str = str(rel)
        if rel_str in (".", ""):          # cwd == home itself
            return p.name
        parts = rel_str.replace("\\", "/").split("/")
        if len(parts) == 1 and parts[0].lower() in ("downloads", "documents", "desktop"):
            return parts[0]              # generic home sub-folder — keep as-is
        return rel_str.replace("\\", "/")
    except ValueError:
        return p.name


def _extract_text_from_content(content) -> str:
    """Extract plain text from message content (string or list of content items)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "tool_result":
                    # Skip tool results (they're responses to tool calls)
                    pass
        return " ".join(p for p in parts if p)
    return ""


def _extract_tool_calls_from_content(content) -> list[str]:
    """Extract tool call summaries from assistant message content."""
    if not isinstance(content, list):
        return []
    summaries = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            tool_name = item.get("name", "")
            inp = item.get("input", {}) or {}
            # Build a human-readable summary
            if tool_name in ("Read", "Glob", "Grep"):
                path = inp.get("file_path") or inp.get("path") or inp.get("pattern", "")
                summaries.append(f"Read {Path(path).name}" if path else f"Read file")
            elif tool_name in ("Write", "Edit"):
                path = inp.get("file_path") or inp.get("path", "")
                summaries.append(f"Edit {Path(path).name}" if path else f"Edit file")
            elif tool_name == "Bash":
                cmd = (inp.get("command") or "")[:60]
                summaries.append(f"Run: {cmd}")
            elif tool_name == "Agent":
                summaries.append(f"Agent: {inp.get('description', 'subagent')[:50]}")
            else:
                summaries.append(tool_name)
    return summaries


def _extract_lines_from_tool(tool_name: str, inp: dict, current_line_counts: dict) -> int:
    """Estimate lines added from Edit/Write tool calls."""
    added = 0
    if tool_name == "Write":
        path = inp.get("file_path", "")
        content = inp.get("content", "")
        new_count = content.count("\n") + 1 if content else 0
        old_count = current_line_counts.get(path, 0)
        delta = new_count - old_count
        if delta > 0:
            added = delta
        current_line_counts[path] = new_count
    elif tool_name == "Edit":
        old_str = inp.get("old_string", "")
        new_str = inp.get("new_string", "")
        old_lines = old_str.count("\n") + 1 if old_str else 0
        new_lines = new_str.count("\n") + 1 if new_str else 0
        delta = new_lines - old_lines
        if delta > 0:
            added = delta
    return added


def get_sessions_for_date(target_date: str) -> list:
    """
    Find all Claude Code sessions with activity on target_date (YYYY-MM-DD).
    Returns a list of session dicts compatible with the whatidid schema.
    """
    sessions = []

    if not PROJECTS_DIR.exists():
        return sessions

    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue

        project_name = _decode_project_name(project_dir.name)

        # Find all session JSONL files in this project
        jsonl_files = list(project_dir.glob("*.jsonl"))
        if not jsonl_files:
            continue

        for jsonl_path in jsonl_files:
            session_id = jsonl_path.stem
            session_data = _parse_session_file(jsonl_path, target_date, project_name, session_id)
            if session_data:
                sessions.append(session_data)

    return sessions


def _parse_session_file(jsonl_path: Path, target_date: str, project_name: str, session_id: str) -> dict | None:
    """Parse a single Claude session JSONL file for the target date."""
    lines_raw = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            lines_raw = [line.strip() for line in f if line.strip()]
    except Exception:
        return None

    if not lines_raw:
        return None

    # Parse all records
    records = []
    for line in lines_raw:
        try:
            records.append(json.loads(line))
        except Exception:
            continue

    # Check if any record falls on the target date
    has_target = any(
        r.get("timestamp", "")[:10] == target_date
        for r in records
        if r.get("type") in ("user", "assistant")
    )
    if not has_target:
        return None

    # Extract session metadata from first user message
    cwd = ""
    git_branch = ""
    for r in records:
        if r.get("type") == "user" and r.get("cwd"):
            cwd = r.get("cwd", "")
            git_branch = r.get("gitBranch", "")
            # Override project_name with path relative to home (better context than leaf-only)
            if cwd:
                project_name = _project_name_from_cwd(cwd) or project_name
            break

    # Process records chronologically
    messages            = []
    trivial_timestamps  = []   # timestamps of approval/trivial messages (not in messages[])
    tokens        = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    git_ops       = []
    git_repos     = []
    pull_requests = []
    lines_added   = 0
    files_touched = set()
    session_start = None
    session_end   = None
    current_line_counts: dict[str, int] = {}

    # We process in pairs: user message followed by assistant response
    pending_user_msg: dict | None = None

    for r in records:
        rtype = r.get("type", "")
        ts    = r.get("timestamp", "")

        if rtype == "user":
            if ts[:10] != target_date:
                continue

            content = r.get("message", {}).get("content", "") if isinstance(r.get("message"), dict) else r.get("message", "")
            text = _extract_text_from_content(content)

            if not text or len(text.strip()) == 0:
                continue

            # Skip pure approvals and single-digit menu selections (e.g. "1", "2")
            cleaned = text.strip().rstrip(".!").lower()
            if len(cleaned.split()) <= 8 and (cleaned in _APPROVALS or _re.fullmatch(r'\d{1,2}', cleaned)):
                trivial_timestamps.append(ts)
                continue

            if not session_start:
                session_start = ts
            session_end = ts

            # Detect git ops
            if _GIT_COMMIT_RE.search(text):
                if "commit" not in git_ops[-1:]:
                    git_ops.append("commit")
            if _GIT_PUSH_RE.search(text):
                if "push" not in git_ops[-1:]:
                    git_ops.append("push")
            if _PR_RE.search(text):
                if "pr" not in git_ops[-1:]:
                    git_ops.append("pr")

            # Look for GitHub repo slugs
            for slug in _REPO_SLUG_RE.findall(text):
                if slug not in git_repos:
                    git_repos.append(slug)

            pending_user_msg = {
                "role":        "user",
                "text":        text[:500],
                "timestamp":   ts,
                "tools_after": [],
                "intent":      _classify_intent(text),
            }
            messages.append(pending_user_msg)

        elif rtype == "assistant":
            if ts[:10] != target_date:
                continue

            if not session_start:
                session_start = ts
            session_end = ts

            # Accumulate tokens
            msg = r.get("message", {})
            usage = msg.get("usage", {})
            tokens["input"]          += usage.get("input_tokens", 0)
            tokens["output"]         += usage.get("output_tokens", 0)
            tokens["cache_read"]     += usage.get("cache_read_input_tokens", 0)
            tokens["cache_creation"] += usage.get("cache_creation_input_tokens", 0)

            # Extract tool calls from content
            content = msg.get("content", [])
            tool_summaries = _extract_tool_calls_from_content(content)

            if tool_summaries and pending_user_msg:
                pending_user_msg["tools_after"].extend(tool_summaries)

            # Track files and lines from tool calls
            for item in (content if isinstance(content, list) else []):
                if not isinstance(item, dict) or item.get("type") != "tool_use":
                    continue
                tool_name = item.get("name", "")
                inp = item.get("input", {}) or {}

                # Track files touched
                path = inp.get("file_path") or inp.get("path") or ""
                if path:
                    files_touched.add(str(Path(path).name))

                # Count lines added
                added = _extract_lines_from_tool(tool_name, inp, current_line_counts)
                lines_added += added

                # Detect git/PR operations from Bash commands
                if tool_name == "Bash":
                    cmd = inp.get("command", "")
                    if _GIT_COMMIT_RE.search(cmd) and "commit" not in git_ops[-1:]:
                        git_ops.append("commit")
                    if _GIT_PUSH_RE.search(cmd) and "push" not in git_ops[-1:]:
                        git_ops.append("push")
                    if _PR_RE.search(cmd) and "pr" not in git_ops[-1:]:
                        git_ops.append("pr")
                    # Detect PR URLs
                    pr_match = _re.search(r'github\.com/[^/]+/[^/]+/pull/(\d+)', cmd)
                    if pr_match:
                        pull_requests.append(f"#{pr_match.group(1)}")

            pending_user_msg = None  # reset after assistant response

    tokens["total"] = sum(v for k, v in tokens.items() if k != "total")

    user_messages = [m for m in messages if m["role"] == "user"]
    if not user_messages:
        return None

    return {
        "session_id":     session_id,
        "project":        project_name,
        "project_path":   cwd or str(jsonl_path.parent),
        "source":         "claude",
        "date":           target_date,
        "messages":       messages,
        "tokens":         tokens,
        "session_start":  session_start,
        "session_end":    session_end,
        "git_repos":      git_repos,
        "git_ops":        git_ops,
        "pull_requests":  pull_requests,
        "lines_added":    lines_added,
        "lines_removed":  0,
        "files_touched":  sorted(files_touched),
        "tool_invocations":    sum(len(m.get("tools_after", [])) for m in messages if m["role"] == "user"),
        "trivial_timestamps":  trivial_timestamps,
        "git_branch":          git_branch,
    }


def compute_active_minutes(messages: list) -> float:
    """Estimate active engagement time by summing gaps under 5 minutes."""
    timestamps = []
    for m in messages:
        ts = m.get("timestamp", "")
        if ts:
            try:
                timestamps.append(datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                pass

    if not timestamps:
        return 0.0
    if len(timestamps) < 2:
        return 2.0

    total = 0.0
    for i in range(1, len(timestamps)):
        gap = (timestamps[i] - timestamps[i - 1]).total_seconds() / 60
        if gap < 5:
            total += gap
    return round(total, 1)
