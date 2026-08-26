"""Claude Code hook -> widget state bridge.

Claude Code runs this on every hook event. It records the session state in the
per-user state directory and makes sure the widget process is up, so the widget
itself never has to talk to Claude Code directly.

The paths come from state.py rather than being rebuilt here: the widget reads the
same files, and a hook writing somewhere else would just stop producing bubbles.
"""

import json
import os
import sys
import time
from pathlib import Path

import single_instance
from state import PAUSE_PATH, STATE_DIR, state_path

BASE = Path(__file__).resolve().parent
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


def project_from_scratchpad(path):
    """Recover the real project when a session is working inside its scratchpad.

    Claude Code puts scratchpads under <temp>/claude/<encoded-cwd>/<session>/scratchpad,
    and the encoded name ends with the project directory. Without this the bubble
    would be labelled with a throwaway subfolder that the user has never heard of.

    Returns (name, key). The encoded directory is shared by every session of that
    project while the session folder below it is not, so it is what identifies it.
    """
    for parent in path.parents:
        name = parent.name
        # The encoded cwd starts with a drive letter followed by two dashes ("c--...").
        if parent.parent.name == "claude" and len(name) > 3 and name[1:3] == "--":
            tokens = [token for token in name.rstrip("-").split("-") if token]
            if tokens:
                return tokens[-1], str(parent)
    return "", ""


def project_identity(payload):
    """(name for the bubble header, path that identifies the project).

    The hook's cwd can point at a subdirectory, so walk up to the nearest project
    marker instead: working inside me/claude-widget still reads as "me". The project
    root doubles as the identity, because two of them can share a display name.
    """
    cwd = payload.get("cwd") or ""
    if not cwd:
        return "", ""
    try:
        path = Path(cwd).resolve()
        home = Path.home().resolve()
    except Exception:
        return "", ""
    for candidate in (path, *path.parents):
        # Checked before the markers, not after: ~/.claude exists on every Claude Code
        # machine, so testing home would label any stray folder with the account name.
        if candidate == home:
            break
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate.name or str(candidate), str(candidate)
    name, key = project_from_scratchpad(path)
    return (name, key) if name else (path.name or str(path), str(path))


def save_state(state, key=""):
    """Write the state atomically.

    The widget re-reads these files every 500ms, and on Windows that read can
    collide with the replace (WinError 5), so the swap gets a short bounded retry.
    The temp name carries the pid so two sessions never share one scratch file.
    """
    state["ts"] = time.time()
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = state_path(state.get("folder") or "", key)
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


def is_paused():
    """True while the user has switched the widget off.

    Quitting alone would not stick: the next hook would just start it again. The
    pause file holds the timestamp it expires at, or "forever".
    """
    try:
        raw = PAUSE_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return False
    if raw == "forever":
        return True
    try:
        return time.time() < float(raw)
    except ValueError:
        return False


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

    # Cutting inside a fence would leave it open and render the rest as one code block.
    if sum(1 for line in kept if line.startswith("```")) % 2:
        kept.append("```")
    return "\n".join(kept)


def ensure_widget_running():
    # Spawning blind costs ~900ms of PIL and tkinter imports before the new process
    # loses the lock and exits, on every single tool call. Probing the lock first
    # costs microseconds.
    if single_instance.is_running():
        return

    import subprocess  # deferred: only needed on the rare call that actually spawns

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
    if is_paused():
        return

    payload = read_payload()
    event = payload.get("hook_event_name", "")
    folder, key = project_identity(payload)

    if event in ("PreToolUse", "PostToolUse"):
        # The assistant's own line is already in the transcript by the time a tool
        # runs, so the bubble can show what Claude just said instead of a bare tool
        # name. Costs a tail read (single-digit ms).
        text = last_assistant_text(payload.get("transcript_path", ""))
        tool = payload.get("tool_name", "") if event == "PreToolUse" else ""
        save_state({"state": "running", "tool": tool, "text": text, "folder": folder}, key)
    elif event == "Notification":
        # Trimmed like the Stop path: an untrimmed message would be stored in full.
        text = trim_snippet(payload.get("message", "") or "")
        save_state({"state": "waiting", "tool": "", "text": text, "folder": folder}, key)
    elif event == "Stop":
        text = last_assistant_text(payload.get("transcript_path", ""))
        save_state({"state": "done", "tool": "", "text": text, "folder": folder}, key)
    elif event == "SubagentStop":
        # 서브에이전트(워크플로 에이전트 포함)가 하나 끝날 때마다 온다. 메인 응답이 끝난
        # 것은 아니므로 done 이 아니라 running 으로 두고, 몇 개가 끝났는지만 보여준다.
        # 워크플로는 에이전트를 여럿 띄우므로 이 이벤트는 여러 번 발생한다.
        text = last_assistant_text(payload.get("transcript_path", ""))
        save_state({"state": "running", "tool": "에이전트 완료", "text": text, "folder": folder}, key)
    else:
        return

    ensure_widget_running()


if __name__ == "__main__":
    main()
