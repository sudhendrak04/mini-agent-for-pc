"""Mini Jarvis - Phase 4: safety layer.

Every tool call is classified before execution:

    SAFE       execute immediately
    SENSITIVE  ask for a confirmation first
    DANGEROUS  always require explicit confirmation - no exceptions, and
               there is deliberately NO config option to disable it.

The level of each tool comes from the unified intent schema
(intent_schema.py, keyed by tool name). Unknown tools default to
SENSITIVE: a tool that forgets to declare its level errs on the side of
asking.

A "yes" can be spoken (SttAnswerChannel - a transcript that is a yes/no
word answers the pending confirmation instead of becoming a command) or
typed in the console. Anything else, or silence within the timeout,
cancels the action.
"""

import queue
import threading
import time
from typing import Protocol

from mini_jarvis import events
from mini_jarvis.intent_schema import INTENTS, Level
from mini_jarvis.nlu.slots import normalize_command

YES_WORDS = {
    "yes", "y", "yeah", "yep", "sure", "confirm", "confirmed",
    "do it", "ok", "okay", "please do", "go ahead", "proceed", "approve",
}
NO_WORDS = {
    "no", "n", "nope", "cancel", "stop", "abort", "deny", "deny it",
    "don't", "dont", "do not", "never mind", "nevermind",
}

# Every wired intent's tool level, straight from the schema...
_LEVELS_FROM_SCHEMA = {
    spec.tool.__name__: spec.level
    for spec in INTENTS.values()
    if spec.tool is not None and spec.level is not None
}

# ...plus tools declared ahead of their intent (wired in Phase 8) and the
# pre-adapter function names, so the policy stays reviewable in one place.
_LEVELS_DECLARED_AHEAD = {
    "find_file": Level.SAFE,
    "type_text": Level.SENSITIVE,
    "move_file": Level.SENSITIVE,
    "copy_file": Level.SENSITIVE,
    "delete_file": Level.DANGEROUS,
    "delete_folder": Level.DANGEROUS,
    "kill_app": Level.DANGEROUS,
    "shutdown": Level.DANGEROUS,
    "restart": Level.DANGEROUS,
}

LEVEL_BY_TOOL: dict[str, Level] = {
    **_LEVELS_DECLARED_AHEAD,
    **_LEVELS_FROM_SCHEMA,
}

DEFAULT_LEVEL = Level.SENSITIVE


def level_for(tool_name: str) -> Level:
    """The safety level for a tool. Unknown tools default to SENSITIVE."""
    return LEVEL_BY_TOOL.get(tool_name, DEFAULT_LEVEL)


class AnswerChannel(Protocol):
    """Source of a spoken yes/no answer for confirmations.

    Phase 9's own STT feed supplies its own implementation; nothing in
    this module needs to change for that swap.
    """

    def poll(self, max_wait: float = 0.5) -> str | None: ...


class SttAnswerChannel:
    """Spoken yes/no straight from the microphone.

    While a confirmation is up the STT feed keeps transcribing. This
    channel drains the same queue the orchestrator reads: a yes/no word
    answers the prompt, anything else is put back so it is processed as a
    normal command once the prompt resolves - the contract the old
    clipboard channel had. Bound to the running feed by the orchestrator
    at startup; unbound, it simply never answers.
    """

    def __init__(self) -> None:
        self._queue: "queue.Queue[tuple[int, str]] | None" = None

    def bind(self, feed) -> None:
        """Attach the live feed (anything exposing .events as a queue)."""
        self._queue = getattr(feed, "events", feed)

    def poll(self, max_wait: float = 0.5) -> str | None:
        if self._queue is None:
            return None
        try:
            seq, text = self._queue.get(timeout=max_wait)
        except queue.Empty:
            return None
        answer = normalize_command(text).strip(" .!?")
        if answer in YES_WORDS or answer in NO_WORDS:
            return answer
        self._queue.put((seq, text))  # not an answer - keep it for the loop
        return None


# Shared default so poll state persists across confirmations. The
# orchestrator binds the running STT feed to it at startup.
_DEFAULT_ANSWER_CHANNEL: AnswerChannel = SttAnswerChannel()
STT_ANSWER_CHANNEL = _DEFAULT_ANSWER_CHANNEL


# One shared reader thread for typed confirmations. A per-prompt thread
# would linger after a timeout and steal the NEXT prompt's typed answer.
_TYPED_ANSWERS: "queue.Queue[str]" = queue.Queue()
_reader_thread: threading.Thread | None = None


def _ensure_typed_reader() -> None:
    global _reader_thread
    if _reader_thread is None or not _reader_thread.is_alive():
        def read_typed_loop() -> None:
            while True:
                try:
                    line = input().strip()
                except (EOFError, OSError):
                    return
                if line:
                    _TYPED_ANSWERS.put(line)

        _reader_thread = threading.Thread(target=read_typed_loop, daemon=True)
        _reader_thread.start()


def confirm(
    description: str,
    level: Level,
    timeout: float = 30.0,
    answer_channel: AnswerChannel | None = None,
) -> bool:
    """Ask the user to approve an action. Returns True only on explicit yes.

    Both channels run in parallel: a typed answer in the console and a
    spoken answer via the AnswerChannel. First answer wins.
    """
    channel = answer_channel if answer_channel is not None else _DEFAULT_ANSWER_CHANNEL

    events.emit("confirm_requested", level=level.value, description=description)

    _ensure_typed_reader()
    # Discard anything typed before this prompt appeared, so a stale
    # answer cannot silently approve the next action.
    while not _TYPED_ANSWERS.empty():
        _TYPED_ANSWERS.get_nowait()

    deadline = time.time() + timeout
    answer = None
    while time.time() < deadline:
        try:
            answer = _TYPED_ANSWERS.get_nowait()
            break
        except queue.Empty:
            pass
        spoken = channel.poll()
        if spoken is not None:
            answer = spoken
            break
        time.sleep(0.1)

    if answer is None:
        events.emit("confirm_resolved", result="timeout")
        return False

    answer = normalize_command(answer).strip(" .!?")
    if answer in YES_WORDS:
        events.emit("confirm_resolved", result="approved")
        return True
    if answer in NO_WORDS:
        events.emit("confirm_resolved", result="denied")
        return False
    events.emit("confirm_resolved", result="unclear", answer=answer)
    return False
