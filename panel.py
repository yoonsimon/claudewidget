"""The usage panel: the card of bars that opens under the character."""

import json
import queue
import threading
import tkinter as tk

from PIL import Image, ImageDraw

import tokens
import win_layered
from imaging import Canvas, downscale
from theme import (
    BUBBLE_BORDER,
    BUBBLE_FILL,
    BUBBLE_RULE,
    BUBBLE_TEXT,
    FONT_BOLD,
    FONT_REGULAR,
    SS,
    hex_to_rgb,
    load_font,
)
from usage import fetch_usage


def fit_parts(parts, font, width, separator=", "):
    """As many of the breakdown pieces as fit on one line, in the order given."""
    text = ""
    for part in parts:
        candidate = f"{text}{separator}{part}" if text else part
        if font.getlength(candidate) > width:
            break
        text = candidate
    return text


class UsagePanel:
    WIDTH = 232
    REFRESH_MS = 5 * 60 * 1000
    DRAIN_MS = 120  # how soon a finished lookup reaches the screen

    def __init__(self, master):
        self.master = master
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.canvas = Canvas(self.win)
        self.win.withdraw()
        self.image = None
        self._signature = None
        self.data = {"loading": True}
        self.open = False
        self._refresh_job = None
        # Background lookups hand their results over here instead of touching Tk.
        self.results = queue.Queue()
        self._pending = 0
        self._round = 0  # stamps results so a cancelled round cannot resurface stale data
        self._on_done = None
        self._tokens = None
        self._drain_job = None

    def render(self):
        # Dragging the character with the panel open re-rendered it on every mouse
        # move, supersampled at 3x. Nothing changes unless the data does.
        signature = json.dumps(self.data, sort_keys=True, default=str)
        if signature == self._signature and self.image is not None:
            return self.image.size
        self._signature = signature

        pad, radius, gap = 14, 14, 9
        bar_h, row_gap = 6, 13
        font_title = load_font(FONT_BOLD, 12 * SS)
        font_label = load_font(FONT_REGULAR, 12 * SS)
        font_value = load_font(FONT_REGULAR, 12 * SS)

        width = self.WIDTH * SS
        inner = width - pad * 2 * SS
        line_h = sum(font_label.getmetrics())

        rows = self.data.get("rows") or []
        # The token line has no bar to draw: there is no quota to be a fraction of,
        # so a bar would have to invent its own denominator. A rule sets it apart
        # from the bars instead, and the breakdown sits where a reset time would.
        token = self.data.get("tokens")
        detail = fit_parts(token.get("parts") or [], font_label, inner) if token else ""

        if "error" in self.data:
            # Blocked shows two lines; the others one. Keep this in step with render.
            error_lines = 2 if self.data["error"] in ("http-403", "http-429") else 1
            body_h = error_lines * (line_h + 3 * SS) + gap * SS - 3 * SS
        elif self.data.get("loading"):
            body_h = line_h + gap * SS
        else:
            body_h = len(rows) * (line_h * 2 + bar_h * SS + gap * SS) + max(0, len(rows) - 1) * row_gap * SS
        if token:
            body_h += row_gap * SS * 2 + SS + line_h + (line_h if detail else 0)

        height = pad * 2 * SS + line_h + gap * SS + body_h

        im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle(
            [0, 0, width - 1, height - 1], radius=radius * SS, fill=BUBBLE_FILL, outline=BUBBLE_BORDER, width=SS
        )

        x = pad * SS
        y = pad * SS
        d.text((x, y), "사용량", font=font_title, fill=BUBBLE_TEXT)
        y += line_h + gap * SS

        if "error" in self.data:
            err = self.data["error"]
            # A 403 or 429 on this endpoint is Anthropic refusing the request, which
            # is the case worth calling out rather than a generic failure line.
            blocked = err in ("http-403", "http-429")
            lines = {
                "no-credentials": ["로그인 정보를 찾을 수 없습니다"],
                "unreachable": ["사용량을 가져오지 못했습니다"],
                "no-data": ["표시할 사용량이 없습니다"],
                "http-403": ["이런! ㅠㅠ", "Anthropic 에 의해 차단당했습니다..."],
                "http-429": ["이런! ㅠㅠ", "요청이 너무 많아 잠시 막혔습니다..."],
            }.get(err, [f"오류: {err}"])
            colour = (200, 70, 70) if blocked else (140, 140, 148)
            for line in lines:
                d.text((x, y), line, font=font_label, fill=colour)
                y += line_h + 3 * SS
            y += gap * SS - 3 * SS
        elif self.data.get("loading"):
            d.text((x, y), "불러오는 중...", font=font_label, fill=(140, 140, 148))
            y += line_h + gap * SS
        else:
            for index, row in enumerate(rows):
                if index:
                    y += row_gap * SS
                percent = max(0.0, min(100.0, row["percent"]))
                value = f"{percent:.0f}%"
                d.text((x, y), row["label"], font=font_label, fill=BUBBLE_TEXT)
                d.text((x + inner - font_value.getlength(value), y), value, font=font_value, fill=BUBBLE_TEXT)
                y += line_h + 5 * SS

                d.rounded_rectangle(
                    [x, y, x + inner, y + bar_h * SS], radius=bar_h * SS // 2, fill=(238, 238, 242)
                )
                filled = int(inner * percent / 100)
                if filled > bar_h * SS:
                    d.rounded_rectangle(
                        [x, y, x + filled, y + bar_h * SS],
                        radius=bar_h * SS // 2,
                        fill=hex_to_rgb(row["color"]),
                    )
                y += bar_h * SS + 4 * SS

                if row.get("reset"):
                    d.text((x, y), row["reset"], font=font_label, fill=(150, 150, 158))
                y += line_h

        if token:
            y += row_gap * SS
            d.rectangle([x, y, x + inner, y + SS - 1], fill=BUBBLE_RULE)
            y += SS + row_gap * SS
            amount = token.get("value") or "-"
            d.text((x, y), token.get("label") or "오늘 토큰", font=font_label, fill=BUBBLE_TEXT)
            d.text((x + inner - font_value.getlength(amount), y), amount, font=font_value, fill=BUBBLE_TEXT)
            y += line_h
            if detail:
                d.text((x, y), detail, font=font_label, fill=(150, 150, 158))

        self.image = downscale(im)
        return self.image.size

    def place_under(self, char_x, char_y, char_w, char_h):
        w, h = self.render()
        x = char_x + char_w // 2 - w // 2
        y = char_y + char_h + 6

        # Clamped against the monitor the character is on, not the whole virtual
        # desktop: with two monitors the desktop rectangle is taller than either
        # screen, so a whole-desktop clamp never fires.
        area = win_layered.work_area(char_x + char_w // 2, char_y + char_h // 2)
        if area:
            left, top, right, bottom = area
            if y + h > bottom:
                y = char_y - h - 6  # no room below: flip above the character
            y = max(top, min(y, bottom - h))
            x = max(left, min(x, right - w))
        self.canvas.show(self.image, x, y)

    def show(self):
        self.open = True
        self.win.deiconify()

    def hide(self):
        self.open = False
        self.win.withdraw()
        if self._refresh_job:
            self.master.after_cancel(self._refresh_job)
            self._refresh_job = None
        if self._drain_job:
            self.master.after_cancel(self._drain_job)
            self._drain_job = None

    def refresh(self, on_done=None):
        """Start the background lookups. Returns immediately; results arrive via drain().

        The workers used to call master.after(0, ...) themselves. Tcl is not
        thread-safe and after() raises RuntimeError once the root is gone, and
        under pythonw that traceback has nowhere to go: the process would sit
        there holding the single-instance lock, and no later hook could bring the
        widget back. So a worker now only ever touches a Queue.
        """
        self._on_done = on_done
        self._round += 1
        if self._tokens is not None:
            # The caller resets data to {"loading": True} on every refresh; keep the
            # last token line visible instead of blinking it out for a second.
            self.data.setdefault("tokens", self._tokens)
        self._start("usage", fetch_usage)
        self._start("tokens", tokens.today_row)

    def _start(self, kind, work):
        stamp = self._round

        def worker():
            try:
                result = work()
            except Exception as exc:  # a lookup must never take the widget down
                result = {"error": str(exc) or exc.__class__.__name__}
            self.results.put((stamp, kind, result))

        self._pending += 1
        threading.Thread(target=worker, daemon=True).start()
        self._pump_later()

    def drain(self):
        """Apply whatever the workers finished. Tk thread only.

        Safe to call on an empty queue, which is the normal case: it does nothing
        and returns False. True means self.data changed and the panel needs to be
        placed again.
        """
        changed = False
        while True:
            try:
                stamp, kind, result = self.results.get_nowait()
            except queue.Empty:
                break
            self._pending = max(0, self._pending - 1)
            if stamp != self._round:
                # Left over from a round that hide() cancelled: applying it would put
                # last session's numbers on screen and arm schedule_refresh with them.
                continue
            changed = True
            if kind == "tokens":
                self._tokens = result if result.get("value") else None
                if self._tokens is None:
                    self.data.pop("tokens", None)
                else:
                    self.data["tokens"] = self._tokens
            else:
                # A usage result replaces the rows; the token line outlives it.
                keep = self.data.get("tokens")
                self.data = dict(result)
                if keep is not None:
                    self.data["tokens"] = keep
        if changed and self._on_done:
            self._on_done(self.data)
        return changed

    def _pump_later(self):
        """Keep a drain scheduled while lookups are outstanding.

        This chain is the only consumer of the queue: nothing else calls drain()
        on a timer. Scheduled from the Tk thread, which is what makes it safe
        where the old worker-side after(0, ...) was not, and it stops on its own
        once nothing is pending.
        """
        if self._drain_job is not None:
            return
        try:
            self._drain_job = self.master.after(self.DRAIN_MS, self._pump)
        except tk.TclError:
            self._drain_job = None

    def _pump(self):
        self._drain_job = None
        self.drain()
        if self._pending:
            self._pump_later()

    def schedule_refresh(self, callback):
        if self._refresh_job:
            self.master.after_cancel(self._refresh_job)
        self._refresh_job = self.master.after(self.REFRESH_MS, callback)
