"""Mini Jarvis - tool registry with the safety gate.

Maps classified intents to plain Python functions (the tool of each
intent comes from intent_schema.py) and executes them, emitting one
tool event per action. Every call passes through the safety layer first
(safety.level_for); SENSITIVE and DANGEROUS tools require an explicit
spoken-or-typed "yes".

COMPLEX_TASK transcripts go to the Ollama planner (planner.py), whose
steps are executed right back through this same registry and gate.

Tools raise ToolFailure on problems; the registry reports the failure
and keeps running.
"""

from mini_jarvis import events
from mini_jarvis.core.safety import Level, confirm, level_for
from mini_jarvis.intent_schema import INTENTS
from mini_jarvis.nlu.planner import plan as plan_steps
from mini_jarvis.tools import ToolFailure

# intent -> (function, required slots, optional slots)
TOOL_BY_INTENT = {
    name: (spec.tool, spec.required, spec.optional)
    for name, spec in INTENTS.items()
    if spec.tool is not None
}


def execute(intent: str, slots: dict, transcript: str = "") -> None:
    """Run the tool for an intent, or explain why nothing runs.

    transcript is the (sub-)command text; it is only needed for
    COMPLEX_TASK, where the planner turns it into a plan of steps.
    """
    if intent == "UNKNOWN":
        events.emit("unknown_ignored")
        return
    if intent == "COMPLEX_TASK":
        steps = plan_steps(transcript)
        if not steps:
            events.emit("plan_unusable")
            return
        total = len(steps)
        events.emit("plan_created", steps=steps)
        for index, step in enumerate(steps, 1):
            if step["intent"] not in TOOL_BY_INTENT:
                # Belt and braces: the planner already restricts itself,
                # but a step outside the registry is never executed.
                events.emit("plan_step_skipped", index=index, total=total)
                continue
            events.emit(
                "plan_step_started",
                index=index,
                total=total,
                intent=step["intent"],
                slots=step["slots"],
            )
            execute(step["intent"], step["slots"])
        return

    entry = TOOL_BY_INTENT.get(intent)
    if entry is None:
        events.emit("tool_missing", intent=intent)
        return

    func, required, optional = entry
    missing = [key for key in required if not slots.get(key)]
    if missing:
        events.emit("tool_slots_missing", missing=missing, slots=slots)
        return

    spec = INTENTS.get(intent)
    if spec is not None and spec.prepare is not None:
        # File tools resolve "which file did you mean" BEFORE the safety
        # prompt: the confirmation names the exact path, and ambiguity is
        # refused instead of asking approval for something that cannot run.
        try:
            slots = spec.prepare(dict(slots))
        except ToolFailure as failure:
            events.emit("tool_failed", description=intent + " slot resolution", error=failure)
            return
        except Exception as unexpected:
            events.emit("tool_error", description=intent + " slot resolution", error=unexpected)
            return

    args = [slots[key] for key in required]
    kwargs = {key: slots[key] for key in optional if slots.get(key)}
    description = func.__name__ + "(" + ", ".join(repr(a) for a in args)
    if kwargs:
        description += ", " + ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
    description += ")"

    # Safety gate: SAFE runs now, everything else needs an explicit yes.
    level = level_for(func.__name__)
    if level is not Level.SAFE:
        approved = confirm(description, level)
        if not approved:
            events.emit(
                "tool_cancelled", tool=func.__name__, description=description
            )
            return

    try:
        result = func(*args, **kwargs)
        events.emit("tool_run", tool=func.__name__, description=description, result=result)
    except ToolFailure as failure:
        events.emit(
            "tool_failed", tool=func.__name__, description=description, error=failure
        )
    except Exception as unexpected:  # a broken tool must never kill the loop
        events.emit(
            "tool_error",
            tool=func.__name__,
            description=description,
            error=unexpected,
        )
