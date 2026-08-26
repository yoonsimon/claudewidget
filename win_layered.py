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

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0

# LONG_PTR: 64-bit on Win64, plain LONG on Win32. wintypes has no name for it.
LONG_PTR = ctypes.c_ssize_t


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", wintypes.BYTE),
        ("BlendFlags", wintypes.BYTE),
        ("SourceConstantAlpha", wintypes.BYTE),
        ("AlphaFormat", wintypes.BYTE),
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


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


MONITOR_DEFAULTTONEAREST = 2

if SUPPORTED:
    # Private handles rather than ctypes.windll.*, which caches one shared instance
    # per DLL: pinning argtypes on that would rewrite the prototypes every other
    # module in the process sees. use_last_error keeps GetLastError readable after a
    # call, so a refused paint can be diagnosed instead of guessed at.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    # Every prototype below has to be pinned. ctypes assumes a C `int` return for an
    # undeclared function, which on Win64 truncates the 64-bit DC and bitmap handles
    # to their low 32 bits. Handles are small early in a session so that usually
    # happens to work, and then does not on a machine where they are not: the paint
    # quietly returns 0 and the widget falls back to the jagged colour key. Declared
    # argtypes also turn a wrong argument into an exception here instead of letting
    # GDI read a half-formed pointer.
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = wintypes.INT
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND,
        wintypes.HDC,
        ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE),
        wintypes.HDC,
        ctypes.POINTER(wintypes.POINT),
        wintypes.COLORREF,
        ctypes.POINTER(BLENDFUNCTION),
        wintypes.DWORD,
    ]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    # POINT goes to MonitorFromPoint by value, not through a pointer.
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL

    # Win64 exports the ...Ptr forms and its non-Ptr pair truncates anything
    # pointer-sized; Win32 only has the non-Ptr pair, with the SDK defining the Ptr
    # names as macros over it. GWL_EXSTYLE itself is a 32-bit DWORD either way, so
    # this is about calling the documented function, not about the value read today.
    _get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    _set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    _get_window_long.argtypes = [wintypes.HWND, wintypes.INT]
    _get_window_long.restype = LONG_PTR
    _set_window_long.argtypes = [wintypes.HWND, wintypes.INT, LONG_PTR]
    _set_window_long.restype = LONG_PTR


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
        style = _get_window_long(hwnd, GWL_EXSTYLE)
        if not style & WS_EX_LAYERED:
            _set_window_long(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)

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

        screen_dc = mem_dc = bitmap = previous = None
        try:
            screen_dc = user32.GetDC(None)
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            bits = ctypes.c_void_p()
            bitmap = gdi32.CreateDIBSection(
                screen_dc, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
            )
            if not bitmap:
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
            return bool(ok)
        finally:
            # GDI handles are leaked on any early return without this, and paint runs
            # on every state change.
            if mem_dc and previous:
                gdi32.SelectObject(mem_dc, previous)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            if screen_dc:
                user32.ReleaseDC(None, screen_dc)
    except Exception:
        return False


def work_area(x, y):
    """Usable rectangle of the monitor containing (x, y), taskbar excluded.

    Returns None off Windows or on failure, so callers fall back to Tk's numbers.
    """
    if not SUPPORTED:
        return None
    try:
        monitor = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        r = info.rcWork
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def on_any_monitor(x, y, width, height):
    """True if a decent part of the rectangle is visible on some monitor."""
    area = work_area(x + width // 2, y + height // 2)
    if area is None:
        return True
    left, top, right, bottom = area
    cx, cy = x + width // 2, y + height // 2
    return left <= cx <= right and top <= cy <= bottom
