"""Colours, fonts and layout constants, plus the two helpers that read them."""

import sys
from functools import lru_cache

from PIL import ImageFont

SIZE_PRESETS = [("작게", 96), ("보통", 128), ("크게", 180), ("아주 크게", 240)]
OPACITY_PRESETS = [("100%", 1.0), ("75%", 0.75), ("50%", 0.5), ("25%", 0.25)]

IS_MAC = sys.platform == "darwin"

# macOS Tk gives a window real per-pixel alpha; Windows Tk can only knock out one
# flat colour, so there the image has to be keyed against magenta instead.
TRANSPARENT_KEY = "systemTransparent" if IS_MAC else "magenta"
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
MAX_BUBBLE_BODY_H = 150  # px of text area; the whole bubble lands near 210px

if IS_MAC:
    FONT_REGULAR = ("AppleSDGothicNeo.ttc", "AppleGothic.ttf", "Helvetica.ttc")
    FONT_BOLD = ("AppleSDGothicNeoB.otf", "AppleSDGothicNeo.ttc", "AppleGothic.ttf")
    FONT_MONO = ("Menlo.ttc", "Courier.ttc")
else:
    FONT_REGULAR = ("malgun.ttf", "NanumGothic.ttf", "arial.ttf")
    FONT_BOLD = ("malgunbd.ttf", "NanumGothicBold.ttf", "arialbd.ttf")
    FONT_MONO = ("consola.ttf", "cour.ttf")

LINE_GAP = 5
BLOCK_GAP = 8
RULE_GAP = 6
INDENT_STEP = 12
QUOTE_INDENT = 10
SS = 3  # supersampling factor for smooth rounded corners


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
