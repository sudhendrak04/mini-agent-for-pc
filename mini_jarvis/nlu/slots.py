"""Mini Jarvis - Phase 2: regex-based slot extraction.

Laya classifies the intent but does not extract entities. This module
pulls parameters (app names, URLs, search terms, volume amounts, file
names) out of the transcript with plain regular expressions, separately
from intent classification.

extract_slots(intent, text) dispatches to the extractor matching the
classified intent. Every extractor returns a dict of slots or None;
the public function always returns a dict (possibly empty).

Test standalone:  python -m mini_jarvis.nlu.slots
"""

import re

# Matches full http(s) URLs and bare domains like example.com or www.foo.io
URL_RE = re.compile(
    r"(https?://\S+"
    r"|(?:www\.)?[a-z0-9-]+\.(?:com|org|net|io|dev|ai|in|co|edu|gov|gg|tv|me|"
    r"app|xyz|info|tech|site|online|store|us|uk)(?:/\S*)?)",
    re.IGNORECASE,
)

# Trailing filler words that are not part of an app name
_APP_TAIL_RE = re.compile(r"\s+(?:app|application|program|exe|please)\b\.?$", re.IGNORECASE)

# Wake words / filler prefixes: "hey jarvis", "ok", "or", "please", "can you"...
_LEADING_FILLERS_RE = re.compile(
    r"^(?:"
    r"hey\s+(?:jarvis|google|siri|alexa|assistant)?\b\s*"
    r"|jarvis\b\s*"
    r"|ok(?:ay)?\b\s*"
    r"|um+\b\s*|uh+\b\s*"
    r"|or\b\s+|and\b\s+|then\b\s*"
    r"|please\b\s+"
    r"|can\s+you\b\s*|could\s+you\b\s*|would\s+you\b\s*|will\s+you\b\s*"
    r")+",
    re.IGNORECASE,
)


def normalize_command(text: str) -> str:
    """Lowercase and strip wake words / filler prefixes.

    'Or open YouTube' -> 'open youtube'
    'Hey Jarvis, please open Chrome' -> 'open chrome'
    Idempotent: normalizing twice gives the same result.
    """
    t = text.strip().lower()
    previous = None
    while previous != t:
        previous = t
        t = _LEADING_FILLERS_RE.sub("", t).strip()
    return t

DEFAULT_VOLUME_STEP = 10

# Spoken numbers Whisper transcribes as words: "to sixty percent"
_WORD_NUMS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}
_NUMBER_PHRASE_RE = re.compile(
    r"(?:(?P<digits>\d{1,3})"
    r"|(?P<tens>twenty|thirty|forty|fourty|fifty|sixty|seventy|eighty|ninety|hundred)"
    r"(?:\s*[- ]\s*(?P<units>one|two|three|four|five|six|seven|eight|nine))?"
    r"|(?P<unit>ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
    r"seventeen|eighteen|nineteen))",
    re.IGNORECASE,
)


def _parse_amount(text: str) -> int | None:
    """First number in the text, digits or words: 'sixty percent' -> 60."""
    match = _NUMBER_PHRASE_RE.search(text)
    if not match:
        return None
    if match.group("digits"):
        return int(match.group("digits"))
    value = _WORD_NUMS[(match.group("tens") or match.group("unit")).lower()]
    if match.group("units"):
        value += _WORD_NUMS[match.group("units").lower()]
    return value

# Sites people say by name, without a domain. Only website-first names go
# here; app-first names (spotify, discord, chrome) stay with the Laya
# classifier and Phase 3's open_app().
KNOWN_SITES = {
    "youtube": "youtube.com",
    "google": "google.com",
    "gmail": "mail.google.com",
    "maps": "maps.google.com",
    "google maps": "maps.google.com",
    "reddit": "reddit.com",
    "twitter": "x.com",
    "x": "x.com",
    "instagram": "instagram.com",
    "facebook": "facebook.com",
    "netflix": "netflix.com",
    "github": "github.com",
    "stackoverflow": "stackoverflow.com",
    "wikipedia": "wikipedia.org",
    "linkedin": "linkedin.com",
    "twitch": "twitch.tv",
    "chatgpt": "chatgpt.com",
    "bing": "bing.com",
    "duckduckgo": "duckduckgo.com",
}


