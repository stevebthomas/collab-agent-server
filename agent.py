import os
import json
from datetime import datetime
from anthropic import Anthropic

client = Anthropic()
LOG_FILE = "agent_log.md"
MEMORY_FILE = "agent_memory.json"


def read_log() -> str:
    """Read the existing agent log for context."""
    if not os.path.exists(LOG_FILE):
        return "No previous history."
    with open(LOG_FILE, "r") as f:
        content = f.read()
    return content if content.strip() else "No previous history."


def read_memory() -> dict:
    """Read the agent's compressed memory/patterns."""
    if not os.path.exists(MEMORY_FILE):
        return {"patterns": [], "ownership": {}, "summary": "No memory yet."}
    with open(MEMORY_FILE, "r") as f:
        return json.load(f)


def write_memory(memory: dict):
    """Save updated memory back to file."""
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)


def append_to_log(entry: str):
    """Append a new entry to the markdown log."""
    with open(LOG_FILE, "a") as f:
        f.write(entry + "\n\n")


def analyze_and_resolve(dev_a: dict, dev_b: dict) -> dict:
    """
    Core agent function. Takes two developer pushes and returns
    a conflict analysis + resolution.

    Each push is a dict with:
      - developer: name of the developer
      - intent: plain english description of what they were trying to do
      - code: the actual code they wrote
      - file: which file they were working on
    """

    log_history = read_log()
    memory = read_memory()

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
  "confidence": "high, medium, or low"
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

    write_memory(memory)

    return result


def format_log_entry(dev_a: dict, dev_b: dict, result: dict) -> str:
    """Format the resolution as a markdown log entry."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conflict_emoji = "⚠️" if result["conflict_detected"] else "✅"

    entry = f"""---

## {conflict_emoji} {timestamp}

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

    return entry


def run_agent(dev_a: dict, dev_b: dict):
    """Main entry point. Run the agent on two pushes."""
    print(f"\n🤖 Agent running...")
    print(f"   Analyzing push from {dev_a['developer']} and {dev_b['developer']}...")

    result = analyze_and_resolve(dev_a, dev_b)

    log_entry = format_log_entry(dev_a, dev_b, result)
    append_to_log(log_entry)

    # Print summary to terminal
    conflict_status = "⚠️  CONFLICT DETECTED" if result["conflict_detected"] else "✅  NO CONFLICT"
    print(f"\n{conflict_status}")
    print(f"   {result['conflict_description']}")
    print(f"\n📝 Resolution: {result['resolution']}")
    print(f"\n📁 Log updated: {LOG_FILE}")

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
