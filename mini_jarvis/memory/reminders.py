"""Mini Jarvis - Phase 7: reminders with proactive surfacing.

A reminder is a memory note with type: reminder and a parsed due
timestamp (frontmatter "due" field, section 7.2). Parsing uses the
dateparser library ("in an hour", "tomorrow at 5pm", "next monday 9am");
when it cannot find a date, the time counts as missing rather than
guessed, and the tool reports that instead of scheduling nonsense.

A background thread (started by the orchestrator alongside the
transcript feed) polls the store every reminder_poll_seconds. On a due,
unfired reminder it shows a Windows toast (plyer) and marks it fired in
the DB - fired-state is DB-only, never written back into the .md file,
so Obsidian keeps showing what was due and when.

Startup grace window: reminders that came due while the assistant was
not running are not dumped as a barrage on start. Anything overdue by
more than reminder_startup_grace_minutes is marked fired silently (the
note still exists and shows up when asked), anything within the grace
window still notifies.

Test standalone:  python -m mini_jarvis.memory.reminders
"""

import datetime as dt
import threading
import time

from mini_jarvis import config, events
from mini_jarvis.memory import store
from mini_jarvis.tools import ToolFailure

MIN_POLL_SECONDS = 5

_scheduler_thread: threading.Thread | None = None


def parse_when(when: str) -> dt.datetime | None:
    """A future datetime from a natural-language time phrase, or None."""
    when = (when or "").strip()
    if not when:
        return None
    try:
        import dateparser
    except ImportError:
        events.emit("reminder_notice", message="dateparser is not installed")
        return None
    parsed = dateparser.parse(
        when,
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if parsed is None:
        return None
    return parsed.replace(microsecond=0, tzinfo=None)


def set_reminder(text: str, when: str) -> str:
    """Parse the time phrase, save the note, confirm in spoken style."""
    text = (text or "").strip()
    if not text:
        raise ToolFailure("nothing to remind you about")
    due = parse_when(when)
    if due is None:
        raise ToolFailure(
            f"could not work out the time from {when!r} - try e.g. 'at 5pm' "
            "or 'in an hour'"
        )
    store.save(text, note_type="reminder", due=due.isoformat(timespec="seconds"))
    return f"reminder set for {due.strftime('%I:%M %p on %a %d %b')}"


def _notify(text: str) -> None:
    try:
        from plyer import notification

        notification.notify(title="Jarvis reminder", message=text, timeout=10)
    except Exception:
        pass  # the console event below still surfaces the reminder


def _poll_once(now: dt.datetime, first_pass: bool) -> None:
    grace = dt.timedelta(minutes=config.load().reminder_startup_grace_minutes)
    for hit in store.due_reminders(now):
        store.mark_fired(hit.path, now)
        overdue = (now - hit.due_dt).total_seconds() / 60 if hit.due_dt else 0.0
        if first_pass and overdue > grace.total_seconds() / 60:
            continue  # stale while the assistant was off - silent fire
        _notify(hit.content)
        events.emit("reminder_fired", text=hit.content, due=hit.due or "")


def _scheduler_loop() -> None:
    cfg = config.load()
    first_pass = True
    while True:
        try:
            _poll_once(dt.datetime.now(), first_pass)
        except Exception:
            pass  # a transient DB/OSError must never kill the scheduler
        first_pass = False
        time.sleep(max(MIN_POLL_SECONDS, cfg.reminder_poll_seconds))


def start_scheduler() -> None:
    """Start the due-reminder poller once; no-op when memory is not set up."""
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return
    if not config.load().memory_vault_path:
        return
    store.init_db()
    poll = max(MIN_POLL_SECONDS, config.load().reminder_poll_seconds)
    events.emit("reminder_started", poll_seconds=poll)
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="jarvis-reminders", daemon=True
    )
    _scheduler_thread.start()


if __name__ == "__main__":
    try:
        store.vault_dir()
    except store.MemoryNotConfigured as error:
        print(f"[REMINDER] not configured: {error}")
        raise SystemExit(1)
    confirmation = set_reminder(
        "test reminder from the standalone run", "in 30 seconds"
    )
    print(f"[REMINDER] {confirmation}")
    print("[REMINDER] scheduler running for one poll cycle (Ctrl+C to stop)...")
    start_scheduler()
    _scheduler_thread.join()
