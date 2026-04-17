# Remi

Remi is a silent AI collaborator. It runs in the background, watches your shared codebase, and resolves conflicts automatically — before they cause problems.

Named after Remy from Ratatouille. Works quietly behind the scenes making everything come together.

---

## How it works

1. Each developer installs Remi once on their machine (`install.py`)
2. Run `remi init` in any project folder to start watching it
3. Remi runs silently in the background — starts automatically on login
4. When you save a file, it syncs with teammates and checks for conflicts
5. If there's a conflict, Remi resolves it automatically and writes the merged result to disk
6. Every decision is logged to `remi_log.md` in your project folder
7. `remi_updates.md` gives you a glanceable daily activity feed

No commands to remember. No terminal to keep open.

---

## Setup

### 1. Deploy the sync server

The server brokers changes between developers. Deploy `server.py` once:

```bash
# Local testing
python server.py

# Deploy to Railway (recommended)
# railway up
```

### 2. Each developer runs the installer

```bash
python install.py
```

Asks for your name and Anthropic API key. Registers Remi as a background service that starts on login.

### 3. Initialise each project

```bash
cd ~/your-project
remi init
```

Creates `.remi/config.json`, updates `.gitignore`, and registers the project. Share the Room ID it gives you with your teammates.

---

## CLI

```
remi init              Initialise Remi in the current project folder
remi status            Show all watched projects and their status
remi log               Print the last 20 conflict log entries
remi registry          Show the intent registry (what every file does)
remi rollback          List pre-merge backups
remi rollback <file>   Restore a file to its pre-merge state
remi stop              Stop watching the current project
remi help              Show all commands
```

---

## Rollback

Before every merge, Remi saves a backup of the original file to `.remi/backups/`. To see available backups:

```bash
remi rollback
```

To restore a specific file:

```bash
remi rollback game/player.js
```

---

## Configuration

Each project's config lives in `.remi/config.json` (gitignored — each developer sets their own):

```json
{
  "project_name": "MyGame",
  "room_id": "mygame2024",
  "server_url": "https://your-server.railway.app",
  "developer_name": "Alex",
  "api_key_path": "~/.collab-agent/.api_key",
  "change_ttl_hours": 24
}
```

`change_ttl_hours` controls how far back the server looks for partner changes. Default is 24 hours. Set it lower if your team commits frequently, higher if you work across time zones.

Machine-level settings (name, API key) live in `~/.collab-agent/config.json`.

---

## Remi and git

Remi and git are complementary — they operate at different layers:

- **Git** tracks intent: branches, commits, history, pull requests
- **Remi** handles real-time collisions: two people editing the same file at the same time

**Remi does not make git commits.** It writes the merged result to disk, then gets out of the way. You commit the resolved file the same way you'd commit any other change. This is intentional — Remi shouldn't decide what goes into your git history.

**Remi has no branch awareness.** It syncs changes within a room, regardless of which branch each developer is on. If your team uses feature branches heavily, Remi is most useful on a shared integration branch where multiple people are actively editing the same files.

A typical workflow:

```
You save a file  →  Remi detects a conflict  →  Remi merges and writes to disk
     ↓
You review remi_log.md  →  You commit the merged file  →  You push to git
```

---

## Known limitations

**Two-developer ceiling.** The current conflict model assumes exactly two developers touching the same file at the same time. The server's `UNIQUE(room_id, file_path, developer)` constraint and the `/push` conflict logic both reflect this. N-way conflicts (three or more people editing the same file simultaneously) are not handled — Remi will resolve the first conflict it sees and may miss later ones.

This is a known architectural limitation for v1. If your team is larger than 2, the recommendation is to use clear file ownership (avoid more than 2 people editing the same file) until N-way conflict resolution is built.

---

## Files

| File | What it does |
|------|-------------|
| `install.py` | Machine-level setup — run once per developer |
| `remi.py` | CLI entry point — `remi init`, `remi status`, etc. |
| `watcher.py` | Background daemon — watches all registered projects |
| `server.py` | Sync server — deploy this centrally |
| `agent.py` | AI conflict resolution using Claude |
| `mapper.py` | Builds a relationship map of the codebase |
| `registry.py` | Standalone intent registry viewer |

**Per-project (gitignored):**

| File | What it does |
|------|-------------|
| `.remi/config.json` | Project + developer config |
| `.remi/backups/` | Pre-merge file backups for rollback |
| `remi_log.md` | Full conflict and resolution log |
| `remi_updates.md` | Daily activity feed |
| `remi_memory.json` | Accumulated codebase patterns |

---

## Checking if Remi is running

```bash
# Mac
launchctl list | grep remi-agent

# Linux
systemctl --user status remi-agent
```

## Stopping Remi

```bash
# Mac
launchctl unload ~/Library/LaunchAgents/com.remi-agent.plist

# Linux
systemctl --user stop remi-agent
```
