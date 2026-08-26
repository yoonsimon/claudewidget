"""Today's token usage, summed out of the Claude Code transcripts.

Every assistant turn lands as one line in ~/.claude/projects/**/*.jsonl carrying
`message.usage`. Three things make the naive sum either wrong or slow:

  - One API response is written once per content block, so the same
    `message.id` shows up two or three times (one 8MB transcript here: 842 usage
    lines, 552 real responses). Summing every line gave 622M tokens against a
    real 271M, a 2.3x overcount. Ids are remembered, which has the second
    benefit of making a re-read of a file harmless.
  - `timestamp` is UTC, "today" is local. In KST the local day starts at 15:00
    UTC the day before, so the cut has to be a real comparison and not a
    date-string match.
  - There are 636 transcripts and 362MB of them on this machine. Only files
    written since local midnight can hold a line from today, and those are read
    from the byte offset the previous run stopped at.

No cost figure: see the note on COUNTED below.
"""

import json
import os
import threading
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_ROOT = Path.home() / ".claude" / "projects"
CACHE_VERSION = 1

# Pre-filter before json.loads: in a long transcript most lines are tool results,
# far bigger than the assistant turns and unable to hold usage anyway.
NEEDLE = b'"usage"'

# `<synthetic>` marks messages Claude Code produced locally (API errors and the
# like). They carry an all-zero usage block, so they only add a phantom model.
SKIP_MODELS = {"<synthetic>"}

# The four counters that add up to the token total. `thinking` is tracked too but
# never added: it is a subset of output_tokens, not another bucket.
#
# Deliberately no price table. Cost would have to be hardcoded per model, and the
# model strings seen on this machine today already include claude-fable-5 and
# claude-opus-4-8, names with no price to look up. An unknown model would
# silently contribute 0 and quietly understate the total, and the rows above this
# line already show the limit that actually binds a subscription (percent of
# session and weekly). A wrong currency amount is worse than none, so the row
# reports tokens only.
COUNTED = ("input", "output", "cache_write", "cache_read")

SHORT_NAMES = (("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"), ("fable", "Fable"))


def cache_path():
    """Where the scan cache lives.

    Computed here rather than imported: this is a cache, not widget state, and it
    has to survive the state folder moving.
    """
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    else:
        root = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(root) / "claude-widget" / "token-cache.json"


def day_start(now=None):
    """Local midnight, as a POSIX timestamp."""
    now = now or datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _blank():
    return {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "thinking": 0, "messages": 0}


def _load_cache():
    try:
        return json.loads(cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache):
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # pid alone is shared by every thread in the widget process, and refresh
        # runs the scan on a worker thread: two overlapping saves would fight over
        # one temp file (reproduced as PermissionError under load).
        tmp = path.with_suffix(".%d-%d.tmp" % (os.getpid(), threading.get_ident()))
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass  # a cache that cannot be written only costs speed


def _candidates(since):
    """(path, stat) for every transcript that could hold a line from today."""
    found = []
    try:
        paths = list(LOG_ROOT.rglob("*.jsonl"))
    except Exception:
        return found
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime >= since:
            found.append((path, stat))
    return found


