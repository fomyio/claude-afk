#!/usr/bin/env python3
"""
watch_approver.py
Claude Code PermissionRequest hook — sends the request to your Apple Watch
via ntfy.sh and waits for an interactive tap to approve, always-approve, or reject.

Usage (configured automatically by install.sh):
    Registered as a Claude Code PermissionRequest hook. Claude Code pipes JSON
    to stdin; this script outputs a JSON decision to stdout and exits 0.
"""

import http.server
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import urllib.parse
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HOOK_DIR = Path(__file__).parent
CONFIG_PATH = HOOK_DIR / "config.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        _fatal(f"Config not found at {CONFIG_PATH}. Run install.sh first.")
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _fatal(msg: str) -> None:
    """Exit in a way that Claude Code treats as a non-blocking error."""
    print(json.dumps({"error": msg}), file=sys.stderr)
    # Output a deny decision so Claude Code doesn't hang
    _output_decision("deny", reason=f"Watch approver config error: {msg}")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Decision output
# ---------------------------------------------------------------------------

ALWAYS_ALLOW_PERMISSIONS = [{"type": "toolAlwaysAllow"}]


def _output_decision(behavior: str, reason: str = "", always: bool = False) -> None:
    decision: dict = {"behavior": behavior}
    if reason:
        decision["message"] = reason
    if always and behavior == "allow":
        decision["updatedPermissions"] = ALWAYS_ALLOW_PERMISSIONS

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision,
        }
    }
    print(json.dumps(output))


# ---------------------------------------------------------------------------
# Local callback server
# ---------------------------------------------------------------------------

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Tiny HTTP server that listens for one tap from the ntfy action button."""

    result: str | None = None  # "approve" | "always" | "reject"
    _lock = threading.Event()

    def do_GET(self):
        path = self.path.strip("/").lower()
        if path in ("approve", "always", "reject"):
            _CallbackHandler.result = path
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            _CallbackHandler._lock.set()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # Suppress request logs


def _get_callback_port(config: dict) -> int:
    """Use a fixed port from config so macOS firewall rules stay stable.
    Falls back to a random free port if not configured."""
    fixed = config.get("callback_port")
    if fixed:
        return int(fixed)
    # Dynamic fallback (useful for testing, but firewall may block LAN access)
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def _start_callback_server(port: int) -> http.server.HTTPServer:
    # Reset state for this request
    _CallbackHandler.result = None
    _CallbackHandler._lock.clear()
    # Bind to 0.0.0.0 so iPhone/Watch on the same LAN can reach us
    server = http.server.HTTPServer(("0.0.0.0", port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# ntfy.sh notification
# ---------------------------------------------------------------------------

def _send_ntfy(summary: str, port: int, config: dict) -> None:
    ntfy_cfg = config.get("ntfy", {})
    server = ntfy_cfg.get("server", "https://ntfy.sh").rstrip("/")
    topic = ntfy_cfg.get("topic", "")

    if not topic:
        _fatal("ntfy topic not set in config.json.")

    # Get Mac's LAN IP via UDP trick (no data sent)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as _s:
            _s.connect(("8.8.8.8", 80))
            local_ip = _s.getsockname()[0]
    except Exception:
        local_ip = "127.0.0.1"
    base_url = f"http://{local_ip}:{port}"

    headers = {
        # Headers must be latin-1 safe — no emojis here.
        # ntfy prepends Tags as emoji icons before the title (robot=🤖, key=🔑).
        "Title": "ClaudeCode",
        "Priority": "high",
        "Tags": "robot,key",
        # Use 'http' action type (not 'view') so ntfy sends a background HTTP
        # request from the app — this works on Apple Watch via companion app,
        # whereas 'view' (open URL in browser) does not work on watchOS.
        "Actions": (
            f"http, Approve, {base_url}/approve, method=GET, clear=true; "
            f"http, Always Allow, {base_url}/always, method=GET, clear=true; "
            f"http, Reject, {base_url}/reject, method=GET, clear=true"
        ),
        "Content-Type": "text/plain; charset=utf-8",
    }

    url = f"{server}/{urllib.parse.quote(topic, safe='')}"
    data = summary.encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 201, 204):
                _fatal(f"ntfy.sh returned HTTP {resp.status}")
    except Exception as e:
        _fatal(f"Failed to send ntfy notification: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # 1. Read hook JSON from stdin
    try:
        hook_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        _fatal(f"Invalid JSON from Claude Code: {e}")
        return

    # 2. Load config
    config = load_config()

    # 3. Summarize the request
    try:
        from summarizer import summarize  # type: ignore
        summary = summarize(hook_data, config)
    except Exception:
        # summarizer unavailable — use basic fallback
        tool = hook_data.get("tool_name", "Unknown")
        ti = hook_data.get("tool_input", {})
        cmd = ti.get("command", ti.get("file_path", ""))
        cmd_str = str(cmd)
        summary = f"{tool}: {cmd_str[:80]}" if cmd else f"{tool} permission requested"

    # 4. Start local callback server on fixed (or dynamic) port
    port = _get_callback_port(config)
    server = _start_callback_server(port)

    try:
        # 5. Fire the ntfy notification
        _send_ntfy(summary, port, config)

        # 6. Wait for a tap or timeout
        timeout = config.get("timeout_seconds", 60)
        tapped = _CallbackHandler._lock.wait(timeout=timeout)

        result = _CallbackHandler.result if tapped else None

    finally:
        server.shutdown()

    # 7. Output decision
    if result == "approve":
        _output_decision("allow")
    elif result == "always":
        _output_decision("allow", always=True)
    else:
        # reject OR timeout
        timeout_action = config.get("timeout_action", "deny")
        reason = "Rejected from Apple Watch." if result == "reject" else "Approval timed out — defaulting to deny."
        if result is None and timeout_action == "allow":
            _output_decision("allow", reason="Timed out — auto-approved per config.")
        else:
            _output_decision("deny", reason=reason)


if __name__ == "__main__":
    main()
