if __name__ == "__main__":
    # Before anything heavy: a second instance would otherwise spend ~600ms importing
    # PIL and tkinter only to lose the lock and exit. Kept above the imports on
    # purpose, and guarded so importing this module for tests stays side-effect free.
    import single_instance

    if not single_instance.acquire():
        raise SystemExit(0)

import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from PIL import Image, ImageSequence

import win_layered
from bubbles import BubbleStack
from imaging import Canvas, placeholder_image, prepare_frame
from panel import UsagePanel
from state import (
    CONFIG_PATH,
    DEFAULT_CONFIG,
    DEFAULT_IMAGE,
    PAUSE_PATH,
    POLL_MS,
    STATE_DIR,
    bubble_text,
    load_json,
    read_states,
    save_json,
    seconds_until_tomorrow,
)
from theme import SIZE_PRESETS

# The split moved code out of this module, not names off it. Everything the widget
# used to define stays reachable as `widget.<name>`, so the snapshot test and anything
# else pointed at this module keeps working without knowing about the new files.
from bubbles import SpeechBubble  # noqa: F401
from imaging import bleed_rgb, downscale, key_out, make_transparent  # noqa: F401
from state import (  # noqa: F401
    BASE,
    DONE_LINGER,
    RUNNING_LINGER,
    STALE_AFTER,
    WAITING_LINGER,
    WORKING_LINGER,
)
from textlayout import (  # noqa: F401
    cap_lines,
    draw_markdown,
    layout_markdown,
    line_height,
    run_colour,
    run_font,
    split_to_fit,
    split_tokens,
)
from theme import (  # noqa: F401
    BLOCK_GAP,
    BUBBLE_BORDER,
    BUBBLE_CODE_BG,
    BUBBLE_CODE_TEXT,
    BUBBLE_FILL,
    BUBBLE_LINK,
    BUBBLE_MAX_TEXT_W,
    BUBBLE_MUTED,
    BUBBLE_RULE,
    BUBBLE_TEXT,
    FONT_BOLD,
    FONT_MONO,
    FONT_REGULAR,
    INDENT_STEP,
    IS_MAC,
    LINE_GAP,
    MAGENTA_RGB,
    MAX_BUBBLE_BODY_H,
    QUOTE_INDENT,
    RULE_GAP,
    SS,
    TRANSPARENT_KEY,
    hex_to_rgb,
    load_font,
)


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
        self._dismissed = {}

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.config.get("always_on_top", True)))
        self.canvas = Canvas(self.root)
        self.img_label = self.canvas.label

        self.bubbles = BubbleStack(self.root)
        self.panel = UsagePanel(self.root)
        # The panel is opaque to clicks too, so clicking it closes it.
        self.panel.win.bind("<Button-1>", lambda _e: self.toggle_panel())

        self.load_image(self.config.get("image_path", ""))
        self.bind_events()
        self.position_window()
        # A still image is painted once, so make sure that one paint lands after the
        # window is on screen rather than before it exists.
        self.root.after(60, self.paint_character)
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
            self.frames = [(f, 1000)]
            self.img_size = f.size
        self.frame_index = 0

    def load_frames(self, path, max_size):
        self.frames = []
        try:
            im = Image.open(path)
            iterator = ImageSequence.Iterator(im) if getattr(im, "is_animated", False) else [im]
            for frame in iterator:
                f = prepare_frame(frame, max_size)
                duration = frame.info.get("duration", 120) or 120
                self.frames.append((f, duration))
                self.img_size = f.size
        except Exception:
            self.frames = []
        return bool(self.frames)

    def animate(self):
        delay = 250
        if len(self.frames) > 1:
            self.frame_index = (self.frame_index + 1) % len(self.frames)
            delay = max(int(self.frames[self.frame_index][1]), 30)
            self.paint_character()
        self.root.after(delay, self.animate)

    def paint_character(self):
        self.canvas.show(self.frames[self.frame_index][0], self.root.winfo_x(), self.root.winfo_y())

    def default_position(self):
        w, h = self.img_size
        return self.root.winfo_screenwidth() - w - 40, self.root.winfo_screenheight() - h - 80

    def position_window(self):
        w, h = self.img_size
        x = self.config.get("x")
        y = self.config.get("y")
        # A saved position can point at a monitor that is no longer attached. Every
        # control starts with clicking the character, so an off-screen widget would be
        # unrecoverable.
        if x is None or y is None or not win_layered.on_any_monitor(int(x), int(y), w, h):
            x, y = self.default_position()
        self.canvas.show(self.frames[self.frame_index][0], x, y)

    def reset_position(self):
        x, y = self.default_position()
        self.config["x"], self.config["y"] = x, y
        save_json(CONFIG_PATH, self.config)
        self.canvas.show(self.frames[self.frame_index][0], x, y)
        self.place_bubbles()
        if self.panel.open:
            self.place_panel()

    def bind_events(self):
        # Bound on the window, not the label: a layered window paints its own surface,
        # so the label stays empty and would only cover a pixel or two.
        self.root.bind("<ButtonPress-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.do_move)
        self.root.bind("<ButtonRelease-1>", self.end_move)
        # Right-click is Button-3 on Windows but Button-2 on macOS Tk, and a
        # trackpad-only Mac needs the control-click alias too.
        for sequence in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            self.root.bind(sequence, self.show_menu)

    def start_move(self, event):
        self._drag_origin = (event.x, event.y)
        self._dragged = False

    def do_move(self, event):
        dx = event.x - self._drag_origin[0]
        dy = event.y - self._drag_origin[1]
        if abs(dx) > 3 or abs(dy) > 3:
            self._dragged = True
        self.canvas.show(
            self.frames[self.frame_index][0],
            self.root.winfo_x() + dx,
            self.root.winfo_y() + dy,
        )
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
        menu.add_command(label="위치 초기화", command=self.reset_position)
        menu.add_separator()

        off_menu = tk.Menu(menu, tearoff=0)
        off_menu.add_command(label="1시간 끄기", command=lambda: self.pause(3600))
        off_menu.add_command(label="오늘 하루 끄기", command=lambda: self.pause(seconds_until_tomorrow()))
        off_menu.add_command(label="다시 켤 때까지 끄기", command=lambda: self.pause(None))
        menu.add_cascade(label="끄기", menu=off_menu)
        menu.add_command(label="이번만 닫기", command=self.quit)
        menu.tk_popup(event.x_root, event.y_root)

    def pause(self, seconds):
        """Stop the hooks from bringing the widget back, then close it.

        Plain quit does not stick: the next tool call would start it again.
        """
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            value = "forever" if seconds is None else str(time.time() + seconds)
            PAUSE_PATH.write_text(value, encoding="utf-8")
        except Exception:
            pass
        self.quit()

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
        self.root.after(POLL_MS, self.poll_state)

    def refresh_bubbles(self):
        entries = []
        for state in read_states():
            text = bubble_text(state)
            folder = state.get("folder") or ""
            ts = state.get("ts", 0)
            # A dismissed bubble stays gone until that project says something new.
            if text and ts > self._dismissed.get(folder, 0):
                entries.append((folder, text, ts))

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
        self.bubbles.update(
            getattr(self, "_bubble_payload", []), tip_x, tip_y, on_click=self.dismiss_bubble
        )

    def dismiss_bubble(self, folder):
        self._dismissed[folder] = time.time()
        self.refresh_bubbles()

    def quit(self):
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    # Starting it by hand is the way back from "다시 켤 때까지 끄기".
    PAUSE_PATH.unlink(missing_ok=True)
    # The lock was already taken at the top of this file, before the heavy imports.
    Widget().run()
