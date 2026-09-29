"""Mini Jarvis - minimal event stream.

Every pipeline state change goes through emit() instead of being printed
inline. Subscribers receive (event_name, fields). A console subscriber is
registered at import that reproduces, byte for byte, the lines the
pipeline printed before this abstraction existed - so today's behavior
is unchanged. Later phases (the orb in Phase 9) add a second subscriber
(a local WebSocket broadcaster) without touching any emit() call site.
"""

from typing import Any, Callable

Subscriber = Callable[[str, dict[str, Any]], None]

_subscribers: list[Subscriber] = []


def subscribe(fn: Subscriber) -> None:
    """Register a subscriber; it is called for every emit()."""
    _subscribers.append(fn)


def emit(event: str, **fields: Any) -> None:
    """Publish one event to every subscriber."""
    for fn in tuple(_subscribers):
        fn(event, fields)


# ---------------------------------------------------------------------------
# Console presenter: the exact strings the pipeline printed before.
# Events with no formatter return None and print nothing, so richer events
# can be added later without changing console behavior.


def _format(event: str, f: dict[str, Any]) -> str | None:
    if event == "assistant_ready":
        return (
            f"[JARVIS] listening (confidence threshold {f['threshold']:.2f}). "
            "Ctrl+C to stop."
        )
    if event == "assistant_stopped":
        return "\n[JARVIS] stopped."
    if event == "assistant_unavailable":
        return f"[JARVIS] cannot start: {f['reason']}."
    if event == "transcript_received":
        return f"[TRANSCRIPT] {f['text']}"
    if event == "stray_answer":
        return "[CONFIRM] stray yes/no answer - ignoring"
    if event == "compound_detected":
        return "[COMPLEX] compound phrasing -> LLM planner"
    if event == "context_inherited":
        return f"[CONTEXT] typing target: {f['app']}"
    if event == "intent_classified":
        line = (
            f"[INTENT] {f['intent']} "
            f"(confidence {f['confidence']:.2f}, via {f['source']})"
        )
        if f.get("slots"):
            line += f"  slots={f['slots']}"
        return line
    if event == "laya_status":
        return f"[LAYA] {f['message']}"
    if event == "confirm_requested":
        return (
            f"[CONFIRM:{f['level']}] about to: {f['description']}\n"
            "   say 'yes' or type y/yes - 'no'/silence cancels"
        )
    if event == "confirm_resolved":
        result = f.get("result")
        if result == "approved":
            return "   [CONFIRM] approved"
        if result == "denied":
            return "   [CONFIRM] denied"
        if result == "timeout":
            return "   [CONFIRM] timed out - cancelled"
        if result == "unclear":
            return f"   [CONFIRM] unclear answer ({f['answer']!r}) - cancelled"
        return None
    if event == "unknown_ignored":
        return "[UNKNOWN] not a command - ignoring"
    if event == "plan_unusable":
        return "[PLAN] could not plan this - ignoring"
    if event == "plan_notice":
        return f"[PLAN] {f['message']}"
    if event == "plan_created":
        lines = [f"[PLAN] {len(f['steps'])} step(s) to run:"]
        for index, step in enumerate(f["steps"], 1):
            lines.append(f"[PLAN]   {index}. {step['intent']} {step['slots']}")
        return "\n".join(lines)
    if event == "plan_step_started":
        return f"[PLAN] running step {f['index']}/{f['total']}"
    if event == "plan_step_skipped":
        return f"[PLAN] step {f['index']}/{f['total']} not executable - skipped"
    if event == "tool_missing":
        return f"[TOOL] no tool registered for {f['intent']}"
    if event == "tool_slots_missing":
        return (
            f"[TOOL] could not work out the {'/'.join(f['missing'])} - "
            f"slots were {f['slots']}"
        )
    if event == "tool_cancelled":
        return f"[TOOL] cancelled: {f['description']}"
    if event == "tool_run":
        return f"[TOOL] {f['description']} -> {f['result']}"
    if event == "tool_failed":
        return f"[TOOL] {f['description']} failed: {f['error']}"
    if event == "tool_error":
        return f"[TOOL] {f['description']} unexpected error: {f['error']}"
    if event == "memory_saved":
        return f"[MEMORY] saved {f['path']} ({f['type']})"
    if event == "memory_notice":
        return f"[MEMORY] {f['message']}"
    if event == "recall_tier":
        if f["tier"] == 3:
            return "[RECALL] no memory hit - answering from general knowledge"
        return f"[RECALL] {f['hits']} note(s) via tier {f['tier']} ({f['source']})"
    if event == "reminder_started":
        return f"[REMINDER] scheduler running (checking every {f['poll_seconds']}s)"
    if event == "reminder_notice":
        return f"[REMINDER] {f['message']}"
    if event == "reminder_fired":
        return f"[REMINDER] {f['text']} (due {f['due']})"
    if event == "slot_fill_started":
        return f"[SLOTS] regex missed required slots for {f['intent']} - asking model"
    if event == "slot_filled":
        return f"[SLOTS] model extracted: {f['slots']}"
    if event == "slot_fill_failed":
        return f"[SLOTS] model extraction failed ({f['intent']}): {f['error']}"
    if event == "activity_notice":
        return f"[ACTIVITY] {f['message']}"
    if event == "stt_status":
        return f"[STT] {f['message']}"
    if event == "stt_transcript":
        return f"[STT] ({f['seconds']}s audio, {f['latency']}s) {f['text']}"
    return None


def _console_printer(event: str, fields: dict[str, Any]) -> None:
    line = _format(event, fields)
    if line is not None:
        print(line, flush=True)


subscribe(_console_printer)
