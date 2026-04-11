#!/usr/bin/env python3
"""
Remi — Multi-project collaborative coding agent
Usage: remi <command>

Commands:
  remi init      Initialise Remi in the current project folder
  remi status    Show all watched projects and their status
  remi log       Print the last 20 conflict log entries for this project
  remi registry  Show the intent registry for this project
  remi stop      Stop watching the current project
  remi help      Show this help message
"""

import os
import sys
import json
import requests
from pathlib import Path
from datetime import datetime

GLOBAL_CONFIG_DIR  = Path.home() / ".collab-agent"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "config.json"
PROJECTS_PATH      = GLOBAL_CONFIG_DIR / "projects.json"
SERVER_URL         = "https://collab-agent-server-production.up.railway.app"


def load_global_config() -> dict:
    if not GLOBAL_CONFIG_PATH.exists():
        return {}
    with open(GLOBAL_CONFIG_PATH) as f:
        return json.load(f)


def load_projects() -> dict:
    if not PROJECTS_PATH.exists():
        return {}
    with open(PROJECTS_PATH) as f:
        return json.load(f)


def save_projects(projects: dict):
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROJECTS_PATH, "w") as f:
        json.dump(projects, f, indent=2)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_init():
    cwd      = Path.cwd()
    remi_dir = cwd / ".remi"

    if remi_dir.exists():
        print(f"⚠️  Remi is already initialised in {cwd.name}")
        print(f"   Delete .remi/ and run again to reinitialise.")
        return

    global_config = load_global_config()

    print(f"\n🐀 Initialising Remi in {cwd.name}\n")

    # Developer name — reuse global if set
    default_dev    = global_config.get("developer_name", "")
    if default_dev:
        developer_name = default_dev
        print(f"   Developer: {developer_name} (from global config)")
    else:
        developer_name = input("   Your name (e.g. Alex): ").strip() or "Developer"

    # Project name
    name_input   = input(f"   Project name [{cwd.name}]: ").strip()
    project_name = name_input or cwd.name

    # Room ID
    default_room = project_name.lower().replace(" ", "-") + "-" + str(abs(hash(str(cwd))) % 10000)
    room_input   = input(f"   Room ID [{default_room}]: ").strip()
    room_id      = room_input or default_room

    # Create .remi/ and write config
    remi_dir.mkdir()
    project_config = {
        "project_name":  project_name,
        "room_id":       room_id,
        "server_url":    SERVER_URL,
        "developer_name": developer_name,
        "api_key_path":  str(GLOBAL_CONFIG_DIR / ".api_key")
    }
    with open(remi_dir / "config.json", "w") as f:
        json.dump(project_config, f, indent=2)

    # Update .gitignore
    gitignore_path = cwd / ".gitignore"
    entries        = [".remi/", "remi_log.md", "remi_memory.json", "codebase_map.json"]
    existing       = gitignore_path.read_text() if gitignore_path.exists() else ""
    new_entries    = [e for e in entries if e not in existing]
    if new_entries:
        with open(gitignore_path, "a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n# Remi\n")
            for entry in new_entries:
                f.write(f"{entry}\n")
        print(f"   Added to .gitignore: {', '.join(new_entries)}")

    # Register in global projects registry
    projects = load_projects()
    projects[str(cwd)] = {
        "name":    project_name,
        "room_id": room_id,
        "active":  True
    }
    save_projects(projects)

    print(f"""
✓ Remi initialised in {project_name}
  Room ID: {room_id} — share this with your team
  Watching: {cwd}
  Run 'remi status' to check everything is working
""")


def cmd_status():
    projects = load_projects()
    if not projects:
        print("\nNo projects registered. cd into a project and run 'remi init'.\n")
        return

    print(f"\n📋 Remi Projects\n{'─' * 75}")
    print(f"{'Project':<25} {'Room ID':<22} {'Status':<10} Path")
    print(f"{'─' * 75}")

    for path, info in projects.items():
        status     = "● active" if info.get("active") else "○ stopped"
        name       = info.get("name", Path(path).name)
        room       = info.get("room_id", "—")
        short_path = path.replace(str(Path.home()), "~")
        print(f"{name:<25} {room:<22} {status:<10} {short_path}")

    print(f"{'─' * 75}\n")


def cmd_log():
    log_path = Path.cwd() / "remi_log.md"
    if not log_path.exists():
        print("No remi_log.md found here. Is Remi watching this project?")
        return

    content = log_path.read_text()
    entries = [e.strip() for e in content.split("---") if e.strip()]
    print("\n---\n".join(entries[-20:]))


def cmd_registry():
    config_path = Path.cwd() / ".remi" / "config.json"
    if not config_path.exists():
        print("No .remi/config.json found. Run 'remi init' first.")
        return

    with open(config_path) as f:
        config = json.load(f)

    try:
        r = requests.get(
            f"{config['server_url']}/intent/registry",
            params={"room_id": config["room_id"]},
            timeout=10
        )
        r.raise_for_status()
        registry = r.json()
    except Exception as e:
        print(f"❌ Could not fetch registry: {e}")
        return

    if not registry:
        print("📭 Intent registry is empty.")
        return

    print(f"\n📋 Intent Registry — {config.get('project_name', config['room_id'])}")
    print(f"{'─' * 90}")
    print(f"{'File':<35} {'Owner':<12} {'Intent':<30} {'Updated'}")
    print(f"{'─' * 90}")
    for file_path, info in sorted(registry.items()):
        try:
            dt            = datetime.fromisoformat(info.get("updated", ""))
            updated_short = dt.strftime("%m/%d %H:%M")
        except Exception:
            updated_short = ""
        print(
            f"{file_path[:34]:<35} "
            f"{info.get('developer','')[:11]:<12} "
            f"{info.get('intent','')[:29]:<30} "
            f"{updated_short}"
        )
    print(f"{'─' * 90}")
    print(f"Total: {len(registry)} file(s)\n")


def cmd_stop():
    cwd      = Path.cwd()
    projects = load_projects()
    key      = str(cwd)

    if key not in projects:
        print(f"Remi is not watching {cwd.name}. Run 'remi init' first.")
        return

    projects[key]["active"] = False
    save_projects(projects)
    name = projects[key].get("name", cwd.name)
    print(f"⏹  Stopped watching {name}.")
    print("   Restart the watcher for this to take effect.")


def cmd_help():
    print("""
🐀 Remi — silent collaborative coding agent

Commands:
  remi init      Initialise Remi in the current project folder
  remi status    Show all watched projects and their status
  remi log       Print the last 20 conflict log entries for this project
  remi registry  Show the intent registry for this project
  remi stop      Stop watching the current project
  remi help      Show this help message
""")


COMMANDS = {
    "init":     cmd_init,
    "status":   cmd_status,
    "log":      cmd_log,
    "registry": cmd_registry,
    "stop":     cmd_stop,
    "help":     cmd_help,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        cmd_help()
        return
    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
