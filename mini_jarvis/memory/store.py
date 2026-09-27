"""Mini Jarvis - Phase 7: Obsidian-backed memory store.

One Markdown file per memory inside the vault folder configured by
"memory_vault_path" (browsable and editable in Obsidian), plus a SQLite
index over them (FTS5 where available, otherwise a LIKE scan). The index
is a cache, not the source of truth - reindex_all() rebuilds it from the
vault files at any time.

Frontmatter format (see implementation plan section 7.2):

    ---
    created: 2026-09-26T14:03:11
    type: note            # note | reminder | activity
    due: null             # ISO timestamp, only set for type: reminder
    tags: []
    source: mini-jarvis
    ---

    <the remembered text, verbatim>

Reminder fired-state lives in the DB (memory_fired table), never in the
file, so hand-editing in Obsidian never fights the scheduler.

Test standalone:  python -m mini_jarvis.memory.store
"""

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from mini_jarvis import config, events

_SLUG_WORDS = 6
_FILENAME_RE = re.compile(r"^\d{8}-\d{6}-.+\.md$")
_UNSAFE_SLUG_RE = re.compile(r"[^a-z0-9]+")
_DB_DIR_NAME = ".jarvis-index"
_DB_NAME = "memory.db"


@dataclass
class MemoryHit:
    """One note row from the index (or freshly read from the vault)."""

    path: str
    content: str
    tags: list[str] = field(default_factory=list)
    type: str = "note"
    due: str | None = None
    created: str = ""
    score: float = 0.0

    @property
    def due_dt(self) -> dt.datetime | None:
        if not self.due:
            return None
        try:
            return dt.datetime.fromisoformat(self.due)
        except ValueError:
            return None


class MemoryNotConfigured(Exception):
    """memory_vault_path is not set in config.json."""


def vault_configured() -> bool:
    """True when memory_vault_path is set (memory features usable)."""
    return bool(config.load().memory_vault_path)


def vault_dir() -> Path:
    """The configured vault folder; raises when memory is not set up."""
    path = config.load().memory_vault_path
    if not path:
        raise MemoryNotConfigured(
            "memory_vault_path is not set in config.json"
        )
    vault = Path(path).expanduser()
    vault.mkdir(parents=True, exist_ok=True)
    return vault


def _db_path() -> Path:
    configured = config.load().memory_db_path
    if configured:
        return Path(configured).expanduser()
    return vault_dir() / _DB_DIR_NAME / _DB_NAME


_db_ready: bool = False
_fts_ok: bool = False


