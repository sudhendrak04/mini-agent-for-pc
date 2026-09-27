"""Mini Jarvis - Phase 7: memory tools the registry calls.

Thin adapters between intent_schema (MEMORY_SAVE, REMINDER_SET, ASK) and
the memory package. All three are Level.SAFE: read/append-only and
reversible - a bad reminder is just a stray note.
"""

from mini_jarvis.memory import recall, reminders, store
from mini_jarvis.tools import ToolFailure


def save_memory(content: str) -> str:
    if not store.vault_configured():
        raise ToolFailure("memory is not set up - set memory_vault_path in config.json")
    path = store.save(content, note_type="note")
    return f"saved to memory ({path.name})"


def set_reminder(text: str, when: str) -> str:
    if not store.vault_configured():
        raise ToolFailure("memory is not set up - set memory_vault_path in config.json")
    return reminders.set_reminder(text, when)


def ask(query: str) -> str:
    if not store.vault_configured():
        raise ToolFailure("memory is not set up - set memory_vault_path in config.json")
    return recall.answer(query)
