"""Failure-proof entry point for the Claude Code hooks.

Point settings.json at this file, never at hook_bridge.py directly. A hook that
exits non-zero blocks the tool call that triggered it, in *every* session on the
machine, so a missing or half-edited hook_bridge.py must never be fatal. This
wrapper runs it and swallows anything it throws.
"""

import runpy
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "hook_bridge.py"

try:
    runpy.run_path(str(TARGET), run_name="__main__")
except BaseException:
    pass

sys.exit(0)
