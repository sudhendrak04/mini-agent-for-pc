"""Mini Jarvis - Phase 7.7: optional rolling activity log.

When memory_log_activity is on, every successful tool execution is
appended as one timestamped line to Activity/<YYYY-MM-DD>.md inside the
vault and indexed with type: activity, so ASK can answer "what did I
open earlier" or "what was I doing this afternoon" without Nitro ever
saying "remember this".

Off by default (plan section 7.7): a noisy activity log competes with
real notes in keyword search until the core save/recall/reminder loop
is proven. Turn it on in config.json once daily use feels solid.
"""

from mini_jarvis import config, events
from mini_jarvis.memory import store


def append(description: str) -> None:
    """One line for one successful tool run; skipped when disabled."""
    cfg = config.load()
    if not cfg.memory_log_activity or not store.vault_configured():
        return
    try:
        store.log_activity_line(description)
    except (store.MemoryNotConfigured, OSError, ValueError) as error:
        events.emit("activity_notice", message=f"log append failed: {error}")


def install() -> None:
    """Subscribe to the event stream; call once at orchestrator start."""
    events.subscribe(_on_event)


def _on_event(event: str, fields: dict) -> None:
    if event == "tool_run":
        append(fields["description"])
