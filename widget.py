import json
import re
import socket
import sys
import threading
import time
import tkinter as tk
from functools import lru_cache
from pathlib import Path
from tkinter import filedialog

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageSequence, ImageTk

import markdown
from usage import fetch_usage

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
STATE_DIR = BASE / "state"
DEFAULT_IMAGE = BASE / "assets" / "default.png"
LOCK_PORT = 47311  # arbitrary local port used only to prevent double-launch

DEFAULT_CONFIG = {
    "image_path": "",
    "x": None,
    "y": None,
    "max_size": 128,
    "always_on_top": True,
}

DONE_LINGER = 8  # seconds a finished reply keeps its bubble
STALE_AFTER = 30 * 60  # a session quiet this long stops being shown

SIZE_PRESETS = [("작게", 96), ("보통", 128), ("크게", 180), ("아주 크게", 240)]

TRANSPARENT_KEY = "magenta"
MAGENTA_RGB = (255, 0, 255)

BUBBLE_FILL = (255, 255, 255)
BUBBLE_BORDER = (222, 222, 228)
BUBBLE_TEXT = (28, 28, 32)
BUBBLE_MUTED = (96, 96, 106)
BUBBLE_LINK = (37, 99, 235)
BUBBLE_CODE_TEXT = (185, 28, 60)
BUBBLE_CODE_BG = (244, 244, 247)
BUBBLE_RULE = (226, 226, 232)
BUBBLE_MAX_TEXT_W = 300

FONT_REGULAR = ("malgun.ttf", "NanumGothic.ttf", "arial.ttf")
FONT_BOLD = ("malgunbd.ttf", "NanumGothicBold.ttf", "arialbd.ttf")
FONT_MONO = ("consola.ttf", "cour.ttf")

LINE_GAP = 5
BLOCK_GAP = 8
RULE_GAP = 6
INDENT_STEP = 12
QUOTE_INDENT = 10
SS = 3  # supersampling factor for smooth rounded corners


def read_states():
    """Every session's current state, newest write wins per file."""
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
            continue
        states.append(state)
    return states


def bubble_text(state):
    """The line a state should show, or "" when it should show nothing."""
    kind = state.get("state", "idle")
    text = (state.get("text") or "").strip()
    if kind == "running":
        tool = state.get("tool") or ""
        return f"실행중: {tool}" if tool else "작업중..."
    if kind == "waiting":
        return text or "입력을 기다리고 있어요"
    if kind == "done" and text:
        if time.time() - float(state.get("ts") or 0) > DONE_LINGER:
            return ""
        return text
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


def hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


@lru_cache(maxsize=None)
def load_font(candidates, size):
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def placeholder_image(size):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((4, 4, size - 4, size - 4), fill=(90, 90, 220, 255))
    return im


def bleed_rgb(im):
    """Spread edge colour outward into the transparent area.

    Straight-alpha PNGs usually store black in fully transparent pixels, so a plain
    resize drags that black into the antialiased rim. Growing the visible colour into
    the transparent region first keeps the rim the character's own colour.
    """
    rgb = im.convert("RGB")
    known = im.getchannel("A").point(lambda v: 255 if v > 0 else 0)
    for _ in range(3):
        rgb = Image.composite(rgb, rgb.filter(ImageFilter.GaussianBlur(3)), known)
        known = known.filter(ImageFilter.MaxFilter(5))
    return rgb


def prepare_frame(frame, max_size):
    """Fit a frame into the box and key it against the transparency colour.

    tkinter can only key one flat colour, not per-pixel alpha, so the alpha is
    hard-thresholded: a half-transparent pixel left in place would show up as a
    magenta fringe around the character.
    """
    im = frame.convert("RGBA")
    ratio = min(max_size / im.width, max_size / im.height, 1.0)
    size = (max(1, round(im.width * ratio)), max(1, round(im.height * ratio)))

    rgb = bleed_rgb(im).resize(size, Image.LANCZOS)
    alpha = im.getchannel("A").resize(size, Image.LANCZOS).point(lambda v: 255 if v >= 128 else 0)

    flat = Image.new("RGB", size, MAGENTA_RGB)
    flat.paste(rgb, mask=alpha)
    return flat


