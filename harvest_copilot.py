"""
harvest_copilot.py — Read GitHub Copilot session event files and extract structured activity data.

Sessions are stored at ~/.copilot/session-state/<uuid>/events.jsonl
Each session directory also contains workspace.yaml with workspace metadata.
"""
import json
import re as _re
from datetime import datetime
from pathlib import Path

SESSION_DIR = Path.home() / ".copilot" / "session-state"

# Extensions that represent hand-written logic (mirrors harvest_claude.py)
_LOGIC_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".cs",
    ".cpp", ".c", ".h", ".hpp", ".sh", ".bash", ".zsh", ".ps1", ".rb",
    ".php", ".r", ".sql", ".kt", ".swift", ".dart", ".scala", ".ex", ".exs",
    ".vue", ".svelte", ".tf", ".hcl",
}


def _split_lines_by_type(files_modified: set, total_lines: int) -> tuple[int, int]:
    """Estimate (logic_lines, boilerplate_lines) from the set of modified filenames.

    Copilot only reports total linesAdded, not per-file counts.  We infer the split
    by calculating what fraction of modified files are logic files and applying that
    fraction to the total line count.
    """
    if not files_modified or total_lines <= 0:
        return 0, 0
    logic_files = sum(1 for f in files_modified if Path(f).suffix.lower() in _LOGIC_EXTS)
    frac = logic_files / len(files_modified)
    logic = round(total_lines * frac)
    return logic, total_lines - logic

_APPROVALS = {
    "yes", "y", "yep", "yeah", "yup", "no", "n", "nope",
    "ok", "okay", "sure", "fine", "right", "correct",
    "proceed", "go ahead", "go for it", "do it", "do that",
    "looks good", "sounds good", "that's fine", "that works",
    "approved", "continue", "perfect", "great", "good",
    "got it", "understood", "makes sense",
}

# Intent categories loaded from prompts/intent_classification.txt at import time.
# Falls back to an empty list; _classify_intent defaults to "Iterating" when no match.
from analyze import load_intent_categories as _load_intent_categories
_INTENT_CATEGORIES = _load_intent_categories()


def _is_approval(text: str) -> bool:
    cleaned = text.strip().rstrip(".!").lower()
    if _re.fullmatch(r'[\w.+-]+@[\w-]+\.[a-z]{2,}', cleaned):
        return True
    if len(cleaned.split()) > 8:
        return False
    return cleaned in _APPROVALS


def _strip_injected_context(text: str) -> str:
    text = _re.sub(r'<current_datetime>.*?</current_datetime>\s*', '', text, flags=_re.DOTALL)
    text = _re.sub(r'<reminder>.*?</reminder>\s*', '', text, flags=_re.DOTALL)
    text = _re.sub(r'<[a-z_]+>.*?</[a-z_]+>\s*', '', text, flags=_re.DOTALL)
    return text.strip()


def _classify_intent(text: str) -> str:
    for intent, pattern in _INTENT_CATEGORIES:
        if pattern.search(text):
            return intent
    return "Iterating"   # most residual interactions in an agentic session are refinements


def _read_workspace(path: Path) -> dict:
    result = {}
    if not path.exists():
        return result
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if ": " in line and not line.startswith(" "):
                    k, _, v = line.partition(": ")
                    result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