def _extract_open_app(text: str) -> dict | None:
    """'open chrome', 'launch spotify please' -> {'app': 'chrome'}"""
    match = re.match(
        r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?"
        r"(open|launch|start|run)\s+(?P<name>.+)$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    name = _APP_TAIL_RE.sub("", match.group("name").strip(" .!?")).strip()
    # strip leading articles: "open a calculator" -> "calculator"
    name = re.sub(r"^(?:a|an|the|some)\s+", "", name, flags=re.IGNORECASE).strip()
    return {"app": name} if name else None


def _extract_named_site(text: str) -> dict | None:
    """'open youtube' (a site said by name, no dot) -> {'url': 'https://youtube.com'}"""
    match = re.match(r"^(?:open|go\s+to|visit|launch)\s+(?P<site>.+)$", text, re.IGNORECASE)
    if not match:
        return None
    domain = KNOWN_SITES.get(match.group("site").strip(" .!?"))
    return {"url": "https://" + domain} if domain else None


def _extract_open_url(text: str) -> dict | None:
    """'open github.com', 'open https://news.ycombinator.com' -> {'url': ...}"""
    match = URL_RE.search(text)
    if not match:
        return None
    url = match.group(1)
    if not url.lower().startswith("http"):
        url = "https://" + url
    return {"url": url}


_YOUTUBE_PATTERNS = [
    # "play lofi beats on youtube", "watch minecraft tutorials on youtube",
    # "perfect on youtube" (ASR often drops the leading verb)
    re.compile(
        r"^(?:play\s+|watch\s+|search\s+(?:for\s+)?|find\s+|look\s+up\s+)?"
        r"(?P<q>.+?)\s+on\s+youtube\b.*$",
        re.IGNORECASE,
    ),
    # "search youtube for lofi", "youtube cat videos"
    re.compile(r"^(?:search\s+)?youtube\s+(?:search\s+)?(?:for\s+)?(?P<q>.+)$", re.IGNORECASE),
    # "play some lofi beats", "play despacito" (bare play = YouTube here)
    re.compile(
        r"^play\s+(?:some\s+|a\s+|the\s+)?(?P<q>.+?)\s+(?:video|videos|song|songs|music)\b.*$",
        re.IGNORECASE,
    ),
    # "play despacito" - bare play with no media keyword
    re.compile(r"^play\s+(?:some\s+|a\s+|the\s+)?(?P<q>.+)$", re.IGNORECASE),
]


def _extract_youtube(text: str) -> dict | None:
    for pattern in _YOUTUBE_PATTERNS:
        match = pattern.match(text)
        if match and match.group("q").strip():
            return {"query": _clean_query(match.group("q"))}
    return None


_GOOGLE_PATTERNS = [
    # "google python asyncio"
    re.compile(r"^google\s+(?P<q>.+)$", re.IGNORECASE),
    # "do a web search for rust vs go", "search the internet for x"
    re.compile(
        r"^(?:do\s+a\s+)?(?:google|web|internet)\s+search(?:\s+for)?\s+(?P<q>.+)$",
        re.IGNORECASE,
    ),
    # "search for python asyncio", "search the web for x"
    re.compile(
        r"^search(?:\s+(?:the\s+)?(?:web|internet))?\s+for\s+(?P<q>.+)$",
        re.IGNORECASE,
    ),
    # "look up cheapest flights"
    re.compile(r"^look\s+up\s+(?P<q>.+)$", re.IGNORECASE),
]


def _extract_google(text: str) -> dict | None:
    for pattern in _GOOGLE_PATTERNS:
        match = pattern.match(text)
        if match and match.group("q").strip():
            return {"query": _clean_query(match.group("q"))}
    return None


# People name the browser a search should happen in; that is not part of
# the query: "search for fit girl repack on chrome" -> "fit girl repack"
_BROWSER_TAIL_RE = re.compile(
    r"\s+on\s+(?:google\s+chrome|chrome|edge|firefox|browser|the\s+browser|"
    r"incognito|internet)s?\b[.!]?$",
    re.IGNORECASE,
)

# "click on the video of despacito" -> "despacito"
_VIDEO_PREFIX_RE = re.compile(
    r"^(?:click|go|jump|open)\s+(?:on\s+|to\s+|over\s+)?(?:the\s+|a\s+)?"
    r"(?:first\s+|top\s+)?(?:video|clip|result)\s+(?:of|for|on|from)\s+",
    re.IGNORECASE,
)


def _clean_query(query: str) -> str:
    """Strip browser-targeting tails and video-reference prefixes from a
    search query: 'fit girl repack on chrome' -> 'fit girl repack',
    'click on the video of despacito' -> 'despacito'."""
    query = _BROWSER_TAIL_RE.sub("", query)
    query = _VIDEO_PREFIX_RE.sub("", query)
    return query.strip(" .!?")


def _extract_volume(text: str) -> dict | None:
    """Volume phrases -> {'direction': up|down|set, 'amount': int|None}

    "volume TO N" always means set exactly ("increase volume to 60" -> set).
    "BY N" is relative ("turn volume up by 20").
    """
    t = text.lower()

    match = re.search(r"\bvolume\s+(?:up\s+|down\s+)?(?:to|at)\s+", t)
    if match:
        amount = _parse_amount(t[match.end():])
        if amount is not None:
            return {"direction": "set", "amount": max(0, min(amount, 100))}

    if (
        re.search(r"\b(?:raise|increase|turn\s+up|push\s+up|louder)\b", t)
        or re.search(r"\bvolume\s+up\b", t)
    ):
        direction = "up"
    elif (
        re.search(r"\b(?:lower|decrease|reduce|turn\s+down|quieter)\b", t)
        or re.search(r"\bvolume\s+down\b", t)
    ):
        direction = "down"
    else:
        return None

    by_match = re.search(r"\bby\s+", t)
    amount = _parse_amount(t[by_match.end():]) if by_match else None
    return {"direction": direction, "amount": amount if amount is not None else DEFAULT_VOLUME_STEP}


def _extract_find_file(text: str) -> dict | None:
    """'find report.docx in D:\\docs' -> {'name': 'report.docx', 'search_path': ...}"""
    match = re.match(
        r"^(?:find|locate|search\s+for)\s+(?:the\s+|a\s+)?"
        r"(?:file|files|document|documents|folder|folders)?\s*"
        r"(?:named|called)?\s*(?P<name>\S.*?)(?:\s+(?:in|under|from|on)\s+(?P<path>.+))?$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    name = match.group("name").strip(" .!?")
    if not name:
        return None
    slots: dict = {"name": name}
    if match.group("path"):
        slots["search_path"] = match.group("path").strip(" .!?")
    return slots


_DELETE_RE = re.compile(
    r"^delete\s+(?:the\s+)?(?:file\s+|document\s+|folder\s+)?(?P<name>.+?)"
    r"(?:\s+(?:in|under|from|on)\s+(?P<path>.+))?[.!]?$",
    re.IGNORECASE,
)
_MOVE_RE = re.compile(
    r"^move\s+(?:the\s+)?(?:file\s+|document\s+)?(?P<name>.+?)"
    r"(?:\s+from\s+(?P<src>.+?))?\s+(?:to|into)\s+(?P<destination>.+)$",
    re.IGNORECASE,
)
_COPY_RE = re.compile(
    r"^copy\s+(?:the\s+)?(?:file\s+|document\s+)?(?P<name>.+?)"
    r"(?:\s+from\s+(?P<src>.+?))?\s+(?:to|into)\s+(?P<destination>.+)$",
    re.IGNORECASE,
)


def _extract_delete_file(text: str) -> dict | None:
    """'delete report.docx in D:\\docs' -> {'name': ..., 'search_path': ...}"""
    match = _DELETE_RE.match(text)
    if not match:
        return None
    name = match.group("name").strip(" .!?")
    if not name:
        return None
    slots: dict = {"name": name}
    if match.group("path"):
        slots["search_path"] = match.group("path").strip(" .!?")
    return slots


def _extract_transfer_file(pattern: re.Pattern, text: str) -> dict | None:
    """Shared move/copy slots: name [+ from folder] + to/into destination."""
    match = pattern.match(text)
    if not match:
        return None
    name = match.group("name").strip(" .!?")
    destination = match.group("destination").strip(" .!?")
    if not name or not destination:
        return None
    slots: dict = {"name": name, "destination": destination}
    if match.group("src"):
        slots["search_path"] = match.group("src").strip(" .!?")
    return slots


def _extract_move_file(text: str) -> dict | None:
    """'move report.docx from desktop to documents' -> slots."""
    return _extract_transfer_file(_MOVE_RE, text)


def _extract_copy_file(text: str) -> dict | None:
    """'copy budget.xlsx to D:\\backup' -> slots."""
    return _extract_transfer_file(_COPY_RE, text)


_CREATE_RE = re.compile(
    r"^(?:create|make|new)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+|empty\s+|blank\s+)?"
    r"(?P<noun>file|text\s+file|text\s+doc(?:ument)?|doc(?:ument)?|note\s+file)?\s*"
    r"(?:called|named)?\s*(?P<name>.+?)"
    r"(?:\s+(?:on|in|into|under)\s+(?P<place>.+))?[.!]?$",
    re.IGNORECASE,
)


def _extract_create_file(text: str) -> dict | None:
    """'create text.txt on desktop' -> {'name': 'text.txt', 'destination': 'desktop'}"""
    match = _CREATE_RE.match(text)
    if not match:
        return None
    name = match.group("name").strip(" .!?")
    if not name:
        return None
    if not match.group("noun") and "." not in name:
        return None  # "make a note about X" - a memory, not a file
    slots: dict = {"name": name}
    if match.group("place"):
        slots["destination"] = match.group("place").strip(" .!?")
    return slots


_SCREENSHOT_RE = re.compile(
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:take\s+(?:a\s+)?|do\s+a\s+)?screenshot\b.*$",
    re.IGNORECASE,
)

_TYPE_RE = re.compile(
    r"^(?:type|write|put|enter)\s+(?P<text>.+?)"
    r"(?:\s+(?:in|into|on|onto)\s+(?:a\s+|the\s+)?(?:new\s+|fresh\s+|blank\s+)?"
    r"(?P<where>notepad|word|excel|docs?|chrome|edge|terminal))?[.!]?$",
    re.IGNORECASE,
)

_MEMORY_SAVE_RE = re.compile(
    r"^(?:remember|note|note\s+down|save|jot\s+down)\s+(?:that\s+)?(?P<content>.+)$",
    re.IGNORECASE,
)
_REMINDER_RE = re.compile(
    r"^remind\s+me\s+to\s+(?P<text>.+?)\s+(?:at|on|in)\s+(?P<when>.+)$",
    re.IGNORECASE,
)
_REMINDER_WHEN_FIRST_RE = re.compile(
    r"^remind\s+me\s+(?:at|on|in)\s+(?P<when>.+?)\s+to\s+(?P<text>.+)$",
    re.IGNORECASE,
)
_ASK_RE = re.compile(
    r"^(?:what\s+did\s+i\s+say\s+about|do\s+you\s+remember|recall|"
    r"what\s+do\s+you\s+know\s+about|what\s+did\s+i\s+tell\s+you\s+about|"
    r"what(?:'s|\s+is)|who(?:'s|\s+is)|how\s+do(?:es)?|explain)\s+"
    r"(?P<query>.+)$",
    re.IGNORECASE,
)

# "note this in notepad" names an app, so it is TYPE_TEXT, not MEMORY_SAVE
# (plan section 7.9a disambiguation: app name wins over memory trigger).
_APP_CONTEXT_RE = re.compile(
    r"\s+(?:in|into|onto|on)\s+(?:a\s+|the\s+)?(?:new\s+|fresh\s+|blank\s+)?"
    r"(?:notepad|word|excel|docs?|chrome|edge|terminal)\b[.!]?$",
    re.IGNORECASE,
)


def _extract_memory_save(text: str) -> dict | None:
    """'remember that the wifi password is hunter2' -> {'content': ...}"""
    match = _MEMORY_SAVE_RE.match(text)
    if not match:
        return None
    content = match.group("content").strip(" .!?")
    if not content or _APP_CONTEXT_RE.search(content):
        return None
    return {"content": content}


def _extract_reminder(text: str) -> dict | None:
    """'remind me to call mom at 5pm' -> {'text': 'call mom', 'when': '5pm'}
    'remind me in an hour to call mom' -> same slots, time-first order."""
    for pattern in (_REMINDER_RE, _REMINDER_WHEN_FIRST_RE):
        match = pattern.match(text)
        if match:
            break
    else:
        return None
    text_slot = match.group("text").strip(" .!?")
    when_slot = match.group("when").strip(" .!?")
    if not text_slot or not when_slot:
        return None
    return {"text": text_slot, "when": when_slot}


def _extract_ask(text: str) -> dict | None:
    """'what did i say about the demo' -> {'query': 'the demo'}"""
    match = _ASK_RE.match(text)
    if not match:
        return None
    query = match.group("query").strip(" .!?")
    return {"query": query} if query else None


def _extract_type_text(text: str) -> dict | None:
    """'type hello jarvis in notepad' -> {'text': 'hello jarvis', 'where': 'notepad'}"""
    match = _TYPE_RE.match(text)
    if not match or not match.group("text").strip():
        return None
    slots: dict = {"text": match.group("text").strip()}
    if match.group("where"):
        slots["where"] = match.group("where").strip()
    return slots


# Verbs that may start a standalone command inside an "X and Y" compound
_COMMAND_START_RE = re.compile(
    r"^(?:open|launch|start|run|play|watch|search|google|find|locate|take|"
    r"set|turn|increase|decrease|raise|lower|volume|type|screenshot|"
    r"delete|move|copy|create)\b",
    re.IGNORECASE,
)

_SPLIT_RE = re.compile(r"\s+(?:and\s+then|and|then)\s+", re.IGNORECASE)


def split_commands(text: str) -> list[str]:
    """Split compound commands into standalone ones.

    'open chrome and search for youtube' -> ['open chrome', 'search for youtube']

    Conservative: only splits when EVERY part independently starts with a
    command verb, so 'google taylor and ariana' stays a single search.
    """
    t = normalize_command(text)
    parts = [p.strip(" .!?") for p in _SPLIT_RE.split(t)]
    parts = [p for p in parts if p]
    if len(parts) < 2 or not all(_COMMAND_START_RE.match(p) for p in parts):
        return [t]
    return parts


def has_command_connector(text: str) -> bool:
    """True when a transcript contains 'and'/'then' command connectors.

    Used by the orchestrator to send un-splittable compounds to the LLM
    planner instead of trusting a single-intent classification.
    """
    return bool(_SPLIT_RE.search(normalize_command(text)))


def rule_classify(text: str) -> tuple[str, dict] | None:
    """Deterministic intent rules for lexically unambiguous commands.

    These catch phrasings that are certain from their words alone - no
    model needed, zero latency. Anything ambiguous returns None and is
    handed to the Laya classifier instead.
    """
    t = text.lower().strip()

    slots = _extract_memory_save(text)
    if slots:
        return "MEMORY_SAVE", slots

    slots = _extract_reminder(text)
    if slots:
        return "REMINDER_SET", slots

    if (
        re.match(
            r"^(?:set|put|turn|raise|increase|lower|decrease|reduce|push|volume)\b",
            t,
        )
        and "volume" in t
    ):
        slots = _extract_volume(text)
        if slots:
            return "VOLUME_CONTROL", slots

    if "youtube" in t:
        slots = _extract_youtube(text)
        if slots:
            return "YOUTUBE_SEARCH", slots

    if re.match(r"^play\s+\S", t):
        # Bare "play despacito" with no "on youtube" - on this assistant a
        # play request means YouTube. (Whisper also misspells song titles,
        # e.g. "despasito"; the raw query still finds the video.)
        slots = _extract_youtube(text)
        if slots:
            return "YOUTUBE_SEARCH", slots
        query = re.sub(
            r"^play\s+(?:some\s+|a\s+|the\s+)?", "", text, flags=re.IGNORECASE
        ).strip(" .!?")
        if query:
            return "YOUTUBE_SEARCH", {"query": query}

    if re.match(r"^google\s+\S", t):
        slots = _extract_google(text)
        if slots:
            return "GOOGLE_SEARCH", slots

    if (
        re.match(r"^search\s+for\s+\S", t)
        or re.match(r"^search\s+(?:the\s+)?(?:web|internet)\s+for\s+\S", t)
        or re.match(r"^look\s+up\s+\S", t)
    ):
        slots = _extract_google(text)
        if slots:
            return "GOOGLE_SEARCH", slots

    # ASK stays below the media/search rules so "what's playing on
    # youtube" still routes to YouTube, but above everything else -
    # "what is X" / "who is X" / "explain X" are questions, zero latency.
    slots = _extract_ask(text)
    if slots:
        return "ASK", slots

    if re.match(r"^(?:open|go\s+to|visit|launch)\s+\S", t) and not _SPLIT_RE.search(t):
        # The "and"/"then" guard keeps single-verb rules out of compound
        # phrasings ("open spotify and make the volume quieter") - those go
        # to Laya and, below threshold, to the LLM planner instead.
        slots = _extract_open_url(text)
        if slots:
            return "OPEN_URL", slots
        # "open youtube" - a site said by name, no dot in the transcript
        slots = _extract_named_site(text)
        if slots:
            return "OPEN_URL", slots
        # bare "open chrome" - no URL, no known site: a desktop app
        slots = _extract_open_app(text)
        if slots:
            return "OPEN_APPLICATION", slots

    if _SCREENSHOT_RE.match(t):
        return "SCREENSHOT", {}

    if re.match(r"^(?:type|write|put|enter)\s+\S", t):
        slots = _extract_type_text(text)
        if slots:
            return "TYPE_TEXT", slots

    if re.match(r"^(?:find|locate)\s+\S", t):
        slots = _extract_find_file(text)
        if slots:
            return "FILE_OPERATION", slots

    if re.match(r"^delete\s+\S", t):
        slots = _extract_delete_file(text)
        if slots:
            return "DELETE_FILE", slots

    if re.match(r"^move\s+\S", t):
        slots = _extract_move_file(text)
        if slots:
            return "MOVE_FILE", slots

    if re.match(r"^copy\s+\S", t):
        slots = _extract_copy_file(text)
        if slots:
            return "COPY_FILE", slots

    if re.match(r"^(?:create|make|new)\s+\S", t):
        slots = _extract_create_file(text)
        if slots:
            return "CREATE_FILE", slots

    return None


def extract_slots(intent: str, text: str) -> dict:
    """Extract parameters for the classified intent from the transcript."""
    text = normalize_command(text)
    if intent == "OPEN_APPLICATION":
        # A transcript naming a URL classifies close to OPEN_APPLICATION;
        # prefer the URL slot when one is present.
        if URL_RE.search(text):
            slots = _extract_open_url(text)
            return slots if slots else {}
        slots = _extract_open_app(text)
        return slots if slots else {}
    if intent == "OPEN_URL":
        slots = _extract_open_url(text)
        if slots:
            return slots
        slots = _extract_named_site(text)
        return slots if slots else {}
    if intent == "YOUTUBE_SEARCH":
        slots = _extract_youtube(text)
        return slots if slots else {}
    if intent == "GOOGLE_SEARCH":
        slots = _extract_google(text)
        if slots:
            return slots
        # "... on youtube" style searches may still classify as a generic
        # search; fall back to the YouTube extractor.
        slots = _extract_youtube(text)
        return slots if slots else {}
    if intent == "VOLUME_CONTROL":
        slots = _extract_volume(text)
        return slots if slots else {}
    if intent == "FILE_OPERATION":
        slots = _extract_find_file(text)
        return slots if slots else {}
    if intent == "TYPE_TEXT":
        slots = _extract_type_text(text)
        return slots if slots else {}
    if intent == "DELETE_FILE":
        slots = _extract_delete_file(text)
        return slots if slots else {}
    if intent == "MOVE_FILE":
        slots = _extract_move_file(text)
        return slots if slots else {}
    if intent == "COPY_FILE":
        slots = _extract_copy_file(text)
        return slots if slots else {}
    if intent == "CREATE_FILE":
        slots = _extract_create_file(text)
        return slots if slots else {}
    if intent == "MEMORY_SAVE":
        slots = _extract_memory_save(text)
        return slots if slots else {}
    if intent == "REMINDER_SET":
        slots = _extract_reminder(text)
        return slots if slots else {}
    if intent == "ASK":
        slots = _extract_ask(text)
        return slots if slots else {}
    return {}


if __name__ == "__main__":
    samples = [
        ("OPEN_APPLICATION", "open chrome"),
        ("OPEN_APPLICATION", "could you launch spotify please"),
        ("OPEN_URL", "open https://news.ycombinator.com"),
        ("OPEN_URL", "open github.com"),
        ("YOUTUBE_SEARCH", "play lofi beats on youtube"),
        ("YOUTUBE_SEARCH", "search youtube for synthwave"),
        ("GOOGLE_SEARCH", "google python asyncio"),
        ("GOOGLE_SEARCH", "search the web for best mechanical keyboards"),
        ("VOLUME_CONTROL", "turn volume up by 20"),
        ("VOLUME_CONTROL", "set volume to 30"),
        ("VOLUME_CONTROL", "volume down"),
        ("FILE_OPERATION", "find report.docx in D:\\docs"),
        ("MEMORY_SAVE", "remember that the wifi password is hunter2"),
        ("MEMORY_SAVE", "jot down the demo is on friday"),
        ("REMINDER_SET", "remind me to call mom at 5pm"),
        ("ASK", "what did i say about the launch plan"),
        ("ASK", "what is the capital of france"),
        ("DELETE_FILE", "delete report.docx"),
        ("DELETE_FILE", "delete the report in D:\\docs"),
        ("MOVE_FILE", "move report.docx from desktop to documents"),
        ("COPY_FILE", "copy budget.xlsx to D:\\backup"),
        ("CREATE_FILE", "create text.txt on desktop"),
        ("CREATE_FILE", "make a file called my notes.txt in documents"),
        ("CREATE_FILE", "new file report-final.txt"),
        ("MEMORY_SAVE", "make a note about the meeting"),
        ("VOLUME_CONTROL", "make the volume quieter"),
        ("TYPE_TEXT", "note this in notepad"),
        ("UNKNOWN", "tell me a joke"),
    ]
    for intent, phrase in samples:
        got = extract_slots(intent, phrase)
        status = "ok" if got else "MISS"
        print(f"[{status:4}] {intent:18} {phrase!r:55} -> {got}")
