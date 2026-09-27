"""Mini Jarvis - Phase 6: Ollama planner for complex commands.

When Laya routes a transcript to COMPLEX_TASK, the planner asks a local
Ollama model to turn it into a small JSON plan of registry steps. The LLM
never executes anything itself and never invents tools - it only
nominates (intent, slots) pairs, which are validated here and then run
through the same registry and safety gates as every other command.

The allow-list and the prompt's slot section are derived from
intent_schema.py, so new intents (Phases 7-8) appear to the planner
automatically. Model choice comes from config.json ("planner_model" -
llama3:latest won the phase-6 benchmark: 6/6 valid JSON, fastest warm
latency).
"""

import json
import re
import urllib.request

from mini_jarvis import config, events
from mini_jarvis.intent_schema import INTENTS
from mini_jarvis.nlu.slots import DEFAULT_VOLUME_STEP

OLLAMA_CHAT = "http://localhost:11434/api/chat"
REQUEST_TIMEOUT = 90.0

# The planner may only nominate these intents and slot keys - anything
# else is dropped, so the LLM can never reach code outside the registry.
ALLOWED_INTENTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    name: (spec.required, spec.optional)
    for name, spec in INTENTS.items()
    if spec.tool is not None
}

_SLOT_LINES = "\n".join(
    f"- {name}: {spec.planner_slot_hint}"
    for name, spec in INTENTS.items()
    if spec.planner_slot_hint
)

_PROMPT_TEMPLATE = """You are the planning unit of a local Windows voice assistant.
Convert the user's voice command into a JSON plan for execution.

Allowed step intents and their slots:
@@SLOT_LINES@@

Rules:
- Output ONLY JSON: {"steps": [...]} with 1-5 steps in execution order.
- Order steps exactly as the user said them ("before X, do Y" -> X last).
- Use ONLY the allowed intents. Never invent tools (no shutdown).
- If the command cannot be done with these tools, output {"steps": []}.
- Keep slot values short and literal. No explanations, no markdown.

Examples:
Command: "open chrome and set volume to 30"
{"steps":[{"intent":"OPEN_APPLICATION","slots":{"app":"chrome"}},{"intent":"VOLUME_CONTROL","slots":{"direction":"set","amount":30}}]}
Command: "play despacito"
{"steps":[{"intent":"YOUTUBE_SEARCH","slots":{"query":"despacito"}}]}
Command: "put hello world into a fresh notepad"
{"steps":[{"intent":"TYPE_TEXT","slots":{"text":"hello world","where":"notepad"}}]}
Command: "before you open spotify could you make the volume quieter and grab a screenshot of my screen"
{"steps":[{"intent":"VOLUME_CONTROL","slots":{"direction":"down","amount":10}},{"intent":"SCREENSHOT","slots":{}},{"intent":"OPEN_APPLICATION","slots":{"app":"spotify"}}]}
Command: "delete my files"
{"steps":[]}

Notes:
- "put/write/type <text> into/onto/in <app>" is ALWAYS TYPE_TEXT with the app as "where" - never FILE_OPERATION.
- FILE_OPERATION is only for locating files on disk ("find my taxes file").
- DELETE_FILE is permanent: nominate it only when the user clearly says delete, with the exact file name; "delete my files" or vague deletions get {"steps": []}.
- MOVE_FILE/COPY_FILE need both the file name and the destination folder.
- CREATE_FILE makes an empty file; keep the extension if the user said one ("text.txt" stays "text.txt").
- Relative volume words: quieter/lower -> down, louder/higher -> up; no number means amount 10."""

SYSTEM_PROMPT = _PROMPT_TEMPLATE.replace("@@SLOT_LINES@@", _SLOT_LINES)


def _chat(transcript: str) -> str:
    """One JSON answer from Ollama. Raises on network errors."""
    payload = {
        "model": config.load().planner_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f'Command: "{transcript}"'},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 500},
    }
    request = urllib.request.Request(
        OLLAMA_CHAT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        data = json.loads(response.read())
    return data.get("message", {}).get("content", "")


def _parse_plan(content: str) -> list:
    """Extract the validated steps list from the model's JSON reply."""
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    text = re.sub(r"</?think>", "", text).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        reply = json.loads(text)
    except ValueError:
        events.emit("plan_notice", message="model returned invalid JSON")
        return []
    if not isinstance(reply, dict) or not isinstance(reply.get("steps"), list):
        return []

    steps: list[dict] = []
    for raw in reply["steps"][:5]:
        if not isinstance(raw, dict):
            continue
        intent = raw.get("intent")
        slots_in = raw.get("slots", {})
        if intent not in ALLOWED_INTENTS or not isinstance(slots_in, dict):
            events.emit("plan_notice", message=f"dropped invalid step: {raw!r}")
            continue
        required, optional = ALLOWED_INTENTS[intent]
        missing = [key for key in required if slots_in.get(key) in (None, "")]
        if missing:
            events.emit(
                "plan_notice", message=f"dropped {intent} step (missing {missing})"
            )
            continue
        slots = {key: slots_in[key] for key in (*required, *optional) if key in slots_in}
        if intent == "VOLUME_CONTROL":
            slots["direction"] = str(slots.get("direction", "")).lower()
            if slots["direction"] not in ("up", "down", "set"):
                events.emit(
                    "plan_notice", message=f"dropped bad volume direction: {slots}"
                )
                continue
            try:
                amount = int(slots["amount"])
            except (TypeError, ValueError):
                amount = DEFAULT_VOLUME_STEP
            if amount <= 0:  # "quieter" sometimes yields 0 - use the default
                amount = DEFAULT_VOLUME_STEP
            slots["amount"] = max(0, min(amount, 100))
        steps.append({"intent": intent, "slots": slots})
    return steps


def plan(transcript: str) -> list[dict]:
    """Turn a complex transcript into validated plan steps ([] if none)."""
    transcript = (transcript or "").strip()
    if not transcript:
        return []
    try:
        ping = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(ping, timeout=5.0) as response:
            json.loads(response.read())
    except (OSError, ValueError) as e:
        events.emit(
            "plan_notice",
            message=f"Ollama is not reachable ({e}) - cannot plan",
        )
        return []

    try:
        content = _chat(transcript)
    except (OSError, ValueError) as e:
        events.emit("plan_notice", message=f"Ollama request failed: {e}")
        return []

    steps = _parse_plan(content)
    if not steps:
        events.emit("plan_notice", message="no usable steps for this command")
    return steps