def flatten_for_transparency(im):
    """Downscale a supersampled RGBA card and key it against the transparent colour.

    The transparent area is pre-filled with the card colour so antialiased edges never
    blend toward black, and the alpha is hard-thresholded so no half-transparent pixel
    can pick up the magenta key as a coloured fringe.
    """
    alpha = im.getchannel("A")
    rgb = Image.new("RGB", im.size, BUBBLE_FILL)
    rgb.paste(im.convert("RGB"), mask=alpha)
    size = (im.width // SS, im.height // SS)
    rgb = rgb.resize(size, Image.LANCZOS)
    alpha = alpha.resize(size, Image.LANCZOS).point(lambda v: 255 if v >= 128 else 0)

    flat = Image.new("RGB", size, MAGENTA_RGB)
    flat.paste(rgb, mask=alpha)
    return ImageTk.PhotoImage(flat), size


def run_font(style, text, heading=False):
    """Pick the face for a run. Mono only for ASCII, since Consolas has no Hangul."""
    if heading:
        return load_font(FONT_BOLD, 15 * SS)
    if style == "code" and text.isascii():
        return load_font(FONT_MONO, 12 * SS)
    if style == "bold":
        return load_font(FONT_BOLD, 14 * SS)
    return load_font(FONT_REGULAR, 14 * SS)


def run_colour(style):
    if style == "link":
        return BUBBLE_LINK
    if style == "code":
        return BUBBLE_CODE_TEXT
    return BUBBLE_TEXT


def split_tokens(runs, heading=False):
    """Break styled runs into wrappable tokens, keeping the space that follows each."""
    tokens = []
    for text, style in runs:
        font = run_font(style, text, heading)
        colour = run_colour(style)
        for piece in re.findall(r"\S+\s*|\s+", text):
            word = piece.rstrip()
            if not word:
                # Whitespace between runs: keep it as the previous token's trailing space
                # instead of dropping it, or "**bold** text" loses its gap.
                if tokens:
                    tokens[-1]["trailing"] = 1
                continue
            tokens.append(
                {
                    "text": word,
                    "trailing": len(piece) - len(word),
                    "style": style,
                    "font": font,
                    "colour": colour,
                }
            )
    return tokens


def layout_markdown(text, max_width):
    """Turn markdown into positioned lines.

    Returns (lines, width, height); each line is
    {"y", "indent", "kind", "tokens": [{"x", "w", ...}]}.
    """
    lines = []
    y = 0
    width = 0

    for block in markdown.parse(text):
        kind = block["kind"]
        if kind == "blank":
            y += BLOCK_GAP * SS
            continue
        if kind == "rule":
            y += RULE_GAP * SS
            lines.append({"y": y, "indent": 0, "kind": "rule", "tokens": []})
            y += RULE_GAP * SS
            continue

        heading = kind == "heading"
        indent = block["indent"] * INDENT_STEP * SS
        marker = block.get("marker") or ""
        marker_font = load_font(FONT_REGULAR, 14 * SS)
        marker_w = int(marker_font.getlength(marker + " ")) if marker else 0
        if kind == "quote":
            indent += QUOTE_INDENT * SS

        tokens = split_tokens(block["runs"], heading)
        if not tokens:
            tokens = [
                {"text": "", "trailing": 0, "style": "", "font": marker_font, "colour": BUBBLE_TEXT}
            ]

        if heading and lines:
            y += BLOCK_GAP * SS

        available = max_width - indent - marker_w
        x = 0
        current = []
        first_line = True

        def flush():
            nonlocal x, current, first_line, y, width
            if not current and not first_line:
                return
            lines.append(
                {
                    "y": y,
                    "indent": indent,
                    "marker": marker if first_line else "",
                    "marker_font": marker_font,
                    "marker_w": marker_w,
                    "kind": kind,
                    "tokens": current,
                }
            )
            line_h = max((t["font"].getmetrics() for t in current), key=lambda m: sum(m), default=(0, 0))
            y += sum(line_h) + LINE_GAP * SS
            width = max(width, indent + marker_w + x)
            x = 0
            current = []
            first_line = False

        for token in tokens:
            token_w = int(token["font"].getlength(token["text"]))
            space_w = int(token["font"].getlength(" ")) * min(token["trailing"], 1)
            if current and x + token_w > available:
                flush()
            if token_w > available and not current:
                # a single unbreakable word: let it overflow rather than vanish
                pass
            token = dict(token, x=x, w=token_w)
            current.append(token)
            x += token_w + space_w
        flush()

        if kind in ("heading", "code"):
            y += BLOCK_GAP * SS // 2

    height = max(0, y - LINE_GAP * SS)
    return lines, width, height


def draw_markdown(d, lines, ox, oy, width):
    """Paint the laid-out lines, including code backgrounds and quote bars."""
    for line in lines:
        y = oy + line["y"]
        x0 = ox + line["indent"]

        if line["kind"] == "rule":
            d.line([(ox, y), (ox + width, y)], fill=BUBBLE_RULE, width=SS)
            continue

        if line.get("marker"):
            d.text((x0, y), line["marker"], font=line["marker_font"], fill=BUBBLE_MUTED)

        x0 += line.get("marker_w", 0)
        height = max((sum(t["font"].getmetrics()) for t in line["tokens"]), default=0)

        if line["kind"] == "quote":
            bar = ox + line["indent"] - QUOTE_INDENT * SS
            d.rounded_rectangle(
                [bar, y, bar + 2 * SS, y + height], radius=SS, fill=BUBBLE_RULE
            )

        for token in line["tokens"]:
            tx = x0 + token["x"]
            if token["style"] == "code" or line["kind"] == "code":
                d.rounded_rectangle(
                    [tx - 3 * SS, y - SS, tx + token["w"] + 3 * SS, y + height],
                    radius=3 * SS,
                    fill=BUBBLE_CODE_BG,
                )
            d.text((tx, y), token["text"], font=token["font"], fill=token["colour"])


class SpeechBubble:
    """One bubble window. Sessions in different folders each get their own."""

    TAIL_W = 18
    TAIL_H = 11

    def __init__(self, master):
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=TRANSPARENT_KEY)
        self.win.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
        self.label = tk.Label(self.win, bg=TRANSPARENT_KEY, bd=0)
        self.label.pack()
        self.win.withdraw()
        self._photo = None
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

        self._photo, size = flatten_for_transparency(im)
        self.label.configure(image=self._photo)
        self.width, self.height = size
        self.tail_dx = tip_x // SS

    def place(self, x, y):
        self.win.geometry(f"{self.width}x{self.height}+{int(x)}+{int(y)}")
        self.win.deiconify()

    def hide(self):
        self.win.withdraw()

    def destroy(self):
        self.win.destroy()


