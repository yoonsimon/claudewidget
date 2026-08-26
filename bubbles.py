"""The speech bubbles: one card per folder, stacked upward from the character."""

import tkinter as tk

from PIL import Image, ImageDraw

import win_layered
from imaging import Canvas, downscale
from textlayout import draw_markdown, layout_markdown
from theme import (
    BUBBLE_BORDER,
    BUBBLE_FILL,
    BUBBLE_MAX_TEXT_W,
    BUBBLE_MUTED,
    FONT_REGULAR,
    SS,
    load_font,
)


class SpeechBubble:
    """One bubble window. Sessions in different folders each get their own."""

    TAIL_W = 18
    TAIL_H = 11

    def __init__(self, master):
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.canvas = Canvas(self.win)
        self.win.withdraw()
        self.image = None
        self._signature = None
        self.width = 0
        self.height = 0
        self.tail_dx = 0

    def render(self, text, folder, with_tail):
        signature = (text, folder, with_tail)
        if signature == self._signature:
            return
        self._signature = signature

        pad_x, pad_y, radius = 14, 11, 14
        head_gap = 3
        body, text_w, text_h = layout_markdown(text, BUBBLE_MAX_TEXT_W * SS)

        head_font = load_font(FONT_REGULAR, 11 * SS)
        head_text = f"폴더 {folder}" if folder else ""
        head_h = (sum(head_font.getmetrics()) + head_gap * SS) if head_text else 0
        if head_text:
            text_w = max(text_w, int(head_font.getlength(head_text)))

        bw = text_w + pad_x * 2 * SS
        bh = text_h + head_h + pad_y * 2 * SS
        tail_w, tail_h = self.TAIL_W * SS, (self.TAIL_H * SS if with_tail else 0)
        tail_x = max(radius * SS, bw - 46 * SS)

        im = Image.new("RGBA", (bw, bh + tail_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)

        tip_x = tail_x + tail_w // 3
        if with_tail:
            tip = (tip_x, bh + tail_h)
            d.polygon([(tail_x, bh - SS), (tail_x + tail_w, bh - SS), tip], fill=BUBBLE_FILL)
            d.line([(tail_x, bh - SS), tip], fill=BUBBLE_BORDER, width=SS)
            d.line([(tail_x + tail_w, bh - SS), tip], fill=BUBBLE_BORDER, width=SS)

        d.rounded_rectangle(
            [0, 0, bw - 1, bh - 1], radius=radius * SS, fill=BUBBLE_FILL, outline=BUBBLE_BORDER, width=SS
        )

        tx = pad_x * SS
        ty = pad_y * SS
        if head_text:
            d.text((tx, ty), head_text, font=head_font, fill=BUBBLE_MUTED)
            ty += head_h
        draw_markdown(d, body, tx, ty, text_w)

        self.image = downscale(im)
        self.width, self.height = self.image.size
        self.tail_dx = tip_x // SS

    def place(self, x, y):
        self.win.deiconify()
        self.canvas.show(self.image, x, y)

    def hide(self):
        self.win.withdraw()

    def destroy(self):
        self.win.destroy()


class BubbleStack:
    """Keeps one bubble per folder, stacked upward from the character."""

    GAP = 6
    MAX_VISIBLE = 3  # more than this buries the screen and runs off the top edge

    def __init__(self, master):
        self.master = master
        self.bubbles = {}

    def update(self, entries, tip_x, tip_y, on_click=None):
        """entries: list of (folder, text) ordered oldest first, newest last."""
        entries = entries[-self.MAX_VISIBLE :]
        wanted = {folder for folder, _ in entries}
        for folder in list(self.bubbles):
            if folder not in wanted:
                self.bubbles.pop(folder).destroy()
        if not entries:
            return

        # The newest sits closest to the character and is the one wearing the tail.
        bottom_first = list(reversed(entries))
        right = None
        y = tip_y
        area = win_layered.work_area(tip_x, tip_y)
        ceiling = area[1] if area else None
        for index, (folder, text) in enumerate(bottom_first):
            bubble = self.bubbles.get(folder)
            if bubble is None:
                bubble = self.bubbles[folder] = SpeechBubble(self.master)
                if on_click is not None:
                    # A bubble is opaque to clicks, so it has to offer a way out.
                    bubble.win.bind("<Button-1>", lambda _e, f=folder: on_click(f))
            bubble.render(text, folder, with_tail=index == 0)
            if index == 0:
                x = tip_x - bubble.tail_dx
                right = x + bubble.width
            else:
                x = right - bubble.width
            top = y - bubble.height - (0 if index == 0 else self.GAP)
            if ceiling is not None and top < ceiling and index > 0:
                # Out of room upward: the rest would be drawn off the top edge.
                bubble.hide()
                continue
            y = top
            bubble.place(x, y)

    def clear(self):
        for bubble in self.bubbles.values():
            bubble.destroy()
        self.bubbles.clear()
