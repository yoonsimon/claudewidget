"""Claude Code hook -> widget state bridge.

Claude Code runs this on every hook event. It records the session state next to
this file and makes sure the widget process is up, so the widget itself never
has to talk to Claude Code directly.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE_DIR = BASE / "state"
WIDGET_PATH = BASE / "widget.py"

MAX_SNIPPET = 260
MAX_LINES = 8


def gui_executable():
    """Interpreter that starts the widget without flashing a console window."""
    current = Path(sys.executable)
    for name in ("pythonw.exe", "pythonw"):
        candidate = current.with_name(name)
        if candidate.exists():
            return str(candidate)
    return str(current)


PROJECT_MARKERS = (".git", ".claude", "CLAUDE.md")


def project_folder(payload):
    """Project name for the bubble header.

    The hook's cwd can point at a subdirectory, so walk up to the nearest project
    marker instead: working inside me/claude-widget still reads as "me".
    """
    cwd = payload.get("cwd") or ""
    if not cwd:
        return ""
    try:
        path = Path(cwd).resolve()
    except Exception:
        return ""
    for candidate in (path, *path.parents):
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate.name or str(candidate)
    return path.name or str(path)


def state_path(folder):
    """One file per project, so sessions in different folders never overwrite each other."""
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in folder) or "_"
    return STATE_DIR / f"{slug}.json"


def save_state(state):
    state["ts"] = time.time()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = state_path(state.get("folder") or "")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_payload():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def last_assistant_text(transcript_path):
    if not transcript_path:
        return ""
    path = Path(transcript_path)
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""

    for line in reversed(lines[-200:]):
        if '"role":"assistant"' not in line and '"role": "assistant"' not in line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        message = obj.get("message", obj)
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                return trim_snippet(block["text"])
    return ""


def trim_snippet(text):
    """Shorten a reply for the bubble while keeping its markdown line structure."""
    lines = [" ".join(line.split()) for line in text.strip().split("\n")]
    lines = [line for line in lines if line or True]  # keep blanks: they separate blocks

    kept = []
    used = 0
    for line in lines[:MAX_LINES]:
        if used + len(line) > MAX_SNIPPET:
            remaining = max(0, MAX_SNIPPET - used)
            if remaining > 10:
                kept.append(line[:remaining] + "…")
            elif kept:
                kept[-1] = kept[-1].rstrip("…") + "…"
            break
        kept.append(line)
        used += len(line)
    else:
        if len(lines) > MAX_LINES:
            kept.append("…")

    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)


def ensure_widget_running():
    # The widget must outlive this hook process, and the detach flags for that are
    # named differently on each platform.
    options = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen([gui_executable(), str(WIDGET_PATH)], **options)
    except Exception:
        pass


def main():
    payload = read_payload()
    event = payload.get("hook_event_name", "")
    folder = project_folder(payload)

    if event == "PreToolUse":
        save_state({"state": "running", "tool": payload.get("tool_name", ""), "text": "", "folder": folder})
    elif event == "PostToolUse":
        save_state({"state": "running", "tool": "", "text": "", "folder": folder})
    elif event == "Notification":
        save_state({"state": "waiting", "tool": "", "text": payload.get("message", ""), "folder": folder})
    elif event == "Stop":
        text = last_assistant_text(payload.get("transcript_path", ""))
        save_state({"state": "done", "tool": "", "text": text, "folder": folder})
    else:
        return

    ensure_widget_running()


if __name__ == "__main__":
    main()