class BubbleStack:
    """Keeps one bubble per folder, stacked upward from the character."""

    GAP = 6

    def __init__(self, master):
        self.master = master
        self.bubbles = {}

    def update(self, entries, tip_x, tip_y):
        """entries: list of (folder, text) ordered oldest first, newest last."""
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
        for index, (folder, text) in enumerate(bottom_first):
            bubble = self.bubbles.get(folder)
            if bubble is None:
                bubble = self.bubbles[folder] = SpeechBubble(self.master)
            bubble.render(text, folder, with_tail=index == 0)
            if index == 0:
                x = tip_x - bubble.tail_dx
                right = x + bubble.width
            else:
                x = right - bubble.width
            y -= bubble.height + (0 if index == 0 else self.GAP)
            bubble.place(x, y)

    def clear(self):
        for bubble in self.bubbles.values():
            bubble.destroy()
        self.bubbles.clear()


class UsagePanel:
    WIDTH = 232
    REFRESH_MS = 5 * 60 * 1000

    def __init__(self, master):
        self.master = master
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=TRANSPARENT_KEY)
        self.win.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
        self.label = tk.Label(self.win, bg=TRANSPARENT_KEY, bd=0)
        self.label.pack()
        self.win.withdraw()
        self._photo = None
        self.data = {"loading": True}
        self.open = False
        self._refresh_job = None

    def render(self):
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

        self._photo, size = flatten_for_transparency(im)
        self.label.configure(image=self._photo)
        return size

    def place_under(self, char_x, char_y, char_w, char_h):
        w, h = self.render()
        x = char_x + char_w // 2 - w // 2
        y = char_y + char_h + 6
        # Always sits below the character; only pulled up far enough to stay on screen.
        bottom = self.master.winfo_vrooty() + self.master.winfo_vrootheight()
        if y + h > bottom:
            y = bottom - h
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

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