def _fts_available(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("CREATE VIRTUAL TABLE temp.fts_probe USING fts5(x)")
        connection.execute("DROP TABLE temp.fts_probe")
        return True
    except sqlite3.Error:
        return False


def init_db() -> None:
    """Create the index schema. Idempotent; FTS5 probed once per process."""
    global _db_ready, _fts_ok
    if _db_ready:
        return
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=5.0) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        _fts_ok = _fts_available(connection)
        if _fts_ok:
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    path UNINDEXED, content, tags, type UNINDEXED,
                    due UNINDEXED, created UNINDEXED
                )
                """
            )
        else:
            events.emit("memory_notice", message="FTS5 unavailable - using LIKE scan")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_fts (
                    path TEXT PRIMARY KEY, content, tags, type, due, created
                )
                """
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_fired (
                path TEXT PRIMARY KEY, fired_at TEXT NOT NULL
            )
            """
        )
    _db_ready = True


def _connect() -> sqlite3.Connection:
    init_db()
    return sqlite3.connect(_db_path(), timeout=5.0)


def _slugify(content: str) -> str:
    words = re.findall(r"[a-z0-9]+", content.lower())
    slug = "-".join(words[:_SLUG_WORDS]) or "note"
    return _UNSAFE_SLUG_RE.sub("-", slug)[:60].strip("-") or "note"


def _timestamp_slug_now() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _frontmatter(
    created: str, note_type: str, due: str | None, tags: list[str]
) -> str:
    tags_text = "[" + ", ".join(tags) + "]" if tags else "[]"
    due_text = due if due else "null"
    return (
        "---\n"
        f"created: {created}\n"
        f"type: {note_type}\n"
        f"due: {due_text}\n"
        f"tags: {tags_text}\n"
        "source: mini-jarvis\n"
        "---\n"
    )


def _unique_path(vault: Path, stem: str) -> Path:
    candidate = vault / f"{stem}.md"
    counter = 2
    while candidate.exists():
        candidate = vault / f"{stem}-{counter}.md"
        counter += 1
    return candidate


def save(
    content: str,
    note_type: str = "note",
    due: str | None = None,
    tags: list[str] | None = None,
) -> Path:
    """Write one note file and index it incrementally."""
    content = content.strip()
    if not content:
        raise ValueError("cannot save an empty memory")
    vault = vault_dir()
    created = dt.datetime.now().isoformat(timespec="seconds")
    stem = f"{_timestamp_slug_now()}-{_slugify(content)}"
    path = _unique_path(vault, stem)
    path.write_text(
        _frontmatter(created, note_type, due, list(tags or [])) + "\n" + content + "\n",
        encoding="utf-8",
    )
    note = MemoryHit(
        path=str(path),
        content=content,
        tags=list(tags or []),
        type=note_type,
        due=due,
        created=created,
    )
    with _connect() as connection:
        _delete_row(connection, str(path))
        _insert_row(connection, note)
    events.emit("memory_saved", path=path.name, type=note_type)
    from mini_jarvis.memory import embeddings  # deferred: no import cycle

    embeddings.index_note(str(path), content)
    return path


def _insert_row(connection: sqlite3.Connection, note: MemoryHit) -> None:
    tags_text = " ".join(note.tags)
    connection.execute(
        "INSERT OR REPLACE INTO memory_fts "
        "(path, content, tags, type, due, created) VALUES (?, ?, ?, ?, ?, ?)",
        (note.path, note.content, tags_text, note.type, note.due, note.created),
    )


def _delete_row(connection: sqlite3.Connection, path: str) -> None:
    connection.execute("DELETE FROM memory_fts WHERE path = ?", (path,))


def _parse_note_file(path: Path) -> MemoryHit | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    front: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        lines = text.splitlines()
        try:
            end = lines.index("---", 1)
        except ValueError:
            end = -1
        if end > 0:
            for line in lines[1:end]:
                key, sep, value = line.partition(":")
                if sep:
                    front[key.strip()] = value.strip()
            body = "\n".join(lines[end + 1 :])
    content = body.strip()
    if not content:
        return None
    tags_text = front.get("tags", "[]").strip("[]").strip()
    tags = [t.strip() for t in tags_text.split(",") if t.strip()] if tags_text else []
    due = None if front.get("due", "null") in ("", "null") else front.get("due")
    return MemoryHit(
        path=str(path),
        content=content,
        tags=tags,
        type=front.get("type", "note") or "note",
        due=due,
        created=front.get("created", ""),
    )


_QUESTION_STOPWORDS = frozenset(
    "what did do does is was are the a an i my me you your about say said "
    "tell told know remember recall that this it of for to in on and or "
    "whats whose how why when where who".split()
)


def _query_tokens(query: str) -> list[str]:
    """Content words of a search query; question filler words dropped.

    Without this, "what did I say about the wifi password" matches any
    note containing "the" and a keyword miss never reaches the semantic
    tier.
    """
    tokens = []
    for token in query.split():
        cleaned = re.sub(r"[^\w]", "", token)
        if cleaned and cleaned.lower() not in _QUESTION_STOPWORDS:
            tokens.append(cleaned)
    return tokens


def _fts_match_expression(query: str) -> str:
    return " OR ".join(f'"{token}"' for token in _query_tokens(query))


def search_fts(
    query: str, top_k: int = 5, note_type: str | None = None
) -> list[MemoryHit]:
    """Keyword search over the index; best matches first."""
    query = (query or "").strip()
    if not query:
        return []
    with _connect() as connection:
        if _fts_ok:
            match = _fts_match_expression(query)
            if not match:
                return []
            parameters: list = [match]
            sql = (
                "SELECT path, content, tags, type, due, created, "
                "bm25(memory_fts) FROM memory_fts WHERE memory_fts MATCH ?"
            )
            if note_type:
                sql += " AND type = ?"
                parameters.append(note_type)
            sql += " ORDER BY rank LIMIT ?"
            parameters.append(top_k)
            rows = connection.execute(sql, parameters).fetchall()
        else:
            parameters = []
            sql = "SELECT path, content, tags, type, due, created FROM memory_fts"
            clauses = []
            for token in _fts_match_expression(query).split(" OR "):
                clauses.append("content LIKE ? OR tags LIKE ?")
                like = "%" + token.strip('"') + "%"
                parameters.extend([like, like])
            if note_type:
                clauses.append("type = ?")
                parameters.append(note_type)
            sql += " WHERE " + " AND ".join(f"({clause})" for clause in clauses)
            sql += " ORDER BY created DESC LIMIT ?"
            parameters.append(top_k)
            rows = connection.execute(sql, parameters).fetchall()
    hits: list[MemoryHit] = []
    for index, row in enumerate(rows):
        score = -float(row[6]) if _fts_ok and len(row) > 6 else float(len(rows) - index)
        hits.append(
            MemoryHit(
                path=row[0],
                content=row[1],
                tags=[t for t in (row[2] or "").split(" ") if t],
                type=row[3],
                due=row[4],
                created=row[5],
                score=score,
            )
        )
    return hits


def vault_notes() -> list[MemoryHit]:
    """Every note parsed straight from the vault files.

    The source of truth for recall and embeddings: notes hand-created in
    Obsidian appear here without a reindex, deleted files disappear even
    if the index is stale.
    """
    vault = vault_dir()
    notes: list[MemoryHit] = []
    for path in sorted(vault.rglob("*.md")):
        if _DB_DIR_NAME in path.parts:
            continue
        note = _parse_note_file(path)
        if note is not None:
            notes.append(note)
    return notes


def due_reminders(now: dt.datetime) -> list[MemoryHit]:
    """Reminders with due <= now that have not fired yet."""
    with _connect() as connection:
        rows = connection.execute(
            "SELECT path, content, tags, due, created FROM memory_fts "
            "WHERE type = 'reminder' AND due IS NOT NULL AND due != '' "
            "AND path NOT IN (SELECT path FROM memory_fired)"
        ).fetchall()
    hits: list[MemoryHit] = []
    for row in rows:
        hit = MemoryHit(
            path=row[0], content=row[1], tags=[], type="reminder",
            due=row[3], created=row[4],
        )
        due_dt = hit.due_dt
        if due_dt is not None and due_dt <= now:
            hits.append(hit)
    return hits


def mark_fired(path: str, when: dt.datetime) -> None:
    """Record that a reminder fired (DB-only state, never the .md file)."""
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO memory_fired (path, fired_at) VALUES (?, ?)",
            (path, when.isoformat(timespec="seconds")),
        )


def reindex_all() -> int:
    """Rebuild the index from the vault files; returns the note count."""
    notes = vault_notes()
    with _connect() as connection:
        connection.execute("DELETE FROM memory_fts")
        for note in notes:
            _insert_row(connection, note)
    events.emit("memory_notice", message=f"reindexed {len(notes)} note(s)")
    return len(notes)


def recent(n: int = 5) -> list[MemoryHit]:
    """The n most recently created notes."""
    with _connect() as connection:
        rows = connection.execute(
            "SELECT path, content, tags, type, due, created FROM memory_fts "
            "ORDER BY created DESC LIMIT ?",
            (n,),
        ).fetchall()
    return [
        MemoryHit(
            path=row[0],
            content=row[1],
            tags=[t for t in (row[2] or "").split(" ") if t],
            type=row[3],
            due=row[4],
            created=row[5],
        )
        for row in rows
    ]


def delete(path: str) -> None:
    """Remove one note from disk and from the index."""
    file_path = Path(path)
    file_path.unlink(missing_ok=True)
    with _connect() as connection:
        _delete_row(connection, str(file_path))
    events.emit("memory_notice", message=f"deleted {file_path.name}")
    from mini_jarvis.memory import embeddings  # deferred: no import cycle

    embeddings.forget(str(file_path))


def log_activity_line(description: str) -> Path:
    """Append one timestamped line to today's Activity/<date>.md note.

    The whole day is one note indexed with type: activity (section 7.7);
    every append rewrites the file and refreshes its single index row.
    """
    vault = vault_dir()
    folder = vault / "Activity"
    folder.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now()
    path = folder / f"{now:%Y-%m-%d}.md"
    line = f"{now:%H:%M} - {description}"
    existing = _parse_note_file(path) if path.exists() else None
    body = existing.content if existing else ""
    created = existing.created if existing else now.isoformat(timespec="seconds")
    path.write_text(
        _frontmatter(created, "activity", None, [])
        + "\n"
        + (body + "\n" if body else "")
        + line
        + "\n",
        encoding="utf-8",
    )
    refreshed = _parse_note_file(path)
    if refreshed is not None:
        with _connect() as connection:
            _delete_row(connection, str(path))
            _insert_row(connection, refreshed)
    return path


if __name__ == "__main__":
    try:
        vault_dir()
    except MemoryNotConfigured as error:
        print(f"[MEMORY] not configured: {error}")
        raise SystemExit(1)
    init_db()
    saved = [
        save("the wifi password is hunter2", tags=["wifi"]),
        save("project phoenix demo is on friday", tags=["work"]),
        save("call the dentist about the cleaning", note_type="reminder",
             due=(dt.datetime.now() + dt.timedelta(minutes=1)).isoformat(timespec="seconds")),
    ]
    for path in saved:
        print(f"[MEMORY] saved {path.name}")
    hits = search_fts("wifi password")
    print(f"[MEMORY] fts 'wifi password' -> {[h.path for h in hits]}")
    hits = search_fts("phoenix", note_type="reminder")
    print(f"[MEMORY] fts 'phoenix' reminders -> {len(hits)} (expect 0)")
    due = due_reminders(dt.datetime.now() + dt.timedelta(minutes=5))
    print(f"[MEMORY] due reminders -> {[h.content for h in due]}")
    mark_fired(due[0].path, dt.datetime.now())
    print(f"[MEMORY] after fire -> {len(due_reminders(dt.datetime.now() + dt.timedelta(minutes=5)))} (expect 0)")
    print(f"[MEMORY] reindex -> {reindex_all()} note(s)")
    print(f"[MEMORY] recent -> {[h.content for h in recent(2)]}")
