"""Claude Code usage lookup.

Reads the OAuth access token from ~/.claude/.credentials.json (macOS keeps it
in the login keychain instead) and asks Anthropic's usage endpoint for the
current limits. The token never leaves this process and is never logged.
"""

import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
KEYCHAIN_SERVICE = "Claude Code-credentials"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TIMEOUT = 10

KIND_LABELS = {
    "session": "세션 (5시간)",
    "weekly_all": "주간 전체",
    "weekly_scoped": "주간",
}

SEVERITY_COLORS = {
    "normal": "#22c55e",
    "warning": "#f59e0b",
    "critical": "#ef4444",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects instead of forwarding the Authorization header.

    HTTPRedirectHandler.redirect_request copies every request header except
    content-length/content-type onto the redirect target, so a 302 from an
    intercepting proxy would hand it our bearer token. Returning None falls
    through to HTTPDefaultErrorHandler, which raises HTTPError(30x).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _read_credentials_text():
    """Return the raw credentials JSON, or None if it cannot be obtained."""
    try:
        return CREDENTIALS_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    if sys.platform != "darwin":
        return None
    # macOS Claude Code keeps the same JSON in the login keychain.
    try:
        done = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except Exception:
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def read_access_token():
    text = _read_credentials_text()
    if text is None:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    return oauth.get("accessToken") or None


def parse_reset_seconds(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            reset = datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            reset = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return max(0, int((reset - datetime.now(timezone.utc)).total_seconds()))


def format_reset(seconds):
    if seconds is None:
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}일 {hours}시간 후"
    if hours:
        return f"{hours}시간 {minutes}분 후"
    return f"{minutes}분 후"


def label_for(limit):
    kind = limit.get("kind", "")
    label = KIND_LABELS.get(kind, kind or "사용량")
    scope = limit.get("scope") or {}
    model = (scope.get("model") or {}).get("display_name")
    if model:
        return f"{label} {model}"
    return label


def request_usage():
    token = read_access_token()
    if not token:
        return None, "no-credentials"
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
        },
    )
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        return None, f"http-{exc.code}"
    except Exception:
        return None, "unreachable"


def rows_from_legacy(payload):
    rows = []
    for key, label in (("five_hour", "세션 (5시간)"), ("seven_day", "주간 전체")):
        block = payload.get(key)
        if isinstance(block, dict) and block.get("utilization") is not None:
            rows.append(
                {
                    "label": label,
                    "percent": float(block.get("utilization") or 0),
                    "color": SEVERITY_COLORS["normal"],
                    "reset": format_reset(parse_reset_seconds(block.get("resets_at"))),
                }
            )
    return rows


def fetch_usage():
    """Return {'rows': [...]} for display, or {'error': reason}."""
    payload, error = request_usage()
    if error:
        return {"error": error}

    rows = []
    for limit in payload.get("limits") or []:
        percent = limit.get("percent")
        if percent is None:
            continue
        rows.append(
            {
                "label": label_for(limit),
                "percent": float(percent),
                "color": SEVERITY_COLORS.get(limit.get("severity", "normal"), SEVERITY_COLORS["normal"]),
                "reset": format_reset(parse_reset_seconds(limit.get("resets_at"))),
            }
        )

    if not rows:
        rows = rows_from_legacy(payload)
    if not rows:
        return {"error": "no-data"}
    return {"rows": rows}


if __name__ == "__main__":
    result = fetch_usage()
    if "error" in result:
        print("error:", result["error"])
    else:
        for row in result["rows"]:
            print(f"{row['label']:>18} : {row['percent']:5.1f}%  {row['reset']}")
