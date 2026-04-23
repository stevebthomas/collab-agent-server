import os
import json
import requests
from datetime import datetime
from anthropic import Anthropic
from mapper import get_connected_files, read_connected_content, build_map, save_map, load_map, should_rebuild

client = Anthropic()


def _log_path(project_path: str) -> str:
    return os.path.join(project_path, "remi_log.md")


def _memory_path(project_path: str) -> str:
    return os.path.join(project_path, "remi_memory.json")


def read_log(project_path: str = ".") -> str:
    """Read the existing agent log for context."""
    path = _log_path(project_path)
    if not os.path.exists(path):
        return "No previous history."
    with open(path, "r") as f:
        content = f.read()
    return content if content.strip() else "No previous history."


def read_memory(project_path: str = ".") -> dict:
    """Read the agent's compressed memory/patterns."""
    path = _memory_path(project_path)
    if not os.path.exists(path):
        return {"patterns": [], "ownership": {}, "summary": "No memory yet."}
    with open(path, "r") as f:
        return json.load(f)


def write_memory(memory: dict, project_path: str = "."):
    """Save updated memory back to file."""
    with open(_memory_path(project_path), "w") as f:
        json.dump(memory, f, indent=2)


def append_to_log(entry: str, project_path: str = "."):
    """Append a new entry to the markdown log."""
    with open(_log_path(project_path), "a") as f:
        f.write(entry + "\n\n")


def infer_intent(file_path: str, content: str) -> str:
    """Ask Claude to infer what a file does from its content."""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{
                "role": "user",
                "content": (
                    f"In one concise sentence, describe what this file does.\n\n"
                    f"File: {file_path}\n\n{content[:3000]}"
                )
            }]
        )
        return response.content[0].text.strip()
    except Exception:
        return ""


def fetch_intent_registry(server_url: str, room_id: str) -> dict:
    """Fetch the full intent registry from the sync server."""
    try:
        r = requests.get(f"{server_url}/intent/registry", params={"room_id": room_id}, timeout=5)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def analyze_and_resolve(dev_a: dict, dev_b: dict, codebase_context: str = "", config: dict = None) -> dict:
    """
    Core agent function. Takes two developer pushes and returns
    a conflict analysis + resolution.

    Each push is a dict with:
      - developer: name of the developer
      - intent: plain english description of what they were trying to do
      - code: the actual code they wrote
      - file: which file they were working on
    """

    project_path = config.get("project_path", ".") if config else "."
    log_history  = read_log(project_path)
    memory       = read_memory(project_path)

    intent_registry = {}
    if config:
        intent_registry = fetch_intent_registry(
            config.get("server_url", ""),
            config.get("room_id", "")
        )

    system_prompt = f"""You are a collaborative coding agent for a small game development team (3-4 developers).
Your job is to:
1. Understand the INTENT behind each developer's code changes
2. Detect any logical or behavioral conflicts between them (not just syntax conflicts)
3. Auto-resolve by producing a merged version that honors both intents
4. Update the ownership map if needed (who is responsible for which systems)
5. Identify any patterns you notice for future reference

You have access to the full history of previous changes and your accumulated memory.

PREVIOUS CHANGE LOG:
{log_history}

ACCUMULATED MEMORY & PATTERNS:
{json.dumps(memory, indent=2)}

CONNECTED FILES IN CODEBASE:
{codebase_context if codebase_context else "No connected file context available."}

PROJECT INTENT REGISTRY — what every file is for:
{json.dumps(intent_registry, indent=2) if intent_registry else "No registry data available."}

Respond ONLY in this exact JSON format, no markdown, no extra text:
{{
  "conflict_detected": true or false,
  "conflict_description": "plain english explanation of what conflicted and why",
  "developer_a_intent": "what developer A was actually trying to do",
  "developer_b_intent": "what developer B was actually trying to do",
  "resolution": "plain english explanation of how you resolved it",
  "merged_code": "the full resolved code as a string",
  "affected_file": "which file this applies to",
  "ownership_update": {{"system_name": "developer_name"}},
  "new_pattern": "any pattern you noticed worth remembering, or null",
  "confidence": "high, medium, or low",
  "cross_file_risks": "comma-separated list of other files that may be affected by this change, or null"
}}"""

    user_message = f"""Two developers just pushed changes. Analyze and resolve.

DEVELOPER A — {dev_a['developer']}
File: {dev_a['file']}
Intent: {dev_a['intent']}
Code:
{dev_a['code']}

---

DEVELOPER B — {dev_b['developer']}
File: {dev_b['file']}
Intent: {dev_b['intent']}
Code:
{dev_b['code']}"""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    result = json.loads(raw)

    # Update memory with any new patterns or ownership
    if result.get("ownership_update"):
        memory["ownership"].update(result["ownership_update"])

    if result.get("new_pattern"):
        memory["patterns"].append({
            "pattern": result["new_pattern"],
            "date": datetime.now().isoformat()
        })

    write_memory(memory, project_path)

    return result


