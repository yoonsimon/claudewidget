"""Claude Code hook -> widget state bridge.

Claude Code runs this on every hook event. It records the session state next to
this file and makes sure the widget process is up, so the widget itself never
has to talk to Claude Code directly.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import single_instance

BASE = Path(__file__).resolve().parent
STATE_DIR = BASE / "state"
WIDGET_PATH = BASE / "widget.py"

MAX_SNIPPET = 260
MAX_LINES = 8
TAIL_BYTES = 1024 * 1024  # only the end of a transcript can hold the last reply
REPLACE_RETRIES = 3
REPLACE_WAIT = 0.02


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
        home = Path.home().resolve()
    except Exception:
        return ""
    for candidate in (path, *path.parents):
        # Checked before the markers, not after: ~/.claude exists on every Claude Code
        # machine, so testing home would label any stray folder with the account name.
        if candidate == home:
            break
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate.name or str(candidate)
    return path.name or str(path)


def state_path(folder):
    """One file per project, so sessions in different folders never overwrite each other."""
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in folder) or "_"
    return STATE_DIR / f"{slug}.json"


def save_state(state):
    """Write the state atomically.

    The widget re-reads these files every 500ms, and on Windows that read can
    collide with the replace (WinError 5), so the swap gets a short bounded retry.
    The temp name carries the pid so two sessions never share one scratch file.
    """
    state["ts"] = time.time()
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = state_path(state.get("folder") or "")
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(REPLACE_RETRIES):
            try:
                tmp.replace(path)
                return
            except OSError:
                if attempt == REPLACE_RETRIES - 1:
                    raise
                time.sleep(REPLACE_WAIT)
    except Exception:
        # A hook must never fail: a non-zero exit blocks the tool call that fired it.
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


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
        # Only the tail can hold the last reply, and a transcript can reach tens of
        # megabytes. Reading it whole cost 1s and 600MB on the largest one here.
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - TAIL_BYTES))
            lines = handle.read().decode("utf-8", "ignore").splitlines()
    except Exception:
        return ""

    for line in reversed(lines[-200:]):
        if '"role":"assistant"' not in line and '"role": "assistant"' not in line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            message = obj.get("message", obj)
            blocks = message.get("content") if isinstance(message, dict) else None
        except Exception:
            continue
        for block in blocks or []:
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
    # Spawning blind costs ~900ms of PIL and tkinter imports before the new process
    # loses the lock and exits, on every single tool call. Probing the lock first
    # costs microseconds.
    if single_instance.is_running():
        return

    # The widget must outlive this hook process, and the detach flags for that are
    # named differently on each platform.
    options = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen(
            [gui_executable(), str(WIDGET_PATH)],
            # Without this the long-lived widget inherits the hook's pipes and its
            # working directory: it would hold the hook's stdout open for days and
            # lock the project folder against deletion.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(BASE),
            **options,
        )
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
        # Trimmed like the Stop path: an untrimmed message would be stored in full.
        text = trim_snippet(payload.get("message", "") or "")
        save_state({"state": "waiting", "tool": "", "text": text, "folder": folder})
    elif event == "Stop":
        text = last_assistant_text(payload.get("transcript_path", ""))
        save_state({"state": "done", "tool": "", "text": text, "folder": folder})
    else:
        return

    ensure_widget_running()


if __name__ == "__main__":
    main()
