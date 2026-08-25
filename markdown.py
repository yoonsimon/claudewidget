"""Tiny markdown reader for the speech bubble.

Only the parts that actually show up in a short Claude reply are handled:
headings, bullet and numbered lists, quotes, fenced code, and the inline
run styles. Everything else degrades to plain text rather than showing raw
markers. No rendering here, only structure.
"""

import re

INLINE = re.compile(
    r"(`[^`]+`"                 # `code`
    r"|\*\*[^*]+\*\*"           # **bold**
    r"|__[^_]+__"               # __bold__
    r"|\*[^*\n]+\*"             # *emphasis*
    r"|_[^_\n]+_"               # _emphasis_
    r"|~~[^~]+~~"               # ~~strike~~
    r"|\[[^\]]*\]\([^)]*\))"    # [label](url)
)
LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")

HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
NUMBER = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
FENCE = re.compile(r"^\s*```")
RULE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")


def parse_inline(text):
    """Split a line into (text, style) runs. style: "" | bold | code | link."""
    runs = []
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            runs.append((part[1:-1], "code"))
        elif (part.startswith("**") and part.endswith("**")) or (
            part.startswith("__") and part.endswith("__")
        ):
            runs.append((part[2:-2], "bold"))
        elif part.startswith("~~") and part.endswith("~~"):
            runs.append((part[2:-2], ""))
        elif LINK.fullmatch(part):
            runs.append((LINK.fullmatch(part).group(1), "link"))
        elif len(part) > 1 and part[0] in "*_" and part[-1] == part[0]:
            runs.append((part[1:-1], "bold"))
        else:
            runs.append((part, ""))
    return runs or [("", "")]


def parse(text):
    """Return a list of blocks: {kind, runs, marker, indent}.

    kind is one of: text, heading, bullet, number, quote, code, rule.
    """
    blocks = []
    in_code = False
    for raw in (text or "").split("\n"):
        line = raw.rstrip()

        if FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            blocks.append({"kind": "code", "runs": [(raw, "code")], "marker": "", "indent": 0})
            continue

        if not line.strip():
            if blocks and blocks[-1]["kind"] != "blank":
                blocks.append({"kind": "blank", "runs": [], "marker": "", "indent": 0})
            continue

        if RULE.match(line):
            blocks.append({"kind": "rule", "runs": [], "marker": "", "indent": 0})
            continue

        match = HEADING.match(line)
        if match:
            blocks.append(
                {
                    "kind": "heading",
                    "runs": parse_inline(match.group(2)),
                    "marker": "",
                    "indent": 0,
                    "level": len(match.group(1)),
                }
            )
            continue

        match = QUOTE.match(line)
        if match:
            blocks.append({"kind": "quote", "runs": parse_inline(match.group(1)), "marker": "", "indent": 0})
            continue

        match = NUMBER.match(line)
        if match:
            blocks.append(
                {
                    "kind": "number",
                    "runs": parse_inline(match.group(3)),
                    "marker": f"{match.group(2)}.",
                    "indent": len(match.group(1)) // 2,
                }
            )
            continue

        match = BULLET.match(line)
        if match:
            blocks.append(
                {
                    "kind": "bullet",
                    "runs": parse_inline(match.group(2)),
                    "marker": "•",
                    "indent": len(match.group(1)) // 2,
                }
            )
            continue

        blocks.append({"kind": "text", "runs": parse_inline(line), "marker": "", "indent": 0})

    while blocks and blocks[-1]["kind"] == "blank":
        blocks.pop()
    return blocks
