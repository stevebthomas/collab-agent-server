# Remi

<p align="center">
  <img src="assets/remi-logo.png" alt="Remi" width="400"/>
</p>

**Pre-push, intent-aware conflict detection for teams using AI-assisted development.**

> *Git catches conflicts after a push, at the syntax level. Remi catches them before a push, at the level of what each developer was trying to do.*

---

## The Problem

Vibecoding is here. Developers using Cursor, Claude Code, and similar tools now produce large, coherent chunks of AI-generated code in a single session. They move fast, but they often don't deeply understand every line their AI wrote.

This creates a new category of collaboration failure: two developers are building in the same repo simultaneously, each with their AI assistant making interconnected changes across multiple files. They don't conflict in git; they conflict in *intent*. One developer's agent restructured the collision system to be event-driven. The other's added direct calls to the old interface. The code merges cleanly. The game breaks at runtime.

Git was designed for humans who understand their own changes. Vibecoders need something different.

## What Remi Does

Remi runs as a silent background daemon on each developer's machine. When a file is saved, it:

1. **Captures intent**: Claude Haiku generates a plain-English description of the file's purpose in the codebase, stored in the shared intent registry
2. **Syncs with the team**: pushes the change and intent to a shared Flask server
3. **Detects conflicts**: cross-references against teammates' recent changes across the full codebase
4. **Resolves with Claude Opus**: the agent reads both developers' code and their stated intent, and produces a merged result that preserves what both were trying to accomplish
5. **Writes to disk**: the resolved file lands silently in your project, ready to review and commit

There's no merge dialog, no terminal to open, and no interruption to your workflow.

## Architecture

```
┌─────────────────────────────────┐     ┌─────────────────────────────────┐
│         Developer A             │     │         Developer B             │
│  ┌──────────────────────────┐   │     │  ┌──────────────────────────┐   │
│  │  watcher.py (launchd /   │   │     │  │  watcher.py (launchd /   │   │
│  │  systemd / Task Sched.)  │   │     │  │  systemd / Task Sched.)  │   │
│  │  - watchdog file events  │   │     │  │  - watchdog file events  │   │
│  │  - SHA256 content hash   │   │     │  │  - SHA256 content hash   │   │
│  │  - 3s debounce           │   │     │  │  - 3s debounce           │   │
│  └──────────┬───────────────┘   │     │  └──────────┬───────────────┘   │
│             │                   │     │             │                   │
│  ┌──────────▼───────────────┐   │     │  ┌──────────▼───────────────┐   │
│  │  compactor.py            │   │     │  │  compactor.py            │   │
│  │  - burst collapsing      │   │     │  │  - burst collapsing      │   │
│  │  - intent dedup          │   │     │  │  - intent dedup          │   │
│  │  - quality filtering     │   │     │  │  - quality filtering     │   │
│  └──────────┬───────────────┘   │     │  └──────────┬───────────────┘   │
│             │                   │     │             │                   │
│  ┌──────────▼───────────────┐   │     │  ┌──────────▼───────────────┐   │
│  │  Claude Haiku 4.5        │   │     │  │  Claude Haiku 4.5        │   │
│  │  Intent inference        │   │     │  │  Intent inference        │   │
│  └──────────┬───────────────┘   │     │  └──────────┬───────────────┘   │
└─────────────┼───────────────────┘     └─────────────┼───────────────────┘
              │                                       │
              └──────────────┬────────────────────────┘
                             │
                ┌────────────▼────────────┐
                │     server.py           │
                │     (Railway / Flask)   │
                │                         │
                │  - /push  (receive)     │
                │  - /pull  (poll)        │
                │  - /intent/register     │
                │  - SQLite persistence   │
                │  - 24h TTL window       │
                └────────────┬────────────┘
                             │  conflict detected
                             │
                ┌────────────▼────────────┐
                │     agent.py            │
                │  (Claude Opus 4.6)      │
                │                         │
                │  Input:                 │
                │  - Dev A's code+intent  │
                │  - Dev B's code+intent  │
                │  - Codebase map         │
                │  - Intent registry      │
                │  - Learned patterns     │
                │                         │
                │  Output:                │
                │  - Conflict analysis    │
                │  - Merged resolution    │
                │  - Cross-file risks     │
                │  - Updated patterns     │
                └────────────┬────────────┘
                             │
                   writes merged file to disk
                   logs to remi_log.md
                   macOS notification
```

## Key Components

| File | Role |
|---|---|
| `watcher.py` | Background daemon: monitors all registered projects, debounces saves, detects real content changes via SHA256 hashing. Registers as launchd (macOS), systemd (Linux), or Task Scheduler (Windows). |
| `agent.py` | Core AI layer: Claude Opus 4.6 analyzes intent conflicts, produces merged resolutions with cross-file risk flags and learned patterns |
| `server.py` | Flask sync server: brokers changes between developers, persists to SQLite, deployed on Railway |
| `remi.py` | CLI entry point: `remi init`, `remi status`, `remi log`, `remi rollback` |
| `mapper.py` | Codebase relationship mapper: builds file dependency graph to give the agent cross-file context |
| `compactor.py` | Event log compaction: collapses burst saves, deduplicates intents, filters noise (91% compression in testing) |
| `event_log.py` | Append-only event log: clean separation between raw source log and rendered activity feed |
| `install.py` | One-time machine setup: stores API key, registers background service for the current OS |

## Cross-Platform Daemon

Remi's background watcher runs on macOS, Linux, and Windows. `install.py` detects the OS and registers the appropriate service automatically:

- **macOS**: launchd (`~/Library/LaunchAgents/com.remi-agent.plist`), starts on login
- **Linux**: systemd user service (`~/.config/systemd/user/remi-agent.service`), starts on login
- **Windows**: Task Scheduler job, starts on login

