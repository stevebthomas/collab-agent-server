# Remi

Remi is your AI collaborator. Works silently in the background, watches your shared codebase, and resolves conflicts before they ever cause problems.

## How it works

1. Every developer installs Remi locally
2. It runs silently in the background — starts automatically when your computer starts
3. When you save a file, it syncs with your teammates' changes
4. If there's a conflict, Remi resolves it automatically
5. Every decision is logged to `remi_log.md` in your project folder

That's it. No commands to remember. No terminal to keep open.

---

## Setup (one time)

### 1. You need a sync server running somewhere both developers can reach

For local testing (both on same network):
```bash
python server.py
```

For a real team, deploy `server.py` to Railway, Render, or any cheap host:
- Railway: https://railway.app (free tier available)
- Render: https://render.com (free tier available)

Once deployed, your server URL will look like: `https://your-app.railway.app`

### 2. Each developer runs the installer

```bash
python install.py
```

It will ask you for:
- **Your name** — so Remi knows who made each change
- **Room ID** — a shared ID your whole team uses (make one up, e.g. `mygame2024`)
- **Server URL** — the URL from step 1
- **Project folder** — full path to your local game folder
- **API key** — your Anthropic API key

### 3. That's it

Remi is now running in the background. Share the Room ID with your teammates so they connect to the same room.

---

## What you'll see

Check `remi_log.md` in your project folder whenever you're curious. It looks like this:

```
## ⚠️ Remi — 2024-01-15 14:32:01

**File:** `game/buildings/doors.py`
**Conflict:** Both developers modified on_door_enter with different hardcoded sounds
**Resolution:** Merged into dictionary mapping so each building has its own sound
```

---

## Files

| File | What it does |
|------|-------------|
| `install.py` | Run once to set everything up |
| `watcher.py` | The background daemon — watches files, syncs changes |
| `server.py` | The sync server — deploy this somewhere central |
| `agent.py` | Remi's AI brain — detects and resolves conflicts |
| `mapper.py` | Builds a relationship map of the codebase for context |
| `remi_log.md` | Auto-generated log of every conflict and resolution |
| `remi_memory.json` | Remi's accumulated knowledge about your codebase |

---

## Checking if Remi is running

**Mac:**
```bash
launchctl list | grep remi-agent
```

**Windows:**
```bash
schtasks /query /tn remi-agent
```

**Linux:**
```bash
systemctl --user status remi-agent
```

## Stopping Remi

**Mac:**
```bash
launchctl unload ~/Library/LaunchAgents/com.remi-agent.plist
```

**Windows:**
```bash
schtasks /end /tn remi-agent
```

**Linux:**
```bash
systemctl --user stop remi-agent
```
