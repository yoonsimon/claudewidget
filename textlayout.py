"""Markdown to positioned lines, and the painting of those lines onto a card."""

import re

import markdown
from theme import (
    BLOCK_GAP,
    BUBBLE_CODE_BG,
    BUBBLE_CODE_TEXT,
    BUBBLE_LINK,
    BUBBLE_MUTED,
    BUBBLE_RULE,
    BUBBLE_TEXT,
    FONT_BOLD,
    FONT_MONO,
    FONT_REGULAR,
    INDENT_STEP,
    LINE_GAP,
    MAX_BUBBLE_BODY_H,
    QUOTE_INDENT,
    RULE_GAP,
    SS,
    load_font,
)


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


def split_to_fit(tokens, available):
    """Break tokens that are wider than the line, so the bubble cannot grow past it.

    A URL or a long unbroken string has no space to wrap at, and letting it overflow
    stretched the bubble to whatever the token measured.
    """
    out = []
    for token in tokens:
        font = token["font"]
        if available <= 0 or font.getlength(token["text"]) <= available:
            out.append(token)
            continue
        chunk = ""
        for char in token["text"]:
            if chunk and font.getlength(chunk + char) > available:
                out.append(dict(token, text=chunk, trailing=0))
                chunk = char
            else:
                chunk += char
        if chunk:
            out.append(dict(token, text=chunk))
    return out


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

        if kind == "code":
            # A fenced line is one unit: splitting it into words would lose the
            # indentation and paint a separate pill behind every word.
            block_font = load_font(FONT_MONO if block["runs"][0][0].isascii() else FONT_REGULAR, 12 * SS)
            lines.append(
                {
                    "y": y,
                    "indent": 0,
                    "marker": "",
                    "marker_font": block_font,
                    "marker_w": 0,
                    "kind": "code",
                    "tokens": [
                        {
                            "text": block["runs"][0][0],
                            "trailing": 0,
                            "style": "code",
                            "font": block_font,
                            "colour": BUBBLE_CODE_TEXT,
                            "x": 0,
                            "w": int(block_font.getlength(block["runs"][0][0])),
                        }
                    ],
                }
            )
            line_h = sum(block_font.getmetrics())
            y += line_h + LINE_GAP * SS
            width = max(width, min(int(block_font.getlength(block["runs"][0][0])), max_width))
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

        for token in split_to_fit(tokens, available):
            token_w = int(token["font"].getlength(token["text"]))
            space_w = int(token["font"].getlength(" ")) * min(token["trailing"], 1)
            if current and x + token_w > available:
                flush()
            token = dict(token, x=x, w=token_w)
            current.append(token)
            x += token_w + space_w
        flush()

        if kind in ("heading", "code"):
            y += BLOCK_GAP * SS // 2

    lines, y = cap_lines(lines, y)
    height = max(0, y - LINE_GAP * SS)
    return lines, width, height


def line_height(line, fallback):
    return max((sum(t["font"].getmetrics()) for t in line["tokens"]), default=fallback)


def cap_lines(lines, y):
    """Cut the body at a fixed pixel height.

    Counting characters cannot bound the height (Korean fits ~21 per drawn line, so
    a 60 character limit still makes three), and counting lines leaves it drifting:
    blank lines and headings add space that lines do not account for. Only measuring
    the drawn height pins it down.
    """
    limit = MAX_BUBBLE_BODY_H * SS
    if y <= limit:
        return lines, y

    font = load_font(FONT_REGULAR, 14 * SS)
    ellipsis_h = sum(font.getmetrics())
    budget = limit - ellipsis_h - LINE_GAP * SS

    kept = []
    for line in lines:
        height = ellipsis_h if line["kind"] == "rule" else line_height(line, ellipsis_h)
        if kept and line["y"] + height > budget:
            break
        kept.append(line)

    cutoff = kept[-1]
    ellipsis_y = cutoff["y"] + line_height(cutoff, ellipsis_h) + LINE_GAP * SS
    kept.append(
        {
            "y": ellipsis_y,
            "indent": 0,
            "marker": "",
            "marker_font": font,
            "marker_w": 0,
            "kind": "text",
            "tokens": [
                {
                    "text": "…",
                    "trailing": 0,
                    "style": "",
                    "font": font,
                    "colour": BUBBLE_MUTED,
                    "x": 0,
                    "w": int(font.getlength("…")),
                }
            ],
        }
    )
    return kept, ellipsis_y + sum(font.getmetrics()) + LINE_GAP * SS


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

        if line["kind"] == "code":
            # One background for the whole line, spanning the card, not per token.
            d.rectangle([ox - 4 * SS, y - 2 * SS, ox + width + 4 * SS, y + height + SS], fill=BUBBLE_CODE_BG)

        for token in line["tokens"]:
            tx = x0 + token["x"]
            if token["style"] == "code" and line["kind"] != "code":
                d.rounded_rectangle(
                    [tx - 3 * SS, y - SS, tx + token["w"] + 3 * SS, y + height],
                    radius=3 * SS,
                    fill=BUBBLE_CODE_BG,
                )
            d.text((tx, y), token["text"], font=token["font"], fill=token["colour"])
