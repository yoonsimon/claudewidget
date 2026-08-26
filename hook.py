"""Failure-proof entry point for the Claude Code hooks.

Point settings.json at this file, never at hook_bridge.py directly. A hook that
exits non-zero blocks the tool call that triggered it, in *every* session on the
machine, so a missing or half-edited hook_bridge.py must never be fatal. This
wrapper runs it and swallows anything it throws.
"""

import os
import sys

# Plain import rather than runpy: this runs on every tool call, so the ~10ms runpy
# costs are worth avoiding. sys.path is set so hook_bridge finds its siblings.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import hook_bridge

    hook_bridge.main()
except BaseException:
    pass

sys.exit(0)
