"""Mini Jarvis - orchestrator-facing wrapper for the STT feed.

The orchestrator only ever talks to "an object with an .events queue of
(seq, text) pairs and start()/stop()" (Phase 9.1). SttFeed gives the
mic pipeline in stt/feed.py exactly that shape, so swapping the input
source is a one-line change in the orchestrator - Handy/clipboard stays
installed as the fallback in between (test order 9.4.3).
"""

from mini_jarvis.stt.feed import TranscriptSource


class SttFeed:
    """Drop-in TranscriptFeed replacement backed by the local microphone."""

    def __init__(self) -> None:
        self._source = TranscriptSource()
        self.events = self._source.events

    def start(self) -> bool:
        return self._source.start()

    def stop(self) -> None:
        self._source.stop()
