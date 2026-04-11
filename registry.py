#!/usr/bin/env python3
"""
registry.py — Show the full intent map for the Remi project.
Run: python3 registry.py
"""

import json
import sys
import requests
from pathlib import Path
from datetime import datetime

CONFIG_PATH = Path.home() / ".collab-agent" / "config.json"


def load_config():
    if not CONFIG_PATH.exists():
        print("❌ Config not found. Please run install.py first.")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def main():
    config  = load_config()
    server  = config["server_url"]
    room_id = config["room_id"]

    try:
        r = requests.get(f"{server}/intent/registry", params={"room_id": room_id}, timeout=10)
        r.raise_for_status()
        registry = r.json()
    except Exception as e:
        print(f"❌ Could not fetch registry: {e}")
        sys.exit(1)

    if not registry:
        print("📭 Intent registry is empty. Make some changes and enter intent to populate it.")
        return

    print(f"\n📋 Intent Registry — Room: {room_id}")
    print(f"{'─' * 90}")
    print(f"{'File':<35} {'Owner':<12} {'Intent':<30} {'Updated'}")
    print(f"{'─' * 90}")

    for file_path, info in sorted(registry.items()):
        updated = info.get("updated", "")
        try:
            dt = datetime.fromisoformat(updated)
            updated_short = dt.strftime("%m/%d %H:%M")
        except Exception:
            updated_short = updated[:16]

        file_short   = file_path[:34]
        developer    = info.get("developer", "")[:11]
        intent_short = info.get("intent", "")[:29]

        print(f"{file_short:<35} {developer:<12} {intent_short:<30} {updated_short}")

    print(f"{'─' * 90}")
    print(f"Total: {len(registry)} file(s)\n")


if __name__ == "__main__":
    main()
