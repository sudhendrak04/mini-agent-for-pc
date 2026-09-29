"""Live mic test wrapper: runs the STT feed and records every event to
transcript.log (opened/closed per line so it can be read while running)."""

import time
from pathlib import Path

from mini_jarvis import events
from mini_jarvis.stt.feed import TranscriptSource

LOG = Path(__file__).with_name("transcript.log")


def write(line: str) -> None:
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def on_event(event: str, fields: dict) -> None:
    if event == "stt_status":
        write(f"[STATUS] {fields['message']}")
    elif event == "stt_transcript":
        write(
            f"[TRANSCRIPT] ({fields['seconds']}s audio, {fields['latency']}s) "
            f"{fields['text']}"
        )


events.subscribe(on_event)
write("=== live mic test started ===")

source = TranscriptSource()
if not source.start():
    write("=== feed failed to start ===")
    raise SystemExit(1)

try:
    while True:
        time.sleep(0.5)
except KeyboardInterrupt:
    source.stop()
