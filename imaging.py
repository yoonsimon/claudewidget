"""Getting an image onto the screen: the transparent window surface and the pixel work behind it."""

import tkinter as tk

from PIL import Image, ImageDraw, ImageFilter, ImageTk

import win_layered
from theme import BUBBLE_FILL, IS_MAC, MAGENTA_RGB, SS, TRANSPARENT_KEY


def make_transparent(win):
    """Give a window a see-through background on either platform."""
    win.configure(bg=TRANSPARENT_KEY)
    if IS_MAC:
        win.wm_attributes("-transparent", True)
    else:
        win.wm_attributes("-transparentcolor", TRANSPARENT_KEY)


def key_out(im):
    """Fallback for Windows without layered windows: alpha becomes all or nothing.

    A half-transparent pixel left in place would blend with the key colour and ring
    the artwork in magenta, so the soft edge is thresholded away instead.
    """
    alpha = im.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    flat = Image.new("RGB", im.size, MAGENTA_RGB)
    flat.paste(im.convert("RGB"), mask=alpha)
    return flat


class Canvas:
    """A borderless window that shows one RGBA image.

    On Windows the image goes straight to the compositor as a layered surface, so
    antialiased edges survive. Everywhere else it is a Label holding a PhotoImage.
    """

    use_layered = win_layered.SUPPORTED
    # A layered paint can fail for passing reasons (session lock, RDP reconnect, DWM
    # restart), so one failure must not downgrade the rest of the process.
    failures = 0
    MAX_FAILURES = 3

    def __init__(self, win):
        self.win = win
        self.image = None
        self.x = self.y = 0
        self._photo = None
        self.label = tk.Label(win, bg=TRANSPARENT_KEY, bd=0)
        self.label.pack()
        # Windows treats the colour key and a layered surface as mutually exclusive:
        # once -transparentcolor is set, UpdateLayeredWindow refuses to draw. So the
        # key is only applied when the layered path is not in play.
        if not Canvas.use_layered:
            make_transparent(win)

    @property
    def size(self):
        return self.image.size if self.image else (0, 0)

    def downgrade_all_windows(self):
        """Switch every window to the colour key, not just this one.

        winfo_toplevel() returns the Toplevel itself when called on one, so asking
        it for children would skip the root and leave the character frozen.
        """
        root = self.win.nametowidget(".")
        for window in (root, *[w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]):
            try:
                make_transparent(window)
            except Exception:
                pass

    def show(self, image, x, y):
        self.image = image
        # Tracked here because Tk's idea of the position can go stale after a
        # display change; whoever needs the live position asks the canvas.
        self.x, self.y = int(x), int(y)
        w, h = image.size
        if Canvas.use_layered:
            self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
            # The surface only sticks once the window actually exists, so flush any
            # pending map/resize before handing the bitmap over.
            self.win.update_idletasks()
            if win_layered.paint(self.win, image, x, y):
                Canvas.failures = 0
                return
            Canvas.failures += 1
            if Canvas.failures < Canvas.MAX_FAILURES:
                return  # transient: keep the old surface and try again next time
            Canvas.use_layered = False
            self.downgrade_all_windows()
        self._photo = ImageTk.PhotoImage(key_out(image))
        self.label.configure(image=self._photo)
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")


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
    """Fit a frame into the box, ready for the platform's transparency scheme."""
    im = frame.convert("RGBA")
    ratio = min(max_size / im.width, max_size / im.height, 1.0)
    size = (max(1, round(im.width * ratio)), max(1, round(im.height * ratio)))

    if IS_MAC:
        return im.resize(size, Image.LANCZOS)

    # Bleeding the colour outward first stops the resize from dragging the black
    # that straight-alpha PNGs keep in their transparent pixels into the rim.
    rgb = bleed_rgb(im).resize(size, Image.LANCZOS)
    alpha = im.getchannel("A").resize(size, Image.LANCZOS)
    out = Image.merge("RGBA", (*rgb.split(), alpha))
    return out


def downscale(im):
    """Shrink a supersampled RGBA card to its final size.

    The transparent area is pre-filled with the card colour first so antialiased
    edges never blend toward black on the way down.
    """
    alpha = im.getchannel("A")
    rgb = Image.new("RGB", im.size, BUBBLE_FILL)
    rgb.paste(im.convert("RGB"), mask=alpha)
    size = (im.width // SS, im.height // SS)
    rgb = rgb.resize(size, Image.LANCZOS)
    alpha = alpha.resize(size, Image.LANCZOS)
    return Image.merge("RGBA", (*rgb.split(), alpha))
