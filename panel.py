"""The usage panel: the card of bars that opens under the character."""

import json
import threading
import tkinter as tk

from PIL import Image, ImageDraw

import win_layered
from imaging import Canvas, downscale
from theme import (
    BUBBLE_BORDER,
    BUBBLE_FILL,
    BUBBLE_TEXT,
    FONT_BOLD,
    FONT_REGULAR,
    SS,
    hex_to_rgb,
    load_font,
)
from usage import fetch_usage


class UsagePanel:
    WIDTH = 232
    REFRESH_MS = 5 * 60 * 1000

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
        if "error" in self.data:
            body_h = line_h + gap * SS
        elif self.data.get("loading"):
            body_h = line_h + gap * SS
        else:
            body_h = len(rows) * (line_h * 2 + bar_h * SS + gap * SS) + max(0, len(rows) - 1) * row_gap * SS

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
            message = {
                "no-credentials": "로그인 정보를 찾을 수 없습니다",
                "unreachable": "사용량을 가져오지 못했습니다",
                "no-data": "표시할 사용량이 없습니다",
            }.get(self.data["error"], f"오류: {self.data['error']}")
            d.text((x, y), message, font=font_label, fill=(140, 140, 148))
        elif self.data.get("loading"):
            d.text((x, y), "불러오는 중...", font=font_label, fill=(140, 140, 148))
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

    def refresh(self, on_done):
        def worker():
            result = fetch_usage()
            self.master.after(0, lambda: on_done(result))

        threading.Thread(target=worker, daemon=True).start()

    def schedule_refresh(self, callback):
        if self._refresh_job:
            self.master.after_cancel(self._refresh_job)
        self._refresh_job = self.master.after(self.REFRESH_MS, callback)
