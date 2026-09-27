"""Mini Jarvis - one schema for every intent.

Single source of truth: each intent appears exactly once here, with its
Laya criteria text, its slots (required/optional), the tool that
executes it, its safety level, and the one-line slot description used in
the planner's system prompt.

intents.py, planner.py, tools/registry.py and safety.py all derive their
tables from INTENTS, so adding an intent (Phases 7 and 8) is one entry
here instead of four separate edits in four modules.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from mini_jarvis.tools.apps import open_app
from mini_jarvis.tools.files import (
    _delete_file_prepare,
    _find_file_adapter,
    _transfer_file_prepare,
    copy_file,
    create_file,
    delete_file,
    move_file,
)
from mini_jarvis.tools.memory import ask, save_memory, set_reminder
from mini_jarvis.tools.system import _type_text_adapter, change_volume, take_screenshot
from mini_jarvis.tools.web import google_search, open_url, youtube_search


class Level(str, Enum):
    """Safety level of a tool. Defined here so the schema owns the policy."""

    SAFE = "SAFE"
    SENSITIVE = "SENSITIVE"
    DANGEROUS = "DANGEROUS"


@dataclass(frozen=True)
class IntentSpec:
    criteria: str                 # Laya's choice-criteria text
    required: tuple[str, ...]
    optional: tuple[str, ...]
    tool: Callable | None = None  # None for classifier-only intents
    level: Level | None = None
    planner_slot_hint: str = ""   # slot line in the planner system prompt
    prepare: Callable | None = None  # pre-gate slot resolution (files)


INTENTS: dict[str, IntentSpec] = {
    "OPEN_APPLICATION": IntentSpec(
        criteria="open a desktop app such as chrome, spotify or notepad",
        required=("app",),
        optional=(),
        tool=open_app,
        level=Level.SAFE,
        planner_slot_hint='{"app": "<name>"}',
    ),
    "OPEN_URL": IntentSpec(
        criteria="open a website such as github.com or wikipedia.org",
        required=("url",),
        optional=(),
        tool=open_url,
        level=Level.SAFE,
        planner_slot_hint='{"url": "<url or site>"}',
    ),
    "YOUTUBE_SEARCH": IntentSpec(
        criteria="play music or videos on YouTube",
        required=("query",),
        optional=(),
        tool=youtube_search,
        level=Level.SAFE,
        planner_slot_hint='{"query": "<search words>"}',
    ),
    "GOOGLE_SEARCH": IntentSpec(
        criteria="search the web for information about something",
        required=("query",),
        optional=(),
        tool=google_search,
        level=Level.SAFE,
        planner_slot_hint='{"query": "<search words>"}',
    ),
    "SCREENSHOT": IntentSpec(
        criteria="take a screenshot",
        required=(),
        optional=(),
        tool=take_screenshot,
        level=Level.SAFE,
        planner_slot_hint="{}",
    ),
    "VOLUME_CONTROL": IntentSpec(
        criteria="change or set the volume",
        required=("direction", "amount"),
        optional=(),
        tool=change_volume,
        level=Level.SAFE,
        planner_slot_hint='{"direction": "up"|"down"|"set", "amount": <int 0-100>}',
    ),
    "FILE_OPERATION": IntentSpec(
        criteria="find, move, copy or delete files",
        required=("name",),
        optional=("search_path",),
        tool=_find_file_adapter,
        level=Level.SAFE,
        planner_slot_hint='{"name": "<file name>", "search_path": "<folder, optional>"}',
    ),
    "DELETE_FILE": IntentSpec(
        criteria="permanently delete a file",
        required=("name",),
        optional=("search_path",),
        tool=delete_file,
        level=Level.DANGEROUS,
        planner_slot_hint='{"name": "<exact file name>", "search_path": "<folder, optional>"}',
        prepare=_delete_file_prepare,
    ),
    "MOVE_FILE": IntentSpec(
        criteria="move a file to a different folder",
        required=("name", "destination"),
        optional=("search_path",),
        tool=move_file,
        level=Level.SENSITIVE,
        planner_slot_hint='{"name": "<file name>", "destination": "<folder>"}',
        prepare=_transfer_file_prepare,
    ),
    "COPY_FILE": IntentSpec(
        criteria="copy a file to a different folder",
        required=("name", "destination"),
        optional=("search_path",),
        tool=copy_file,
        level=Level.SENSITIVE,
        planner_slot_hint='{"name": "<file name>", "destination": "<folder>"}',
        prepare=_transfer_file_prepare,
    ),
    "CREATE_FILE": IntentSpec(
        criteria="create a new empty file on the desktop or in a folder",
        required=("name",),
        optional=("destination",),
        tool=create_file,
        level=Level.SAFE,
        planner_slot_hint='{"name": "<file name with extension if spoken>", "destination": "<folder, optional>"}',
    ),
    "TYPE_TEXT": IntentSpec(
        criteria="type or write text into a window or document",
        required=("text",),
        optional=("where",),
        tool=_type_text_adapter,
        level=Level.SENSITIVE,
        planner_slot_hint='{"text": "<text>", "where": "<app, optional>"}',
    ),
    "MEMORY_SAVE": IntentSpec(
        criteria="save a private note or fact, with no app or window named",
        required=("content",),
        optional=(),
        tool=save_memory,
        level=Level.SAFE,
        planner_slot_hint='{"content": "<the note, verbatim>"}',
    ),
    "REMINDER_SET": IntentSpec(
        criteria="set a reminder to do something at a future time",
        required=("text", "when"),
        optional=(),
        tool=set_reminder,
        level=Level.SAFE,
        planner_slot_hint='{"text": "<what to be reminded of>", "when": "<time phrase>"}',
    ),
    "ASK": IntentSpec(
        criteria="ask a question or ask to recall something said or saved earlier",
        required=("query",),
        optional=(),
        tool=ask,
        level=Level.SAFE,
        planner_slot_hint='{"query": "<the question>"}',
    ),
    # Classifier-only intents: no tool, no safety level. COMPLEX_TASK is
    # resolved by the Ollama planner; UNKNOWN means "not a command".
    "COMPLEX_TASK": IntentSpec(
        criteria="a request combining two or more different actions",
        required=(),
        optional=(),
    ),
    "UNKNOWN": IntentSpec(
        criteria="small talk or a general question",
        required=(),
        optional=(),
    ),
}
