---

## ⚠️ 2026-04-10 15:51:31

**File:** `game/buildings/doors.py`
**Confidence:** high

### Developers
- **Alex** — Alex wanted to add atmospheric audio feedback specifically for the bar/saloon building, playing a creaky door sound when players enter
- **Jordan** — Jordan wanted to add atmospheric audio for the library building with a quiet chime, plus add analytics/tracking by logging player visits to buildings

### Conflict Detected
Both developers modified the same function (on_door_enter) to play different sounds, but each hardcoded a sound specific to one building type. Alex's code always plays saloon_creak.wav, while Jordan's always plays library_chime.wav. Neither implementation accounts for different building types, so they would overwrite each other and only one building's audio would work correctly.

### Resolution
Merged both intents by making the door sound dynamic based on building type. Created a sound mapping dictionary that routes each building to its appropriate sound effect. Preserved Jordan's logging functionality since it's a general feature that applies to all buildings, not just the library. This architecture also makes it easy for the team to add more building-specific sounds in the future.

### Merged Code
```python
DOOR_SOUNDS = {
    "bar": "saloon_creak.wav",
    "saloon": "saloon_creak.wav",
    "library": "library_chime.wav",
}

DEFAULT_DOOR_SOUND = "door_open.wav"

def on_door_enter(player, door):
    building_type = getattr(door, 'building_type', None) or getattr(door.building, 'type', 'default')
    sound = DOOR_SOUNDS.get(building_type, DEFAULT_DOOR_SOUND)
    play_sound(sound)
    door.open()
    player.enter()
    log_visit(player, door.building)
```

### Pattern Noted
_When multiple developers add building-specific behaviors to a shared handler, prefer a data-driven approach (dictionary mapping) over hardcoded conditionals. This prevents future conflicts and makes the system extensible._

### Ownership Update
`door_audio_system` → **Alex**

### Ownership Update
`visit_logging_system` → **Jordan**