def _scan(path, offset, since, seen, models):
    """Fold today's records from `offset` onward into `models`.

    Returns (new offset, bytes read, ok). ok=False means the file could not even be
    opened, and the caller must not stamp it as seen: recording the current mtime
    for a failed read would mark the file "unchanged" and silently drop its tokens
    for the rest of the day. Reads line by line rather than slurping: the biggest
    transcript here is 78MB and this runs inside the widget process. A trailing
    line without its newline is a write in progress, so it is left for the next run.
    """
    read = 0
    try:
        handle = open(path, "rb")
    except OSError:
        return offset, 0, False
    with handle as f:
        try:
            f.seek(offset)
        except OSError:
            return offset, 0, False
        for raw in f:
            if not raw.endswith(b"\n"):
                break
            read += len(raw)
            if NEEDLE not in raw:
                continue
            try:
                record = json.loads(raw)
            except Exception:
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            model = message.get("model") or "unknown"
            if model in SKIP_MODELS:
                continue
            stamp = _stamp(record.get("timestamp"))
            if stamp is None or stamp < since:
                continue
            # One response, several content blocks, one line each.
            key = message.get("id") or record.get("requestId")
            if key:
                if key in seen:
                    continue
                seen.add(key)
            bucket = models.setdefault(model, _blank())
            bucket["input"] += int(usage.get("input_tokens") or 0)
            bucket["output"] += int(usage.get("output_tokens") or 0)
            bucket["cache_write"] += int(usage.get("cache_creation_input_tokens") or 0)
            bucket["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
            details = usage.get("output_tokens_details")
            if isinstance(details, dict):
                bucket["thinking"] += int(details.get("thinking_tokens") or 0)
            bucket["messages"] += 1
    return offset + read, read, True


def collect_today(now=None):
    """Token totals for the local day, per model.

    {'total': int, 'models': {name: counters}, 'messages': int,
     'files_scanned': int, 'bytes_read': int, 'elapsed_ms': int}
    """
    started = time.monotonic()
    since = day_start(now)
    today = datetime.fromtimestamp(since).strftime("%Y-%m-%d")

    cache = _load_cache()
    if cache.get("version") != CACHE_VERSION or cache.get("day") != today:
        # An offset is only meaningful next to the totals it produced: it says "these
        # bytes are already counted". Dropping the totals for a new day while keeping
        # the offsets skips every file that has not been appended to since, and the
        # panel reads 0. So the day rolling over costs one full scan of the day's
        # candidates, which is the same work a first run does anyway.
        cache = {"version": CACHE_VERSION, "day": today, "files": {}}

    known = cache.get("files") or {}
    models = {name: {**_blank(), **bucket} for name, bucket in (cache.get("models") or {}).items()}
    seen = set(cache.get("ids") or [])

    kept = {}
    scanned = 0
    read_total = 0
    for path, stat in _candidates(since):
        key = str(path)
        entry = known.get(key) or {}
        if entry.get("mtime") == stat.st_mtime and entry.get("size") == stat.st_size:
            kept[key] = entry  # untouched since the last run
            continue
        offset = int(entry.get("offset") or 0)
        if stat.st_size < offset:
            offset = 0  # truncated or replaced; the id set keeps the re-read honest
        offset, read, ok = _scan(path, offset, since, seen, models)
        if not ok:
            # Keep the stale entry (or none): stamping the current mtime here would
            # mark an unread file as done and lose its tokens for the day.
            if entry:
                kept[key] = entry
            continue
        kept[key] = {"mtime": stat.st_mtime, "size": stat.st_size, "offset": offset}
        scanned += 1
        read_total += read

    # Only today's candidates are carried over, so the cache stays the size of a
    # day's work instead of growing with every transcript ever written.
    cache["files"] = kept
    cache["models"] = models
    cache["ids"] = sorted(seen)
    _save_cache(cache)

    return {
        "total": sum(bucket[name] for bucket in models.values() for name in COUNTED),
        "models": models,
        "messages": sum(bucket["messages"] for bucket in models.values()),
        "files_scanned": scanned,
        "bytes_read": read_total,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def short_model(name):
    lowered = (name or "").lower()
    for needle, label in SHORT_NAMES:
        if needle in lowered:
            return label
    return name or "기타"


def format_count(value):
    # Band edges account for display rounding, so neighbours never regress in
    # precision: 9,999 and 10,000 both read "10.0K", 999,500 turns into "1.0M"
    # instead of the old "1000K".
    if value < 1_000:
        return str(int(value))
    thousands = value / 1_000
    if thousands < 99.95:
        return "%.1fK" % thousands
    if thousands < 999.5:
        return "%.0fK" % thousands
    return "%.1fM" % (value / 1_000_000)


def today_row(now=None):
    """The one row the panel draws: {'label', 'value', 'parts'}.

    `parts` is the per-model breakdown as separate pieces, biggest first, so the
    panel can keep as many as fit on the line. Never raises: a dash is better
    than losing the panel.
    """
    row = {"label": "오늘 토큰", "value": "-", "parts": []}
    try:
        result = collect_today(now)
    except Exception:
        return row
    row["value"] = format_count(result["total"])
    merged = {}
    for name, bucket in result["models"].items():
        total = sum(bucket[counter] for counter in COUNTED)
        if total:
            label = short_model(name)
            merged[label] = merged.get(label, 0) + total
    row["parts"] = [
        "%s %s" % (label, format_count(total))
        for label, total in sorted(merged.items(), key=lambda item: -item[1])
    ]
    return row


if __name__ == "__main__":
    if "--cold" in sys.argv:
        cache_path().unlink(missing_ok=True)
        print("캐시 삭제:", cache_path())
    result = collect_today()
    order = sorted(result["models"].items(), key=lambda item: -sum(item[1][c] for c in COUNTED))
    for name, bucket in order:
        print(
            "  %-20s %7s  in %9d out %8d write %9d read %10d think %7d  x%d"
            % (
                name,
                format_count(sum(bucket[c] for c in COUNTED)),
                bucket["input"],
                bucket["output"],
                bucket["cache_write"],
                bucket["cache_read"],
                bucket["thinking"],
                bucket["messages"],
            )
        )
    print("  합계 %s (%d 토큰, %d 응답)" % (format_count(result["total"]), result["total"], result["messages"]))
    print(
        "  파일 %d개 / %.1fMB 읽음, %dms"
        % (result["files_scanned"], result["bytes_read"] / 1048576, result["elapsed_ms"])
    )
    print("  패널 행: %s" % (today_row(),))
