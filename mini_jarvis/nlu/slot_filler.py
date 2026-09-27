"""Mini Jarvis - Phase 7.9a: LLM slot extraction fallback.

Regex slot extraction only catches the phrasings it was written for
("remind me to X at Y"), while Laya classifies far more phrasings
correctly ("don't let me forget to submit the assignment before tomorrow
noon" is REMINDER_SET but no regex extracts its slots). When the model
path produced a confident intent and the regex pass still leaves a
required slot empty, one small Ollama call - scoped to that single
intent's schema from intent_schema.py, same call shape as
planner._chat - extracts the fields as JSON.

This only fires on cases the regex already failed, so the fast path
stays exactly as fast as before; the rule path never reaches here.
"""

import json
import re
import urllib.request

from mini_jarvis import config, events
from mini_jarvis.intent_schema import INTENTS

OLLAMA_CHAT = "http://localhost:11434/api/chat"
REQUEST_TIMEOUT = 60.0
MAX_FILL_TOKENS = 120

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def missing_required(intent: str, slots: dict) -> list[str]:
    """Required slot keys still empty after the regex pass."""
    spec = INTENTS.get(intent)
    if spec is None or not spec.required:
        return []
    return [key for key in spec.required if not slots.get(key)]


def needs_fill(intent: str, slots: dict, source: str) -> bool:
    """Fill only on the model path - the rules already own their slots."""
    return source == "laya" and bool(missing_required(intent, slots))


def _prompt(intent: str) -> str:
    spec = INTENTS[intent]
    fields = ", ".join((*spec.required, *spec.optional))
    return (
        "You extract fields from a voice sentence for a desktop assistant.\n"
        f"Intent: {intent}\n"
        f"Fields: {fields}\n"
        f"Shape: {spec.planner_slot_hint}\n"
        "Rules:\n"
        "- Output ONLY JSON with those fields.\n"
        "- Copy values verbatim from the sentence.\n"
        "- Omit a field if the sentence does not contain it.\n"
        "- No explanations, no markdown."
    )


def _chat(intent: str, transcript: str) -> str:
    """One JSON answer from Ollama. Raises on network errors."""
    payload = {
        "model": config.load().planner_model,
        "messages": [
            {"role": "system", "content": _prompt(intent)},
            {"role": "user", "content": f'Sentence: "{transcript}"'},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": MAX_FILL_TOKENS},
    }
    request = urllib.request.Request(
        OLLAMA_CHAT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        data = json.loads(response.read())
    return data.get("message", {}).get("content", "")


def _parse_fields(content: str, intent: str) -> dict:
    """Validate the model's JSON against the intent's schema."""
    spec = INTENTS[intent]
    text = _CODE_FENCE_RE.sub("", content).strip()
    try:
        reply = json.loads(text)
    except ValueError:
        raise ValueError("model returned invalid JSON") from None
    if not isinstance(reply, dict):
        raise ValueError("model JSON was not an object")
    allowed = (*spec.required, *spec.optional)
    fields = {}
    for key, value in reply.items():
        if key in allowed and value is not None and str(value).strip():
            fields[key] = str(value).strip()
    return fields


def fill(intent: str, transcript: str, slots: dict) -> dict:
    """One Ollama call to extract the intent's slots, merged over slots.

    Regex-extracted slots win; the model only fills what is missing. On
    any failure the original slots are returned and the registry reports
    the missing slots as before.
    """
    if intent not in INTENTS or intent == "UNKNOWN":
        return slots
    events.emit("slot_fill_started", intent=intent)
    try:
        content = _chat(intent, transcript)
        fields = _parse_fields(content, intent)
    except (OSError, ValueError) as error:
        events.emit("slot_fill_failed", intent=intent, error=str(error))
        return slots
    merged = dict(slots)
    for key, value in fields.items():
        merged.setdefault(key, value)
    events.emit("slot_filled", intent=intent, slots=merged)
    return merged
