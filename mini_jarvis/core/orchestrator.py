"""Mini Jarvis - orchestrator loop.

Microphone (Silero VAD + faster-whisper) -> intent classification (rules +
Laya) -> safety gate -> tool execution (apps, browser, screenshot, volume,
files, typing). Compound phrasings and COMPLEX_TASK go to the local Ollama
planner; every plan step runs through the same registry and safety gates.

State changes go through the events stream (mini_jarvis/events.py)
instead of prints, so later phases can observe the pipeline without
touching this loop.
"""

import queue

from mini_jarvis import config, events
from mini_jarvis.core.orb_broadcaster import OrbBroadcaster
from mini_jarvis.core.safety import NO_WORDS, YES_WORDS, STT_ANSWER_CHANNEL
from mini_jarvis.io.stt_feed import SttFeed
from mini_jarvis.memory import activity, reminders
from mini_jarvis.nlu import slot_filler
from mini_jarvis.nlu.classifier import LayaClassifier
from mini_jarvis.nlu.slots import (
    extract_slots,
    has_command_connector,
    normalize_command,
    split_commands,
)
from mini_jarvis.tools import registry


def main() -> None:
    cfg = config.load()
    classifier = LayaClassifier(threshold=cfg.confidence_threshold)
    classifier.start()

    # The microphone is the only input now (Handy/clipboard is out of the
    # pipeline). The same transcript queue feeds the safety gate, so a
    # spoken "yes" answers a pending confirmation.
    feed = SttFeed()
    STT_ANSWER_CHANNEL.bind(feed)
    if not feed.start():
        events.emit("assistant_unavailable", reason="microphone feed did not start")
        return

    reminders.start_scheduler()

    if cfg.memory_log_activity and cfg.memory_vault_path:
        activity.install()

    # Phase 9: the orb is a second subscriber to the event stream; the
    # broadcaster only translates events, it never drives the pipeline.
    OrbBroadcaster().start()

    events.emit("assistant_ready", threshold=cfg.confidence_threshold)

    try:
        while True:
            # The feed keeps transcribing while tools run, so a slow tool
            # never swallows a command spoken in the meantime.
            try:
                _seq, transcript = feed.events.get(timeout=1.0)
            except queue.Empty:
                continue

            events.emit("transcript_received", text=transcript)
            answer = normalize_command(transcript).strip(" .!?")
            if answer in (YES_WORDS | NO_WORDS):
                # A yes/no that no confirmation asked for - ignore it so it
                # is never misread as a command.
                events.emit("stray_answer")
                events.emit("assistant_idle")
                continue

            last_launched = None  # app launched by a previous sub-command
            commands = split_commands(transcript)
            if len(commands) == 1 and has_command_connector(transcript):
                # Compound phrasing the splitter could not decompose.
                # A single confident intent cannot cover a multi-clause
                # sentence - the LLM planner decides instead.
                events.emit("compound_detected", command=commands[0])
                registry.execute("COMPLEX_TASK", {}, commands[0])
                events.emit("assistant_idle")
                continue

            for command in commands:
                intent, confidence, source = classifier.classify(command)
                slots = extract_slots(intent, command)
                if slot_filler.needs_fill(intent, slots, source):
                    # Laya classified this, but the regex pass could not
                    # get a required slot out of the phrasing - one small
                    # LLM call scoped to this intent's schema fills it.
                    slots = slot_filler.fill(intent, command, slots)

                # Context threading inside compounds: "open notepad and
                # type hello world" - the type step inherits the app the
                # previous sub-command just launched.
                if (
                    intent == "TYPE_TEXT"
                    and "where" not in slots
                    and last_launched
                ):
                    slots["where"] = last_launched
                    events.emit("context_inherited", app=last_launched)

                events.emit(
                    "intent_classified",
                    intent=intent,
                    confidence=confidence,
                    source=source,
                    slots=slots,
                )
                registry.execute(intent, slots, command)
                if intent == "OPEN_APPLICATION" and slots.get("app"):
                    last_launched = slots["app"]
            events.emit("assistant_idle")
    except KeyboardInterrupt:
        events.emit("assistant_stopped")
