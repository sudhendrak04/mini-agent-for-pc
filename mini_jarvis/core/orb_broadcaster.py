"""Mini Jarvis - Phase 9: orb state broadcaster.

A tiny WebSocket server inside the assistant process, subscribed to the
event stream. It never touches the pipeline: it only translates the
events the orchestrator already emits into the orb state contract, so
the orb stays a pure consumer (main plan section 9.3, orb plan 5.1).

    {"state": "idle"}
    {"state": "listening"}
    {"state": "transcribing"}
    {"state": "thinking", "detail": "classifying"}
    {"state": "confirming", "detail": "Delete final_report.docx?"}
    {"state": "acting", "visual": "searching", "detail": "google search: python asyncio"}
    {"state": "acting", "visual": "working", "detail": "opening chrome"}

It runs on its own thread with its own asyncio loop and pushes messages
across with run_coroutine_threadsafe, because the events arrive from the
main, STT, and reminder threads. The last message is cached and sent to
every client on connect, so an orb that starts late is never stuck on a
stale state.
"""

import asyncio
import json
import threading

try:  # websockets >= 13
    from websockets.asyncio.server import serve
except ImportError:  # pragma: no cover - older layout
    from websockets.serve import serve

from mini_jarvis import config, events

HOST = "127.0.0.1"

# Which orb animation a tool gets while it runs (orb plan 5.1). Anything
# not listed reads as "working".
ORB_VISUAL_FOR_TOOL = {
    "google_search": "searching",
    "youtube_search": "searching",
    "find_file": "searching",
    "open_url": "searching",
}

# Friendly one-line labels; the pill is small so these stay short.
ORB_LABEL_FOR_TOOL = {
    "open_app": "opening",
    "open_url": "opening",
    "google_search": "google search",
    "youtube_search": "youtube search",
    "take_screenshot": "taking a screenshot",
    "change_volume": "volume",
    "find_file": "looking for files",
    "create_file": "creating file",
    "copy_file": "copying file",
    "move_file": "moving file",
    "delete_file": "deleting file",
    "type_text": "typing",
    "save_memory": "saving to memory",
    "set_reminder": "setting a reminder",
    "ask": "answering",
}

# Tools whose spoken name reads well followed by the first argument.
_ARG_FIRST = {"google_search", "youtube_search", "ask", "open_app"}
# ...of those, the ones that read better with a colon.
_COLON_ARG = {"google_search", "youtube_search", "ask"}
_MAX_DETAIL = 48


def _tool_detail(tool: str, description: str) -> str:
    """'opening chrome', 'google search: python asyncio', 'taking a screenshot'."""
    label = ORB_LABEL_FOR_TOOL.get(tool, tool.replace("_", " "))
    if tool not in _ARG_FIRST:
        return label
    first = description.partition("(")[2].partition(",")[0].partition(")")[0]
    first = first.strip().strip("'\"").lstrip("'")
    if not first:
        return label
    if len(first) > 32:
        first = first[:32].rstrip() + "…"
    joiner = ": " if tool in _COLON_ARG else " "
    detail = f"{label}{joiner}{first}"
    return detail if len(detail) <= _MAX_DETAIL else detail[:_MAX_DETAIL].rstrip() + "…"


def _confirm_detail(description: str) -> str:
    """'Delete final_report.docx?' - the tool name is enough context."""
    tool = description.partition("(")[0].strip()
    if tool in ORB_LABEL_FOR_TOOL:
        tool = ORB_LABEL_FOR_TOOL[tool]
    return f"{tool.capitalize()}…"


class OrbBroadcaster:
    """WebSocket server + event subscriber. One instance per process."""

    def __init__(self, port: int | None = None) -> None:
        self._port = port if port is not None else config.load().orb_websocket_port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._clients: set = set()
        self._last: dict = {"state": "idle"}
        self._ready = threading.Event()

    # -- public ----------------------------------------------------------
    def start(self) -> None:
        """Start the server thread; safe to call once."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop, name="jarvis-orb", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)
        events.subscribe(self._on_event)

    def stop(self) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)

    # -- server ----------------------------------------------------------
    def _run_loop(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception as error:  # noqa: BLE001 - never kill the assistant
            events.emit("orb_broadcaster_error", message=str(error))

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        async with serve(self._handler, HOST, self._port):
            self._ready.set()
            while True:
                await asyncio.sleep(3600)

    async def _shutdown(self) -> None:
        for client in list(self._clients):
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()
        self._loop = None
        if self._loop is not None:
            self._loop.stop()

    async def _handler(self, websocket, *_extra) -> None:
        self._clients.add(websocket)
        try:
            await websocket.send(json.dumps(self._last))
        except Exception:  # noqa: BLE001
            pass
        try:
            await websocket.wait_closed()
        finally:
            self._clients.discard(websocket)

    async def _broadcast(self, message: dict) -> None:
        if not self._clients:
            return
        payload = json.dumps(message)
        for client in list(self._clients):
            try:
                await client.send(payload)
            except Exception:  # noqa: BLE001 - a dead client must not matter
                self._clients.discard(client)

    def _publish(self, message: dict) -> None:
        self._last = message
        loop = self._loop
        if loop is None or not self._clients:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(message), loop)
        except RuntimeError:
            pass  # loop shutting down

    # -- mapping ---------------------------------------------------------
    def _on_event(self, event: str, fields: dict) -> None:
        message = self._map(event, fields)
        if message is not None:
            self._publish(message)

    def _map(self, event: str, fields: dict) -> dict | None:
        if event == "stt_speech_start":
            return {"state": "listening"}
        if event == "stt_transcribing":
            return {"state": "transcribing"}
        if event == "transcript_received":
            return {"state": "thinking", "detail": "classifying…"}
        if event == "plan_created":
            return {"state": "thinking", "detail": "planning the steps…"}
        if event == "slot_fill_started":
            return {"state": "thinking", "detail": "reading the details…"}
        if event == "confirm_requested":
            return {
                "state": "confirming",
                "detail": _confirm_detail(fields.get("description", "")),
            }
        if event == "confirm_resolved" and fields.get("result") == "approved":
            return {"state": "thinking", "detail": "approved"}
        if event in ("tool_run", "tool_failed", "tool_error", "tool_cancelled"):
            tool = fields.get("tool", "")
            return {
                "state": "acting",
                "visual": ORB_VISUAL_FOR_TOOL.get(tool, "working"),
                "detail": _tool_detail(tool, fields.get("description", "")),
            }
        if event == "reminder_fired":
            text = fields.get("text", "")
            if len(text) > 40:
                text = text[:40].rstrip() + "…"
            return {
                "state": "acting",
                "visual": "working",
                "detail": "reminder: " + text,
            }
        if event == "assistant_idle":
            return {"state": "idle"}
        if event == "assistant_stopped":
            return {"state": "idle"}
        return None