def get_sessions_for_date(target_date: str) -> list:
    """
    Find all Copilot sessions with activity on target_date (YYYY-MM-DD).
    Returns a list of session dicts compatible with the whatidid schema.
    """
    sessions = []

    if not SESSION_DIR.exists():
        return sessions

    for session_dir in SESSION_DIR.iterdir():
        if not session_dir.is_dir():
            continue

        events_file    = session_dir / "events.jsonl"
        workspace_file = session_dir / "workspace.yaml"

        if not events_file.exists():
            continue

        workspace = _read_workspace(workspace_file)

        # Parse all events
        events = []
        try:
            with open(events_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            continue

        if not events:
            continue

        # Quick check: does this session touch the target date?
        has_target_date = any(
            e.get("timestamp", "")[:10] == target_date for e in events
        )
        if not has_target_date:
            continue

        # Pull session context from session.start
        session_ctx = {}
        for e in events:
            if e.get("type") == "session.start":
                session_ctx = e.get("data", {}).get("context", {})
                break

        cwd        = session_ctx.get("cwd", "")        or workspace.get("cwd", "")
        repository = session_ctx.get("repository", "") or workspace.get("repository", "")
        branch     = session_ctx.get("branch", "")     or workspace.get("branch", "")
        project_name = Path(cwd).name if cwd else session_dir.name[:12]

        # Extract user messages and tool summaries
        messages           = []
        trivial_timestamps = []   # timestamps of approval/trivial messages
        session_start = None
        session_end   = None
        git_ops_list  = []
        files_touched = set()

        for e in events:
            ts = e.get("timestamp", "")
            if not ts or ts[:10] != target_date:
                continue

            if not session_start:
                session_start = ts
            session_end = ts

            etype = e.get("type", "")

            if etype == "user.message":
                raw = e.get("data", {}).get("content", "")
                if isinstance(raw, str) and raw.strip():
                    text = _strip_injected_context(raw).strip()
                    if text and not _is_approval(text):
                        messages.append({
                            "role":        "user",
                            "text":        text,
                            "timestamp":   ts,
                            "tools_after": [],
                            "intent":      _classify_intent(text),
                        })
                    elif text:
                        trivial_timestamps.append(ts)

            elif etype == "assistant.message":
                tool_requests = e.get("data", {}).get("toolRequests", [])
                for tr in tool_requests:
                    summary = tr.get("intentionSummary") or tr.get("name", "")
                    if summary and messages and messages[-1]["role"] == "user":
                        messages[-1]["tools_after"].append(summary)

                    tool_name_lower = (tr.get("name") or "").lower()
                    if tool_name_lower in ("edit", "create", "write"):
                        path_str = (tr.get("input", {}) or {}).get("path", "")
                        if not path_str and summary:
                            pm = _re.search(r'[\\/]([^\\/]+\.\w{1,8})\.?\s*$', summary)
                            if pm:
                                path_str = pm.group(1)
                        if path_str:
                            files_touched.add(path_str.replace("\\", "/"))

            elif etype == "tool.execution_complete":
                tool_name = e.get("data", {}).get("toolName", "")
                if "pull_request" in tool_name.lower() or "pr" in tool_name.lower():
                    if e.get("data", {}).get("success", False):
                        git_ops_list.append("pr")

        # Detect PRs and commits from user messages
        _pr_keywords = {"create the pr", "create a pr", "create pr", "gh pr create",
                        "pull request", "open a pr", "open pr", "submit pr"}
        _commit_keywords = {"commit", "git commit", "push to remote", "push to origin",
                            "push it", "commit and push"}
        for m in messages:
            txt = m["text"].lower().strip()
            tools_text = " ".join(m.get("tools_after", [])).lower()
            if any(k in txt for k in _pr_keywords) or "create pr" in tools_text:
                if "pr" not in git_ops_list[-1:]:
                    git_ops_list.append("pr")
            if any(k in txt for k in _commit_keywords) or "commit" in tools_text:
                if "commit" not in git_ops_list[-1:]:
                    git_ops_list.append("commit")

        # Pull shutdown metrics
        tokens           = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
        premium_requests = 0
        code_changes     = {}
        model_used       = ""

        for e in events:
            if e.get("type") == "session.shutdown":
                d = e.get("data", {})
                premium_requests = d.get("totalPremiumRequests", 0)
                code_changes     = d.get("codeChanges", {})
                model_used       = d.get("currentModel", "")
                for model_data in d.get("modelMetrics", {}).values():
                    usage = model_data.get("usage", {})
                    tokens["input"]          += usage.get("inputTokens", 0)
                    tokens["output"]         += usage.get("outputTokens", 0)
                    tokens["cache_read"]     += usage.get("cacheReadTokens", 0)
                    tokens["cache_creation"] += usage.get("cacheWriteTokens", 0)
                break

        tokens["total"] = sum(tokens.values())

        shutdown_files = set(code_changes.get("filesModified", []))
        all_modified   = shutdown_files | files_touched
        lines_added    = code_changes.get("linesAdded", 0)
        lines_removed  = code_changes.get("linesRemoved", 0)
        lines_logic, lines_boilerplate = _split_lines_by_type(all_modified, lines_added)

        user_messages = [m for m in messages if m["role"] == "user"]
        if not user_messages:
            continue

        git_repos = [repository] if repository else []

        sessions.append({
            "session_id":        session_dir.name,
            "project":           project_name,
            "project_path":      cwd or str(session_dir),
            "repository":        repository,
            "branch":            branch,
            "source":            "copilot",
            "date":              target_date,
            "messages":          messages,
            "tokens":            tokens,
            "premium_requests":  premium_requests,
            "code_changes":      code_changes,
            "model_used":        model_used,
            "session_start":     session_start,
            "session_end":       session_end,
            "git_repos":         git_repos,
            "git_ops":           git_ops_list,
            "lines_added":        lines_added,
            "lines_logic":        lines_logic,
            "lines_boilerplate":  lines_boilerplate,
            "lines_removed":      lines_removed,
            "workspace_summary": workspace.get("summary", ""),
            "tool_invocations":   sum(len(m.get("tools_after", [])) for m in messages if m["role"] == "user"),
            "files_touched":      sorted(all_modified),
            "trivial_timestamps": trivial_timestamps,
        })

    return sessions


def compute_elapsed_minutes(session_start: str, session_end: str) -> float:
    if not session_start or not session_end:
        return 0
    try:
        fmt = "%Y-%m-%dT%H:%M:%S"
        t0 = datetime.strptime(session_start[:19], fmt)
        t1 = datetime.strptime(session_end[:19], fmt)
        return max(0, (t1 - t0).total_seconds() / 60)
    except Exception:
        return 0


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
