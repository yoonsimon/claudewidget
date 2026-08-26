"""Pixel snapshot of everything the widget draws.

Run it before and after a refactor and compare the hashes: a pure move of code
between modules must not change a single pixel.

    python tests/render_snapshot.py            # print hashes
    python tests/render_snapshot.py --write    # also write PNGs next to this file
    python tests/render_snapshot.py --check    # compare against baseline.json
"""

import hashlib
import json
import sys
import tkinter as tk
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))

from PIL import Image  # noqa: E402

import widget as W  # noqa: E402

BASELINE = BASE / "baseline.json"
OUT_DIR = BASE / "_snapshots"

BUBBLES = {
    "plain": "네, 반영했습니다.",
    "two_lines": "첫 줄입니다.\n둘째 줄입니다.",
    "long_korean": "가나다라마바사아자차카타파하" * 20,
    "markdown_mix": "**세 가지** 반영했습니다.\n\n- 마크다운\n- 폴더별 말풍선\n- `usage.py` 조회",
    "headings": "# 제목\n\n- 하나\n- 둘\n\n## 소제목\n\n본문입니다.\n\n마지막 문단",
    "code_block": "설명입니다.\n\n```\ndef hello():\n    return 42\n```\n\n끝.",
    "long_token": "A" * 260,
    "url": "https://example.com/" + "x" * 200,
    "quote_rule": "> 인용문입니다.\n\n---\n\n본문입니다. [링크](http://x) 도 있습니다.",
    "blank_heavy": "\n\n".join(f"문단 {i}" for i in range(8)),
    "no_folder": "폴더 없는 경우입니다.",
}

PANELS = {
    "loading": {"loading": True},
    "error": {"error": "no-credentials"},
    "rows": {
        "rows": [
            {"label": "세션 (5시간)", "percent": 43.0, "color": "#22c55e", "reset": "2시간 35분 후"},
            {"label": "주간 전체", "percent": 27.0, "color": "#f59e0b", "reset": "3일 10시간 후"},
            {"label": "주간 Fable", "percent": 19.0, "color": "#ef4444", "reset": "3일 10시간 후"},
        ]
    },
    # The token line under the bars; without this fixture the tokens branch of
    # panel.render never runs and "all snapshots match" says nothing about it.
    "rows_tokens": {
        "rows": [
            {"label": "세션 (5시간)", "percent": 43.0, "color": "#22c55e", "reset": "2시간 35분 후"},
        ],
        "tokens": {"label": "오늘", "value": "1.2M", "parts": ["Opus 980.3K", "Sonnet 219.7K"]},
    },
}


def digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()[:16]


def collect(write=False):
    if write:
        OUT_DIR.mkdir(exist_ok=True)
    root = tk.Tk()
    root.withdraw()
    results = {}
    try:
        bubble = W.SpeechBubble(root)
        for name, text in BUBBLES.items():
            for tail in (True, False):
                bubble._signature = None
                bubble.render(text, "" if name == "no_folder" else "me", with_tail=tail)
                key = f"bubble.{name}.{'tail' if tail else 'plain'}"
                results[key] = {"size": list(bubble.image.size), "sha": digest(bubble.image)}
                if write:
                    bubble.image.save(OUT_DIR / f"{key}.png")

        panel = W.UsagePanel(root)
        for name, data in PANELS.items():
            panel.data = data
            panel._signature = None
            panel.render()
            key = f"panel.{name}"
            results[key] = {"size": list(panel.image.size), "sha": digest(panel.image)}
            if write:
                panel.image.save(OUT_DIR / f"{key}.png")

        for size in (96, 128, 180, 240):
            frame = W.prepare_frame(Image.open(W.DEFAULT_IMAGE), size)
            key = f"character.{size}"
            results[key] = {"size": list(frame.size), "sha": digest(frame)}
            if write:
                frame.save(OUT_DIR / f"{key}.png")
    finally:
        root.destroy()
    return results


def main():
    write = "--write" in sys.argv
    results = collect(write=write)

    if "--check" in sys.argv:
        if not BASELINE.exists():
            print("baseline.json 이 없습니다. 먼저 --save 로 만드세요.")
            return 1
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        bad = []
        for key, value in results.items():
            if key not in baseline:
                bad.append(f"  새 항목: {key}")
            elif baseline[key] != value:
                bad.append(f"  다름: {key}\n    기준 {baseline[key]}\n    현재 {value}")
        for key in baseline:
            if key not in results:
                bad.append(f"  사라짐: {key}")
        if bad:
            print(f"스냅샷 불일치 {len(bad)}건")
            print("\n".join(bad))
            return 1
        print(f"스냅샷 {len(results)}개 전부 일치")
        return 0

    if "--save" in sys.argv:
        BASELINE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"기준 저장: {BASELINE} ({len(results)}개)")
        return 0

    for key, value in sorted(results.items()):
        print(f"  {key:34s} {value['size'][0]:4d}x{value['size'][1]:<4d} {value['sha']}")
    print(f"총 {len(results)}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
