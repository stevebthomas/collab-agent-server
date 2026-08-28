# The Vibecoding Collaboration Problem

## What is vibecoding?

Vibecoding refers to development workflows where AI assistants (Cursor, Claude Code, GitHub Copilot, etc.) generate large chunks of code in response to high-level prompts. The developer guides the AI, accepts or rejects suggestions, and iterates, but may not deeply understand every line produced.

This is increasingly common. It's fast, it unlocks developers who previously couldn't build certain things, and it produces real, working software.

## The new collaboration failure mode

Traditional conflict detection (git) was designed for humans who write code they understand, line by line. Git catches *syntactic* conflicts: two people edited the same line, so git flags it.

Vibecoding creates a new category of problem that git misses entirely: **semantic intent conflicts**.

**Scenario:**
Two developers are building a game simultaneously, both using AI assistants.

- Developer A's AI restructures the physics system to be event-driven, decoupling collision detection from the game loop. The AI touches `physics.py`, `game_loop.py`, and `constants.py`.
- Developer B's AI adds a new enemy type that calls the old synchronous collision interface directly. It touches `enemy_ai.py`, `physics.py`, and `game_loop.py`.

Neither developer fully understands the full scope of what their AI changed. When they push:

- git sees no conflict on `enemy_ai.py` (only B touched it)
- git sees a conflict on `physics.py` (both touched it) and produces a merge
- The merge is syntactically valid: it compiles
- At runtime, the new enemy AI calls a function signature that no longer exists
- The game crashes

**The gap:** git resolved the syntax conflict, but nobody resolved the underlying intent conflict. The right resolution would have been: "B's enemy AI needs to use the new event-driven interface A built."

## Why pre-push matters

Post-push is too late for intent resolution. By the time a PR is opened:
- The AI context that generated the code is gone
- The developer may not remember the intent behind each change
- The conflict has already propagated into shared history

Pre-push, Remi captures intent *at the moment of generation* (when it's still fresh and accurate) and uses it to detect and resolve conflicts before they're committed.

## Why this is hard to solve with existing tools

- **Linters/type checkers:** catch type-level incompatibilities, not design-level ones
- **PR reviews:** depend on a human reviewer understanding both sides of the conflict
- **git merge strategies:** operate on text, not meaning
- **Communication (Slack, standups):** don't scale to the velocity of AI-assisted development

Remi's approach: capture intent continuously, detect conflicts early, resolve with an AI agent that has access to both developers' intent and the full codebase context.