def format_log_entry(dev_a: dict, dev_b: dict, result: dict) -> str:
    """Format the resolution as a markdown log entry."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conflict_emoji = "⚠️" if result["conflict_detected"] else "✅"

    entry = f"""---

## {conflict_emoji} Remi — {timestamp}

**File:** `{result['affected_file']}`
**Confidence:** {result['confidence']}

### Developers
- **{dev_a['developer']}** — {result['developer_a_intent']}
- **{dev_b['developer']}** — {result['developer_b_intent']}

### Conflict Detected
{result['conflict_description']}

### Resolution
{result['resolution']}

### Merged Code
```python
{result['merged_code']}
```
"""

    if result.get("new_pattern"):
        entry += f"\n### Pattern Noted\n_{result['new_pattern']}_\n"

    if result.get("ownership_update"):
        for system, owner in result["ownership_update"].items():
            entry += f"\n### Ownership Update\n`{system}` → **{owner}**\n"

    if result.get("cross_file_risks"):
        entry += f"\n### Cross-file Risks\n{result['cross_file_risks']}\n"

    return entry


def run_agent(dev_a: dict, dev_b: dict, codebase_context: str = "", config: dict = None):
    """Main entry point. Run the agent on two pushes."""
    project_path = config.get("project_path", ".") if config else "."

    # Defense-in-depth: skip Opus entirely when both payloads are byte-identical
    if dev_a.get("code", "") == dev_b.get("code", ""):
        filename = dev_a.get("file", "unknown")
        append_to_log(
            f"✅ No-op sync: {filename} (identical content from both devs, skipped inference)",
            project_path
        )
        print(f"⏭️  Remi: no-op sync — {filename} identical content, skipping inference")
        return {
            "no_op":                True,
            "conflict_detected":    False,
            "conflict_description": "Identical content from both developers — no merge needed.",
            "developer_a_intent":   dev_a.get("intent", ""),
            "developer_b_intent":   dev_b.get("intent", ""),
            "resolution":           "No-op: content was identical.",
            "merged_code":          dev_a["code"],
            "affected_file":        dev_a.get("file", ""),
            "ownership_update":     {},
            "new_pattern":          None,
            "confidence":           "high",
            "cross_file_risks":     None,
        }

    print(f"\n🐀 Remi running...")
    print(f"   Analyzing push from {dev_a['developer']} and {dev_b['developer']}...")

    result = analyze_and_resolve(dev_a, dev_b, codebase_context, config=config)

    log_entry = format_log_entry(dev_a, dev_b, result)
    append_to_log(log_entry, project_path)

    # Print summary to terminal
    conflict_status = "⚠️  CONFLICT DETECTED" if result["conflict_detected"] else "✅  NO CONFLICT"
    print(f"\n{conflict_status}")
    print(f"   {result['conflict_description']}")
    print(f"\n📝 Resolution: {result['resolution']}")
    print(f"\n📁 Log updated: {_log_path(project_path)}")

    if result.get("new_pattern"):
        print(f"\n🧠 Pattern learned: {result['new_pattern']}")

    return result


# ─────────────────────────────────────────────
# TEST SCENARIO — the bar door sound example
# ─────────────────────────────────────────────
if __name__ == "__main__":

    # Developer A writes a sound trigger for the bar — but implements it too broadly
    dev_a = {
        "developer": "Alex",
        "file": "game/buildings/doors.py",
        "intent": "Play a creaky saloon sound when the player enters the bar",
        "code": """
def on_door_enter(player, door):
    play_sound("saloon_creak.wav")
    door.open()
    player.enter()
"""
    }

    # Developer B writes a door system for the library with its own sound logic
    dev_b = {
        "developer": "Jordan",
        "file": "game/buildings/doors.py",
        "intent": "Play a quiet library chime when the player enters the library, and log the visit",
        "code": """
def on_door_enter(player, door):
    play_sound("library_chime.wav")
    door.open()
    player.enter()
    log_visit(player, door.building)
"""
    }

    run_agent(dev_a, dev_b)
