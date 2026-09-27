"""Mini Jarvis - Phase 3: browser tools.

Searches open the pre-filled results page directly (approved approach):
same end result as typing into the page, faster and more reliable.
"""

import re

import webbrowser
from urllib.parse import quote_plus

from mini_jarvis.tools import ToolFailure

# A query that is itself an address: "search for github.com" should open
# the site, not a Google results page about it.
_BARE_ADDRESS_RE = re.compile(
    r"(?:https?://\S+|(?:www\.)?[\w-]+\.[a-z]{2,}(?:/\S*)?)",
    re.IGNORECASE,
)


def _open(url: str) -> None:
    if not webbrowser.open(url):
        raise ToolFailure("no default browser available")


def open_url(url: str) -> str:
    """Open a URL in the default browser."""
    url = (url or "").strip()
    if not url:
        raise ToolFailure("no URL given")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    _open(url)
    return f"opened {url}"


def youtube_search(query: str) -> str:
    """Search YouTube in the default browser."""
    query = (query or "").strip()
    if not query:
        raise ToolFailure("no search query given")
    _open("https://www.youtube.com/results?search_query=" + quote_plus(query))
    return f"searching YouTube for '{query}'"


def google_search(query: str) -> str:
    """Search Google in the default browser (or open the site directly
    when the query is itself an address like github.com)."""
    query = (query or "").strip()
    if not query:
        raise ToolFailure("no search query given")
    if _BARE_ADDRESS_RE.fullmatch(query):
        url = query if query.lower().startswith("http") else "https://" + query
        _open(url)
        return f"opened {url} (query was an address)"
    _open("https://www.google.com/search?q=" + quote_plus(query))
    return f"searching Google for '{query}'"