The watcher logic itself (`watchdog`) is platform-agnostic. Only the daemon registration differs.

## What Claude Opus Receives

The conflict resolution agent receives more than the two code versions:

```python
analyze_and_resolve(
    dev_a={
        "file": "game/physics.py",
        "content": "...",           # full file content
        "intent": "Manages rigid-body collision detection and physics step loop"
    },
    dev_b={
        "file": "game/physics.py",
        "content": "...",           # full file content
        "intent": "Manages rigid-body collision detection and physics step loop"
    },
    codebase_context="...",         # connected files from mapper.py
    config={
        "intent_registry": {...},   # what every file in the project does
        "memory": {...}             # patterns learned from prior merges
    }
)
```

The agent returns: a conflict analysis, a complete merged file, cross-file risk flags ("this change may break `enemy_ai.py` line 47"), and updated learned patterns for future merges.

## Event Log Compaction

A key operational challenge: file watchers generate enormous noise. A single "save" in VS Code can trigger 8–12 filesystem events. Without filtering, the activity feed becomes unusable and API costs spike.

`compactor.py` solves this with three strategies applied in sequence:

- **Burst collapsing**: multiple saves to the same file within a 3-second window → single event
- **Failed intent filtering**: drops events where Haiku returned an empty or trivial description
- **Description deduplication**: suppresses re-emission when a file's intent hasn't changed

**Result:** 91% reduction in log entries during testing on a real project.

The raw event log (`event_log.py`) captures everything before compaction as append-only NDJSON. The compacted activity feed is a derived view, not the source of truth. This means compaction logic can be tuned without losing history.

## Setup

### Deploy the sync server

```bash
# Railway (recommended)
railway up

# Local testing
python server.py
```

### Install on each developer's machine

```bash
python install.py
# Prompts for name + Anthropic API key
# Detects OS and registers appropriate background service
```

### Initialize each project

```bash
cd ~/your-project
remi init
# Creates .remi/config.json
# Updates .gitignore
# Returns a Room ID to share with teammates
```

## CLI

```
remi init              Initialize Remi in the current project
remi status            Show all watched projects and their status
remi log               Print recent conflict log entries
remi registry          Show the intent registry for the current project
remi rollback          List all logged merges available to restore
remi rollback <id>     Restore a specific previous merge
remi stop              Stop watching the current project
remi help              Show all commands
```

## Configuration

Each project's config lives in `.remi/config.json` (gitignored; each developer sets their own):

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

Machine-level settings live in `~/.collab-agent/config.json`.

## Remi and Git

Remi and git operate at different layers and are designed to complement each other:

- **Git** resolves *syntactic* conflicts, after a push, on a per-line basis
- **Remi** resolves *semantic* conflicts, before a push, with understanding of developer intent

Remi does not make commits. It writes the merged result to disk, then gets out of the way. You review and commit normally. This is intentional: Remi shouldn't decide what goes into your git history.

## Design Decisions

**Why pre-push?** Post-push resolution (git merge, PRs) is already well-solved. The gap is the period when two developers are both actively building (AI-assisted or not) before either has committed. That's when the collision happens, and that's the only window where intent is still fresh and resolvable automatically.

**Why intent matters for vibecoders?** When a developer writes code manually, they understand it. When an AI writes 200 lines and the developer accepts it, they may not. Remi's intent registry captures the *purpose* of each file at the moment it's written, before that context is lost.

**Why Claude Haiku 4.5 for intent, Claude Opus 4.6 for resolution?** These two tasks have opposite profiles. Intent inference (`claude-haiku-4-5-20251001`) is high-frequency and low-complexity: it runs on every file save, produces a single descriptive sentence, and needs to be fast enough not to interrupt the developer's workflow. At $1/M input tokens and more than 2× the speed of Sonnet, Haiku is purpose-built for this. Conflict resolution (`claude-opus-4-6`) is low-frequency and high-complexity: it runs only when a real conflict is detected, reasons across two full codebases and their cross-file dependencies, and produces a complete merged file with risk analysis. Opus is the right call when quality is the only thing that matters and the event is rare enough that cost per-resolution is negligible. Using the wrong model in either direction would mean paying Opus prices for thousands of trivial sentence generations, or getting shallow reasoning on a merge that could silently corrupt a shared codebase.

**Why SQLite on Railway?** Simple, zero-dependency persistence that survives server restarts. The 24-hour TTL window keeps the database small. A Redis layer would be a natural next step at scale, but SQLite is the right call for the two-developer use case this is optimized for.

**Why an append-only event log?** The compaction layer needs a source of truth that's independent of its output. An append-only NDJSON log means compaction logic can be changed, replayed, or debugged without data loss. The rendered activity feed (`remi_updates.md`) is always a derived view.

## Known Limitations

**Two-developer ceiling.** The current conflict model assumes exactly two developers. The server's `UNIQUE(room_id, file_path, developer)` constraint and `/push` conflict logic both reflect this. N-way conflicts are not handled. Recommended workaround for larger teams: clear file ownership conventions.

**iCloud Desktop footgun.** Projects on `~/Desktop/` on macOS are likely iCloud-synced, which generates unexpected filesystem metadata events. Remi's SHA256 content-hashing guards against acting on these, but it remains a setup consideration.

## Status

Remi is in active development, currently being dogfooded on a real collaborative indie game project. The compaction layer, intent registry, and conflict resolution loop are all working end-to-end. Upcoming work: `remi rollback <id>` (restore any logged merge, not just the most recent), per-file watch exclusions, and a dry-run mode for testing without writes.

---

*Built with Python, Flask, SQLite, Railway, watchdog, and the Anthropic API (Claude Opus 4.6 + Haiku 4.5).*
