"""Per-pixel alpha for tkinter windows on Windows.

Windows Tk can only knock out a single flat colour (`-transparentcolor`), which
forces every antialiased pixel to be either fully opaque or fully gone. Rounded
corners then come out visibly jagged.

A layered window takes a premultiplied BGRA surface instead, so the compositor
blends the soft edge against whatever is actually behind the window. Clicks still
reach tkinter, and fully transparent pixels pass the click through to what is
underneath.

Import guarded: every entry point returns False on non-Windows or on any failure,
so the caller can fall back to the colour-key path.
"""

import ctypes
import sys
from ctypes import wintypes

SUPPORTED = sys.platform == "win32"

if SUPPORTED:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _handle(win):
    """The top-level HWND behind a tkinter window."""
    hwnd = win.winfo_id()
    parent = user32.GetParent(hwnd)
    return parent if parent else hwnd


def paint(win, image, x, y):
    """Draw an RGBA image as the window's whole surface at (x, y).

    Returns False if anything goes wrong, so the caller can fall back.
    """
    if not SUPPORTED:
        return False
    try:
        hwnd = _handle(win)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if not style & WS_EX_LAYERED:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)

        width, height = image.size
        # "BGRa" is PIL's premultiplied byte order, which is what the API expects.
        pixels = image.tobytes("raw", "BGRa")

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # negative: rows run top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB

        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap:
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
            return False

        ctypes.memmove(bits, pixels, len(pixels))
        previous = gdi32.SelectObject(mem_dc, bitmap)

        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        ok = user32.UpdateLayeredWindow(
            hwnd,
            screen_dc,
            ctypes.byref(wintypes.POINT(int(x), int(y))),
            ctypes.byref(wintypes.SIZE(width, height)),
            mem_dc,
            ctypes.byref(wintypes.POINT(0, 0)),
            0,
            ctypes.byref(blend),
            ULW_ALPHA,
        )

        gdi32.SelectObject(mem_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)
        return bool(ok)
    except Exception:
        return False
