"""The files on disk the widget lives off: config, per-session state, and their lifetimes.

Everything that decides *where* state lives is here. The hook bridge imports these
paths rather than deriving its own: if the two ever disagreed the hook would write
files the widget never reads, and no bubble would appear at all.
"""

import json
import os
import sys
import time
import zlib
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
DEFAULT_IMAGE = BASE / "assets" / "default.png"


def _state_root():
    """Per-user state directory, deliberately outside the widget folder.

    These files hold a snippet of what Claude just said. Kept next to the code that
    text travels with the folder: into a OneDrive or Drive sync root, into a zip
    handed to someone else, into every account's reach on a shared PC. A per-user
    directory keeps it with the user who produced it.
    """
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        root = Path(local) if local else Path.home() / "AppData" / "Local"
        return root / "claude-widget" / "state"
    return Path.home() / ".cache" / "claude-widget" / "state"


STATE_DIR = _state_root()
PAUSE_PATH = STATE_DIR / "_paused"  # written by the off menu, read by the hook
LEGACY_STATE_DIR = BASE / "state"  # where state lived before the move

DEFAULT_CONFIG = {
    "image_path": "",
    "x": None,
    "y": None,
    "max_size": 128,
    "opacity": 1.0,
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


def migrate_legacy_state():
    """Drain a pre-move state/ folder into the per-user directory, once.

    Called at import so the hook and the widget land on the same files whichever one
    starts first. The common case is one failed is_dir() and nothing else.
    """
    try:
        if not LEGACY_STATE_DIR.is_dir():
            return
        files = [path for path in LEGACY_STATE_DIR.iterdir() if path.is_file()]
        if files:
            # shutil is a ~50ms import and this runs inside every hook, so it is only
            # paid when there is actually something to move. If a stray non-file entry
            # keeps rmdir failing below, re-runs cost one iterdir and nothing more.
            import shutil

            STATE_DIR.mkdir(parents=True, exist_ok=True)
            for path in files:
                try:
                    # shutil rather than replace(): LOCALAPPDATA and the widget folder
                    # can sit on different volumes, where a plain rename fails outright.
                    # _paused moves with the rest, or switching off would silently undo.
                    shutil.move(str(path), str(STATE_DIR / path.name))
                except Exception:
                    pass
        LEGACY_STATE_DIR.rmdir()
    except Exception:
        # Never fatal: this also runs inside the hook, where raising blocks a tool call.
        pass


migrate_legacy_state()


def state_path(folder, key=""):
    """One file per project, so sessions in different folders never overwrite each other.

    The folder name alone is not enough: work/api and personal/api would both land on
    api.json and clobber each other's bubble. The suffix is a hash of the project path.
    The name on screen comes from "folder" inside the JSON, so this stays invisible.
    """
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in folder) or "_"
    ident = key or folder
    # Casing is not identity here: hook payloads deliver one project as both
    # "C:\Users\...\me" and "c:\Users\...\me", which would split it into two files.
    if sys.platform == "win32":
        ident = ident.lower()
    # crc32, not a digest: importing hashlib costs 105ms on this machine (it loads
    # OpenSSL) against 0.09ms for zlib, and the hook pays that on every tool call.
    # Telling a handful of project paths apart needs no cryptographic strength.
    digest = "%08x" % (zlib.crc32(ident.encode("utf-8", "replace")) & 0xFFFFFFFF)
    return STATE_DIR / f"{slug}-{digest}.json"


# path -> (mtime_ns, size, parsed state). The widget re-reads every file four times a
# second, and a state file only changes when its session does something.
_parsed = {}


def read_states():
    """Every session's current state, newest write wins per file.

    Unchanged files come back from the parse cache; mtime alone is not enough to trust
    on Windows, where a same-tick replace can keep it, so size guards it too.

    Stale files are deleted rather than skipped: they hold a snippet of the last
    reply, so leaving them on disk keeps that text around indefinitely.
    """
    states = []
    if not STATE_DIR.exists():
        _parsed.clear()
        return states
    now = time.time()
    live = set()
    for path in STATE_DIR.glob("*.json"):
        key = str(path)
        try:
            info = path.stat()
        except OSError:
            continue
        stamp = (info.st_mtime_ns, info.st_size)
        cached = _parsed.get(key)
        if cached is not None and cached[0] == stamp:
            state = cached[1]
        else:
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            # Handed out as-is on every later call, so callers must treat it read-only.
            _parsed[key] = (stamp, state)
        live.add(key)
        if now - float(state.get("ts") or 0) > STALE_AFTER:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            else:
                live.discard(key)
            continue
        states.append(state)
    if len(_parsed) != len(live):
        # Drop entries for files that expired or were removed behind our back.
        for key in [k for k in _parsed if k not in live]:
            del _parsed[key]
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
