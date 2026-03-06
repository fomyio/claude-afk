# 🔌🤖 clauding-afk

<img width="1024" height="339" alt="image" src="https://github.com/user-attachments/assets/40a7436a-85cb-4489-b46a-0d4d160d226e" />

---

**Claude codes. You approve from anywhere — couch, coffee shop, or mid-cat-cuddle.**

Kick off a long task and walk away. When Claude needs a decision, your **Apple Watch, iPhone, or Android** buzzes. Tap **Approve** and it keeps going.

**Works with all Claude plans** · **Claude Code terminal** · **VS Code extension** · **No cloud server needed**

---

## How it works

```
Claude wants to run a command
         │
         ▼
clauding-afk (Claude Code plugin)
  1. Shows a quick prompt in your terminal (tap A to approve in < 1s)
  2. After 10s with no response → sends a push notification via ntfy.sh
  3. Tap Approve on your phone/Watch → Claude continues instantly
         │
    ┌────┴────────────────┐
    │  Approve            │ → Claude continues
    │  Always Allow       │ → Claude continues + adds to allow-list
    │  Reject             │ → Claude tries another approach
    └─────────────────────┘
```

The **Approve button** works through the internet (ntfy.sh cloud relay) — no ports to open, no local network required. Tap from anywhere.

---

## Demo

![clauding-afk-demo-ezgif com-video-to-gif-converter](https://github.com/user-attachments/assets/912a5655-b186-4db8-b9d5-6e7b5585cfef)

> **Apple Watch:** vibrates with a heads-up alert. Action buttons are on the iPhone/phone. Watch = your alert, phone = your control.

---

## Requirements

- **Claude Code CLI** — any plan (Free, Pro, Max, or API)
- **Python 3.8+** (pre-installed on macOS)
- **[ntfy](https://ntfy.sh/)** app on your iPhone / Android
- Apple Watch *(optional — for wrist alerts)*

---

## ⚡ Install — 2 commands

No cloning, no scripts. Install directly as a Claude Code plugin:

```bash
# Step 1: Add the fomyio marketplace (one-time)
claude plugin marketplace add fomyio/clauding-afk

# Step 2: Install the plugin
claude plugin install clauding-afk
```

That's it. The hook registers automatically — no manual config file edits needed.

### Update later

```bash
claude plugin marketplace update fomyio && claude plugin update clauding-afk@fomyio
```

---

## Setup ntfy (2 minutes)

### 1. Install the ntfy app
- **iPhone:** [App Store](https://apps.apple.com/app/ntfy/id1625396336)
- **Android:** [Play Store / F-Droid](https://ntfy.sh/#subscribe)

### 2. Subscribe to your private topic
Open the app → tap **+** → enter a **secret, unguessable topic name** (e.g. `my_claude_alerts_x9k2p`). This is your channel — keep it private.

### 3. Add your topic to the config

```bash
# Create config from the example
mkdir -p ~/.config/claude-afk
cp "$(claude plugin path clauding-afk)/config.example.json" ~/.config/claude-afk/config.json

# Edit and set your topic
nano ~/.config/claude-afk/config.json
```

Set `ntfy.topic` to your chosen topic name. That's the only required change.

### 4. Enable Local Network access (iPhone only)

Go to **iPhone Settings → Privacy & Security → Local Network** and make sure **ntfy** is toggled **ON**.

### 5. Test it

Open Claude Code and ask it to do something outside the auto-approve list (e.g., `create a file called test.txt`). After ~10 seconds the notification should arrive on your phone with **Approve / Always Allow / Reject** buttons.

---

## Configuration

Config lives at `~/.config/claude-afk/config.json`:

```json
{
  "ntfy": {
    "topic": "your-secret-topic",
    "server": "https://ntfy.sh"
  },
  "summarizer": {
    "enabled": true,
    "model": "claude-haiku-3-5"
  },
  "escalation_delay_seconds": 10,
  "timeout_seconds": 60,
  "timeout_action": "deny",
  "macos_dialog": false
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `ntfy.topic` | `""` | Your private ntfy topic — **required for phone/Watch notifications** |
| `ntfy.server` | `"https://ntfy.sh"` | Use your own self-hosted ntfy server for full privacy |
| `summarizer.enabled` | `true` | AI plain-English summaries using Claude Haiku |
| `summarizer.model` | `"claude-haiku-3-5"` | Swap to any LiteLLM-compatible model |
| `escalation_delay_seconds` | `10` | Seconds to wait at terminal before sending to watch |
| `timeout_seconds` | `60` | Total wait time before auto-action |
| `timeout_action` | `"deny"` | `"deny"` or `"allow"` on timeout |
| `macos_dialog` | `false` | Show a native macOS dialog before escalating to Watch |
| `auto_approve` | *(read-only cmds)* | List of glob patterns to silently auto-approve |

> **API key:** Claude Code automatically passes `ANTHROPIC_API_KEY` to all hooks. The AI summarizer works without any extra setup.

### Smart Auto-Approve

Commands matching these glob patterns are silently approved without any notification:

```json
"auto_approve": ["ls*", "cat*", "pwd", "whoami", "git status*", "git log*"]
```

Customize or clear the list to control what requires approval.

---

## Compatibility

| Environment | Supported? |
|-------------|------------|
| `claude` in any terminal | ✅ Full support |
| VS Code with Claude Code extension | ✅ Full support |
| Claude Free plan | ✅ Works |
| Claude Pro plan | ✅ Works |
| Claude Max / API | ✅ Works |
| Apple Watch | ✅ Alert buzz (action on iPhone) |
| Android phone | ✅ Full action buttons |

> Uses Claude Code's `PermissionRequest` hook — available in **all** Claude Code environments (terminal and VS Code).

---

## Security

- **Cloud relay:** Approve/Reject buttons POST to a **unique, one-time private ntfy topic** (`<your_topic>_resp_<random>`). The Mac polls that topic over HTTPS. No ports are opened on your Mac.
- **Private topic:** Your topic name is kept secret — only you are subscribed.
- **No data stored:** The plugin runs entirely on your own machine. Nothing is sent to any external server except the ntfy notification itself.

---

## Project structure

```
clauding-afk/
├── plugin/
│   ├── .claude-plugin/     ← Plugin manifest (v1.2.0)
│   ├── hooks/              ← Auto-registers PermissionRequest hook
│   ├── watch_approver.py   ← Hook: terminal prompt, ntfy push, cloud relay
│   ├── summarizer.py       ← AI summary via LiteLLM
│   └── config.example.json ← Config template
├── .claude-plugin/         ← Marketplace manifest
└── README.md
```

---

## License

MIT — use it, fork it, build on it.
