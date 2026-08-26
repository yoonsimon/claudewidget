"""The files on disk the widget lives off: config, per-session state, and their lifetimes."""

import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
STATE_DIR = BASE / "state"
DEFAULT_IMAGE = BASE / "assets" / "default.png"
PAUSE_PATH = STATE_DIR / "_paused"  # written by the off menu, read by the hook

DEFAULT_CONFIG = {
    "image_path": "",
    "x": None,
    "y": None,
    "max_size": 128,
    "always_on_top": True,
}

DONE_LINGER = 8  # seconds a finished reply keeps its bubble
# A session that is interrupted or killed never sends Stop, so its last state would
# otherwise sit on screen claiming to be busy. Each state gets its own expiry.
RUNNING_LINGER = 90  # "실행중: <tool>"
WORKING_LINGER = 180  # "작업중..." between tools, where Claude may be thinking
WAITING_LINGER = 10 * 60  # a real permission prompt can wait a long time
STALE_AFTER = 30 * 60  # after this the state file itself is deleted
POLL_MS = 250  # how soon a state change reaches the screen


def read_states():
    """Every session's current state, newest write wins per file.

    Stale files are deleted rather than skipped: they hold a snippet of the last
    reply, so leaving them on disk keeps that text around indefinitely.
    """
    states = []
    if not STATE_DIR.exists():
        return states
    now = time.time()
    for path in STATE_DIR.glob("*.json"):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if now - float(state.get("ts") or 0) > STALE_AFTER:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            continue
        states.append(state)
    return states


def bubble_text(state):
    """The line a state should show, or "" when it should show nothing."""
    kind = state.get("state", "idle")
    text = (state.get("text") or "").strip()
    age = time.time() - float(state.get("ts") or 0)

    if kind == "running":
        tool = state.get("tool") or ""
        if age > (RUNNING_LINGER if tool else WORKING_LINGER):
            return ""
        if text:
            # What Claude just said, with the running tool as a trailing code line.
            return f"{text}\n\n`{tool}` 실행중" if tool else text
        return f"실행중: {tool}" if tool else "작업중..."
    if kind == "waiting":
        return (text or "입력을 기다리고 있어요") if age <= WAITING_LINGER else ""
    if kind == "done" and text:
        return text if age <= DONE_LINGER else ""
    return ""


def load_json(path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        merged = dict(default)
        merged.update(data)
        return merged
    except Exception:
        return dict(default)


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def seconds_until_tomorrow():
    now = time.localtime()
    return 86400 - (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec)