class Widget:
    def __init__(self):
        self.config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
        self.frames = []
        self.frame_index = 0
        self.img_size = (96, 96)
        self._drag_origin = (0, 0)
        self._dragged = False
        self._bubble_payload = []
        self._layout_dirty = False

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.config.get("always_on_top", True)))
        self.root.configure(bg=TRANSPARENT_KEY)
        self.root.wm_attributes("-transparentcolor", TRANSPARENT_KEY)

        self.img_label = tk.Label(self.root, bg=TRANSPARENT_KEY, bd=0)
        self.img_label.pack()

        self.bubbles = BubbleStack(self.root)
        self.panel = UsagePanel(self.root)

        self.load_image(self.config.get("image_path", ""))
        self.bind_events()
        self.position_window()
        self.poll_state()
        self.animate()

    def load_image(self, path):
        max_size = int(self.config.get("max_size", 128) or 128)
        # Configured image first, bundled default next, drawn placeholder as last resort.
        for candidate in (path, DEFAULT_IMAGE):
            if candidate and Path(candidate).exists() and self.load_frames(candidate, max_size):
                break
        else:
            f = prepare_frame(placeholder_image(max_size), max_size)
            self.frames = [(ImageTk.PhotoImage(f), 1000)]
            self.img_size = f.size
        self.frame_index = 0
        self.img_label.configure(image=self.frames[0][0])

    def load_frames(self, path, max_size):
        self.frames = []
        try:
            im = Image.open(path)
            iterator = ImageSequence.Iterator(im) if getattr(im, "is_animated", False) else [im]
            for frame in iterator:
                f = prepare_frame(frame, max_size)
                duration = frame.info.get("duration", 120) or 120
                self.frames.append((ImageTk.PhotoImage(f), duration))
                self.img_size = f.size
        except Exception:
            self.frames = []
        return bool(self.frames)

    def animate(self):
        if len(self.frames) > 1:
            self.frame_index = (self.frame_index + 1) % len(self.frames)
            img, delay = self.frames[self.frame_index]
            self.img_label.configure(image=img)
            self.root.after(max(int(delay), 30), self.animate)
        else:
            self.root.after(250, self.animate)

    def position_window(self):
        w, h = self.img_size
        x = self.config.get("x")
        y = self.config.get("y")
        if x is None or y is None:
            x = self.root.winfo_screenwidth() - w - 40
            y = self.root.winfo_screenheight() - h - 80
        self.root.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    def bind_events(self):
        self.img_label.bind("<ButtonPress-1>", self.start_move)
        self.img_label.bind("<B1-Motion>", self.do_move)
        self.img_label.bind("<ButtonRelease-1>", self.end_move)
        self.img_label.bind("<Button-3>", self.show_menu)

    def start_move(self, event):
        self._drag_origin = (event.x, event.y)
        self._dragged = False

    def do_move(self, event):
        dx = event.x - self._drag_origin[0]
        dy = event.y - self._drag_origin[1]
        if abs(dx) > 3 or abs(dy) > 3:
            self._dragged = True
        self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")
        self.place_bubbles()
        if self.panel.open:
            self.place_panel()

    def end_move(self, event):
        if not self._dragged:
            self.toggle_panel()
            return
        self.config["x"] = self.root.winfo_x()
        self.config["y"] = self.root.winfo_y()
        save_json(CONFIG_PATH, self.config)

    def place_panel(self):
        self.panel.place_under(
            self.root.winfo_x(), self.root.winfo_y(), self.root.winfo_width(), self.root.winfo_height()
        )

    def toggle_panel(self):
        if self.panel.open:
            self.panel.hide()
            return
        self.panel.show()
        self.refresh_usage()

    def refresh_usage(self):
        self.panel.data = {"loading": True}
        self.place_panel()
        self.panel.refresh(self.on_usage_loaded)

    def on_usage_loaded(self, result):
        self.panel.data = result
        if self.panel.open:
            self.place_panel()
            self.panel.schedule_refresh(self.refresh_usage)

    def show_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="사용량 접기" if self.panel.open else "사용량 펴기", command=self.toggle_panel)
        if self.panel.open:
            menu.add_command(label="사용량 새로고침", command=self.refresh_usage)
        menu.add_separator()
        menu.add_command(label="이미지 변경...", command=self.change_image)

        size_menu = tk.Menu(menu, tearoff=0)
        current = int(self.config.get("max_size", 128))
        for label, value in SIZE_PRESETS:
            mark = " ✓" if value == current else ""
            size_menu.add_command(label=f"{label}{mark}", command=lambda v=value: self.set_max_size(v))
        menu.add_cascade(label="크기", menu=size_menu)

        label = "항상 위 끄기" if self.config.get("always_on_top", True) else "항상 위 켜기"
        menu.add_command(label=label, command=self.toggle_topmost)
        menu.add_separator()
        menu.add_command(label="종료", command=self.quit)
        menu.tk_popup(event.x_root, event.y_root)

    def change_image(self):
        path = filedialog.askopenfilename(
            title="캐릭터 이미지 선택",
            filetypes=[("이미지", "*.png *.jpg *.jpeg *.gif *.webp"), ("모든 파일", "*.*")],
        )
        if path:
            self.config["image_path"] = path
            save_json(CONFIG_PATH, self.config)
            self.load_image(path)
            self.position_window()
            self.place_bubbles()

    def set_max_size(self, value):
        self.config["max_size"] = value
        save_json(CONFIG_PATH, self.config)
        self.load_image(self.config.get("image_path", ""))
        self.position_window()
        self.place_bubbles()

    def toggle_topmost(self):
        self.config["always_on_top"] = not self.config.get("always_on_top", True)
        self.root.attributes("-topmost", self.config["always_on_top"])
        save_json(CONFIG_PATH, self.config)

    def poll_state(self):
        self.refresh_bubbles()
        self.root.after(500, self.poll_state)

    def refresh_bubbles(self):
        entries = []
        for state in read_states():
            text = bubble_text(state)
            if text:
                entries.append((state.get("folder") or "", text, state.get("ts", 0)))

        # Oldest first so the newest ends up nearest the character.
        entries.sort(key=lambda item: item[2])
        payload = [(folder, text) for folder, text, _ in entries]
        if payload == getattr(self, "_bubble_payload", None) and not self._layout_dirty:
            return
        self._bubble_payload = payload
        self._layout_dirty = False
        self.place_bubbles()

    def place_bubbles(self):
        tip_x = self.root.winfo_x() + self.root.winfo_width() - 20
        tip_y = self.root.winfo_y() + 4
        self.bubbles.update(getattr(self, "_bubble_payload", []), tip_x, tip_y)

    def quit(self):
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def acquire_single_instance_lock():
    # Binding a local TCP port fails if another instance already holds it.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        return None
    return sock


if __name__ == "__main__":
    lock = acquire_single_instance_lock()
    if lock is None:
        sys.exit(0)  # already running
    Widget().run()
