#!/usr/bin/env python3
"""
watch_approver.py
Claude Code PermissionRequest hook — sends the request to your Apple Watch
via ntfy.sh and waits for an interactive tap to approve, always-approve, or reject.

Usage (configured automatically by install.sh):
    Registered as a Claude Code PermissionRequest hook. Claude Code pipes JSON
    to stdin; this script outputs a JSON decision to stdout and exits 0.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.parse
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HOOK_DIR = Path(__file__).parent
LEGACY_CONFIG = HOOK_DIR / "config.json"
USER_CONFIG = Path.home() / ".config" / "claude-afk" / "config.json"
FALLBACK_CONFIG = Path.home() / ".claude-afk-config.json"


def load_config() -> dict:
    config_path = None
    
    if USER_CONFIG.exists():
        config_path = USER_CONFIG
    elif FALLBACK_CONFIG.exists():
        config_path = FALLBACK_CONFIG
    elif LEGACY_CONFIG.exists():
        config_path = LEGACY_CONFIG
        
    if not config_path:
        _fatal(
            f"Config not found! Please create {USER_CONFIG}.\n"
            f"See https://github.com/fomyio/claude-afk for instructions."
        )
        return {} # never reached

    try:
        with open(config_path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        _fatal(f"Invalid JSON in {config_path}: {e}")
        return {}


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
# Cloud relay (ntfy pub/sub) — replaces local callback server
# ---------------------------------------------------------------------------
# Instead of making the phone HTTP-call back to the Mac's LAN IP
# (which fails with AP-isolation, cellular, etc.) we:
#   1. Generate a unique one-time response topic: <main_topic>_resp_<token>
#   2. The notification action buttons POST "approve"/"always"/"reject" to that topic
#   3. The Mac polls the response topic via HTTPS until it gets a decision
# Requires no open ports, works from cellular, Watch, or any network.

_response_result: dict = {"value": None}   # "approve" | "always" | "reject"
_response_event = threading.Event()


def _make_response_topic(base_topic: str) -> tuple:
    """Return (response_topic, token) for this request."""
    token = secrets.token_urlsafe(12)
    return f"{base_topic}_resp_{token}", token


def _poll_response_topic(server: str, response_topic: str, timeout: float) -> None:
    """Subscribe to the ntfy response topic and wait for a decision message.

    Runs in a background thread. Sets _response_event when a valid message arrives.
    Uses ntfy's JSON polling (GET /topic/json?poll=1&since=all is avoided;
    we use a continuous SSE-style connection with since=0 to catch new messages).
    """
    url = f"{server}/{urllib.parse.quote(response_topic, safe='')}/json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not _response_event.is_set():
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/x-ndjson"})
            with urllib.request.urlopen(req, timeout=min(30, deadline - time.monotonic())) as resp:
                for raw_line in resp:
                    if _response_event.is_set():
                        break
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("event") == "open":
                        continue
                    decision = msg.get("message", "").strip().lower()
                    if decision in ("approve", "always", "reject"):
                        _response_result["value"] = decision
                        _response_event.set()
                        return
        except Exception:
            # Network blip — retry loop handles it
            time.sleep(1)


# ---------------------------------------------------------------------------
# Auto-approve logic
# ---------------------------------------------------------------------------

def _is_auto_approved(hook_data: dict, config: dict) -> bool:
    """Check if the requested action matches user-defined safe patterns."""
    import fnmatch
    
    # Only auto-approve Bash commands for now, as that's 99% of the noise
    tool_name = hook_data.get("tool_name")
    if tool_name != "Bash":
        return False
        
    cmd = hook_data.get("tool_input", {}).get("command", "")
    if not cmd:
        return False
        
    # Default safe read-only commands if user hasn't configured any
    default_rules = [
        "ls*", "cat*", "pwd", "whoami", "echo*",
        "git status*", "git branch*", "git log*", "git diff*", "git show*"
    ]
    
    rules = config.get("auto_approve", default_rules)
    
    # Check if the command matches any glob pattern
    # Clean up the command (strip trailing newlines)
    cmd = cmd.strip()
    return any(fnmatch.fnmatch(cmd, rule) for rule in rules)


# ---------------------------------------------------------------------------
# ntfy.sh notification
# ---------------------------------------------------------------------------

def _send_ntfy(summary: str, response_topic: str, config: dict) -> bool:
    """Push a notification with Approve/Reject buttons that publish to response_topic.

    Returns True on success, False if ntfy is not configured.
    The buttons use ntfy's 'http' action to POST a plain body (approve/always/reject)
    directly to our private response topic on ntfy.sh — no LAN IP needed.
    """
    ntfy_cfg = config.get("ntfy", {})
    server = ntfy_cfg.get("server", "https://ntfy.sh").rstrip("/")
    topic = ntfy_cfg.get("topic", "")

    if not topic:
        return False

    resp_url = f"{server}/{urllib.parse.quote(response_topic, safe='')}"

    headers = {
        "Title": "ClaudeCode Plugin",
        "Priority": "high",
        "Tags": "electric_plug,robot,key",
        # Buttons POST a plain-text body to the response topic on ntfy.sh.
        # This works from any network — no LAN IP needed.
        "Actions": (
            f"http, Approve, {resp_url}, method=POST, body=approve, clear=true; "
            f"http, Always Allow, {resp_url}, method=POST, body=always, clear=true; "
            f"http, Reject, {resp_url}, method=POST, body=reject, clear=true"
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

    return True


def _send_ntfy_resolution(summary: str, result_icon: str, original_id, config: dict) -> None:
    """Send a follow-up notification or update to show the final decision."""
    ntfy_cfg = config.get("ntfy", {})
    server = ntfy_cfg.get("server", "https://ntfy.sh").rstrip("/")
    topic = ntfy_cfg.get("topic", "")

    if not topic:
        return
        
    headers = {
        "Title": "ClaudeCode (Resolved)",
        "Priority": "default", 
        "Tags": result_icon,
        "Content-Type": "text/plain; charset=utf-8",
    }
    
    url = f"{server}/{urllib.parse.quote(topic, safe='')}"
    data = summary.encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    
    try:
        # Fire and forget; don't fail the approval if this fails
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Stage 1 — terminal keypress (default) and macOS dialog (opt-in)
# ---------------------------------------------------------------------------

def _show_macos_dialog(summary: str, base_url: str, token: str, delay: int) -> subprocess.Popen:  # type: ignore[type-arg]
    """Show a native macOS dialog in a background subprocess.

    When the user clicks Approve or Reject the dialog uses `curl` to hit the
    local callback server — exactly the same endpoint ntfy uses for Stage 2.
    The main loop is agnostic to which stage produced the response.

    Args:
        summary:  Short description of the permission request.
        base_url: Loopback URL (http://127.0.0.1:PORT) — same machine, no LAN needed.
        token:    Per-request secret token to include in callback URL.
        delay:    Seconds the dialog stays open before auto-dismissing (giving up after).
    """
    # Escape single quotes in summary so AppleScript string literals are safe.
    safe = summary.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\u2019")

    approve_url = f"{base_url}/approve?token={token}"
    reject_url  = f"{base_url}/reject?token={token}"

    # Write to a temp file to avoid shell-escaping nightmares with -e
    script = f'''
tell application "System Events"
    set theResult to display dialog "{safe}" ¬
        with title "Claude Code" ¬
        buttons {{"Reject", "Approve"}} ¬
        default button "Approve" ¬
        giving up after {delay}
end tell
if gave up of theResult is false then
    set btn to button returned of theResult
    if btn is "Approve" then
        do shell script "curl -sf '" & "{approve_url}" & "' &>/dev/null &"
    else
        do shell script "curl -sf '" & "{reject_url}" & "' &>/dev/null &"
    end if
end if
'''
    tmp = tempfile.NamedTemporaryFile(suffix=".applescript", mode="w", delete=False)
    tmp.write(script)
    tmp.flush()
    return subprocess.Popen(
        ["osascript", tmp.name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ANSI colours — fall back gracefully on terminals that don't support them
_BOLD   = "\033[1m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"


def _wait_for_terminal_keypress(base_url: str, token: str, delay: int, summary: str, is_configured: bool = True) -> bool:
    """Print a one-line prompt to stderr and wait up to `delay` seconds for a keypress.

    Reads directly from /dev/tty so it works even though Claude Code has
    redirected stdin.  Returns True if the user responded, False on timeout.
    Silently returns False when no TTY is available (CI, Windows, piped input).

    Keys:
        a / y  → approve
        n / r  → reject
        w      → skip to Watch immediately
        Enter  → approve (default)
    """
    import select
    try:
        import tty as _tty
        import termios as _termios
    except ImportError:
        return False  # Windows or no termios — skip silently

    try:
        tty_file = open("/dev/tty", "rb", buffering=0)  # noqa: WPS515
    except OSError:
        return False  # no controlling TTY (e.g. running in background)

    # Print prompt to stderr (stdout carries the JSON decision)
    if is_configured:
        prompt = (
            f"\r{_YELLOW}{_BOLD}⚡ claude-afk{_RESET} › {summary}  "
            f"{_GREEN}[A]{_RESET}pprove  "
            f"{_RED}[R]{_RESET}eject  "
            f"{_DIM}[W]atch ({delay}s)…{_RESET}  "
        )
    else:
        prompt = (
            f"\r{_YELLOW}{_BOLD}⚡ claude-afk{_RESET} › {summary}  "
            f"{_GREEN}[A]{_RESET}pprove  "
            f"{_RED}[R]{_RESET}eject  "
            f"{_DIM}(ntfy unconfigured){_RESET}  "
        )
    print(prompt, end="", flush=True, file=sys.stderr)

    fd = tty_file.fileno()
    old_attrs = _termios.tcgetattr(fd)
    responded = False
    try:
        _tty.setraw(fd)
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ready, _, _ = select.select([tty_file], [], [], min(0.2, remaining))
            if not ready:
                continue
            ch = tty_file.read(1).decode("utf-8", errors="ignore").lower()
            if ch in ("a", "y", "\r", "\n"):      # approve
                _response_result["value"] = "approve"
                _response_event.set()
                responded = True
                break
            elif ch in ("n", "r"):                # reject
                _response_result["value"] = "reject"
                _response_event.set()
                responded = True
                break
            elif ch == "w":                       # skip to Watch now
                break
            elif ch in ("\x03", "\x04"):          # Ctrl-C / Ctrl-D — reject
                _response_result["value"] = "reject"
                _response_event.set()
                responded = True
                break
    except Exception:
        pass
    finally:
        try:
            _termios.tcsetattr(fd, _termios.TCSADRAIN, old_attrs)
        except Exception:
            pass
        tty_file.close()
        # Clear the prompt line
        print(f"\r{' ' * 80}\r", end="", flush=True, file=sys.stderr)

    return responded



def _build_inline_summary(hook_data: dict, project_name: str) -> str:
    """Fast, no-API summary for the terminal prompt (shown before the delay)."""
    tool = hook_data.get("tool_name", "Unknown")
    ti   = hook_data.get("tool_input", {})
    cmd  = ti.get("command", ti.get("file_path", ""))
    cmd_str = str(cmd).strip() if cmd else ""
    if cmd_str and len(cmd_str) > 80:
        cmd_str = cmd_str[:77] + "…"
    return f"[{project_name}] {tool}: {cmd_str}" if cmd_str else f"[{project_name}] {tool} permission requested"


def main() -> None:
    # 1. Read hook JSON from stdin
    try:
        hook_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        _fatal(f"Invalid JSON from Claude Code: {e}")
        return

    # 2. Load config
    config = load_config()

    # 3. Check for auto-approve (skips everything else if matched)
    if _is_auto_approved(hook_data, config):
        _output_decision("allow", reason="Auto-approved by rules in config.json")
        return

    # 4. Extract project context (from cwd) — cheap, no API call
    cwd          = hook_data.get("cwd", "")
    project_name = os.path.basename(cwd) if cwd else "Unknown"

    # 5. Build a FAST inline summary for the terminal prompt (no LLM call yet).
    terminal_summary = _build_inline_summary(hook_data, project_name)

    ntfy_cfg    = config.get("ntfy", {})
    ntfy_server = ntfy_cfg.get("server", "https://ntfy.sh").rstrip("/")
    topic       = ntfy_cfg.get("topic", "")

    total_timeout    = config.get("timeout_seconds", 60)
    escalation_delay = total_timeout if not topic else config.get("escalation_delay_seconds", 10)

    # 6. Create a unique one-time response topic for this request.
    #    The phone's Approve/Reject buttons POST to this topic on ntfy.sh cloud.
    response_topic, _token = _make_response_topic(topic or "clauding-afk-default")

    # Reset cloud relay state
    _response_result["value"] = None
    _response_event.clear()

    macos_proc = None
    ntfy_sent  = False

    try:
        # ── Stage 1a: Terminal keypress ───────────────────────────────────────
        tapped = _wait_for_terminal_keypress(
            "", "", escalation_delay, terminal_summary,
            is_configured=bool(topic)
        )

        # ── Stage 1b (optional): macOS dialog ────────────────────────────────
        if not tapped and config.get("macos_dialog", False) and sys.platform == "darwin":
            macos_proc = _show_macos_dialog(
                terminal_summary, "http://127.0.0.1:1", "", escalation_delay
            )
            tapped = _response_event.wait(timeout=escalation_delay)

        if not tapped:
            # ── Stage 2: ntfy → phone / Apple Watch ──────────────────────────
            try:
                import importlib.util as _ilu
                _spec = _ilu.spec_from_file_location(
                    "summarizer",
                    Path(__file__).parent / "summarizer.py"
                )
                _mod = _ilu.module_from_spec(_spec)  # type: ignore
                _spec.loader.exec_module(_mod)  # type: ignore
                watch_summary = _mod.summarize(hook_data, config, project_name)
            except Exception:
                watch_summary = terminal_summary

            ntfy_sent = _send_ntfy(watch_summary, response_topic, config)

            if ntfy_sent:
                poll_thread = threading.Thread(
                    target=_poll_response_topic,
                    args=(ntfy_server, response_topic, total_timeout - escalation_delay),
                    daemon=True
                )
                poll_thread.start()

            remaining = total_timeout - escalation_delay
            _response_event.wait(timeout=max(remaining, 5))
        else:
            watch_summary = terminal_summary

        result = _response_result["value"]

    finally:
        if macos_proc is not None:
            try:
                macos_proc.terminate()
            except Exception:
                pass

    # 7. Output decision
    if result == "approve":
        if ntfy_sent:
            _send_ntfy_resolution(watch_summary, "white_check_mark", None, config)
        _output_decision("allow")
    elif result == "always":
        if ntfy_sent:
            _send_ntfy_resolution(watch_summary, "white_check_mark", None, config)
        _output_decision("allow", always=True)
    else:
        timeout_action = config.get("timeout_action", "deny")
        reason = (
            "Rejected from Watch/Phone."
            if result == "reject"
            else "Approval timed out — defaulting to deny."
        )
        if result is None and timeout_action == "allow":
            if ntfy_sent:
                _send_ntfy_resolution(watch_summary, "white_check_mark", None, config)
            _output_decision("allow", reason="Timed out — auto-approved per config.")
        else:
            if ntfy_sent:
                icon = "no_entry_sign" if result == "reject" else "hourglass"
                _send_ntfy_resolution(watch_summary, icon, None, config)
            _output_decision("deny", reason=reason)


if __name__ == "__main__":
    main()
