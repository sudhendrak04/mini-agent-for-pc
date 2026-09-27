"""Mini Jarvis - clipboard bridge.

Polls the Windows clipboard for new text (transcripts that Handy puts
there via its "Copy" post-transcription action) and exposes them to the
orchestrator as a stream.

Dedupe trick: Windows keeps a global clipboard sequence number that
increments on EVERY clipboard write, even when the new text is identical
to the old text. That lets us detect "say 'open chrome' twice in a row"
correctly, without any extra dependency.

Run standalone:  python -m mini_jarvis.io.clipboard_feed [poll_interval]
"""

import ctypes
import queue
import sys
import threading
import time
from typing import Iterator

import pyperclip

POLL_INTERVAL = 0.3  # seconds between clipboard checks

_user32 = ctypes.windll.user32
_user32.GetClipboardSequenceNumber.restype = ctypes.c_ulong

# Clipboard sequence number already consumed elsewhere (e.g. a safety
# confirmation heard "yes" through the clipboard); the stream must not
# yield it as a transcript.
_skip_seq: int | None = None


def mark_sequence_consumed(seq: int) -> None:
    """Prevent transcript_stream from yielding this clipboard event."""
    global _skip_seq
    _skip_seq = seq


def is_skipped(seq: int) -> bool:
    """True (once) if this clipboard event was already consumed by a
    confirmation prompt; clears the marker so later events flow."""
    global _skip_seq
    if seq == _skip_seq:
        _skip_seq = None
        return True
    return False


class TranscriptFeed:
    """Background poller that queues every new clipboard event.

    (seq, text) pairs land in a thread-safe queue, so tools can run for
    as long as they need without the assistant ever missing a transcript
    that lands while a tool is busy.
    """

    def __init__(self, interval: float = POLL_INTERVAL):
        self.interval = interval
        self.events: "queue.Queue[tuple[int, str]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        last_seq = get_clipboard_sequence_number()  # skip stale startup content
        while True:
            time.sleep(self.interval)
            seq = get_clipboard_sequence_number()
            if seq == last_seq:
                continue
            text = read_clipboard_text().strip()
            if not text:
                # Clipboard may be locked by another process right now.
                # Do NOT advance last_seq - retry the same event next poll.
                continue
            last_seq = seq
            self.events.put((seq, text))


def get_clipboard_sequence_number() -> int:
    """Return the Windows clipboard sequence number (increments on every copy)."""
    return int(_user32.GetClipboardSequenceNumber())


def read_clipboard_text() -> str:
    """Return clipboard text, or '' if the clipboard is locked / has no text."""
    try:
        text = pyperclip.paste()
    except pyperclip.PyperclipException:
        # Another process may be holding the clipboard open, or the content
        # may be non-text (e.g. an image). Nothing we can do - skip it.
        return ""
    return text if isinstance(text, str) else ""


def transcript_stream(interval: float = POLL_INTERVAL) -> Iterator[str]:
    """Yield one string per new clipboard write, forever.

    The clipboard content that already exists at startup is stale and is
    skipped. Repeats of identical text are still yielded, because we
    detect events by sequence number rather than by content.
    """
    global _skip_seq
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    last_seq = get_clipboard_sequence_number()
    while True:
        time.sleep(interval)
        seq = get_clipboard_sequence_number()
        if seq == last_seq:
            continue
        text = read_clipboard_text().strip()
        if not text:
            # clipboard busy with another process - retry the same event
            continue
        last_seq = seq
        if seq == _skip_seq:  # already consumed by a confirmation prompt
            _skip_seq = None
            continue
        yield text


def poll_forever(interval: float = POLL_INTERVAL) -> None:
    """Standalone behaviour: print every new transcript to stdout, forever."""
    print(
        f"[BRIDGE] watching clipboard (poll every {interval}s). Ctrl+C to stop.",
        flush=True,
    )
    try:
        for text in transcript_stream(interval):
            print(f"[TRANSCRIPT] {text}", flush=True)
    except KeyboardInterrupt:
        print("\n[BRIDGE] stopped.", flush=True)


if __name__ == "__main__":
    from mini_jarvis import config

    interval = config.load().poll_interval
    if len(sys.argv) > 1:
        interval = float(sys.argv[1])
    poll_forever(interval)
