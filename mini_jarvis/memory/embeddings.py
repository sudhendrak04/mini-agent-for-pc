"""Mini Jarvis - Phase 7: semantic fallback for recall (tier 2).

Keyword search (store.search_fts) misses paraphrases ("what did I say
about the launch plan" vs a note that says "demo is on friday"). This
module embeds each note once - at save time, cached on disk next to the
DB - and on a keyword miss compares the query embedding against every
cached note vector with cosine similarity. A small vector cache is all
this scale (hundreds to low thousands of notes) needs; no vector DB.

The embedding model comes from config ("embedding_model", default
nomic-embed-text - pull once with `ollama pull nomic-embed-text`).
Every Ollama call fails soft: when the server is down, semantic search
returns [] and recall falls through to tier 3.
"""

import math
import pickle
import urllib.request
from pathlib import Path

from mini_jarvis import config
from mini_jarvis.memory import store

OLLAMA_EMBEDDINGS = "http://localhost:11434/api/embeddings"
REQUEST_TIMEOUT = 60.0
CACHE_NAME = "embeddings.pkl"
MIN_SIMILARITY = 0.5

try:
    import numpy as _np
except ImportError:  # cosine still works in pure Python at this scale
    _np = None


def _cache_path() -> Path:
    configured = config.load().memory_db_path
    if configured:
        return Path(configured).expanduser().parent / CACHE_NAME
    return store.vault_dir() / store._DB_DIR_NAME / CACHE_NAME


def _load_cache() -> dict[str, list[float]]:
    try:
        with open(_cache_path(), "rb") as f:
            data = pickle.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, pickle.UnpicklingError):
        return {}


def _write_cache(cache: dict[str, list[float]]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "wb") as f:
            pickle.dump(cache, f)
    except OSError:
        pass


def embed(text: str) -> list[float] | None:
    """One embedding vector from Ollama, or None when unreachable."""
    payload = {
        "model": config.load().embedding_model,
        "prompt": text,
    }
    request = urllib.request.Request(
        OLLAMA_EMBEDDINGS,
        data=_json_bytes(payload),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            data = _json_loads(response.read())
    except (OSError, ValueError):
        return None
    vector = data.get("embedding")
    return vector if isinstance(vector, list) and vector else None


def _json_bytes(payload: dict) -> bytes:
    import json

    return json.dumps(payload).encode("utf-8")


def _json_loads(raw: bytes) -> dict:
    import json

    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _cosine(a: list[float], b: list[float]) -> float:
    if _np is not None:
        va, vb = _np.asarray(a, dtype=float), _np.asarray(b, dtype=float)
        norm = float(_np.linalg.norm(va) * _np.linalg.norm(vb))
        return float(_np.dot(va, vb) / norm) if norm else 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def index_note(path: str, content: str) -> None:
    """Compute and cache one note's embedding at save time. Fails soft."""
    vector = embed(content)
    if vector is None:
        return
    cache = _load_cache()
    cache[path] = vector
    _write_cache(cache)


def forget(path: str) -> None:
    """Drop one note's cached embedding (after a vault delete)."""
    cache = _load_cache()
    if path in cache:
        del cache[path]
        _write_cache(cache)


def ensure_indexed() -> dict[str, list[float]]:
    """Cache with every vault note embedded; embeds any missing note once."""
    cache = _load_cache()
    changed = False
    for note in store.vault_notes():
        if note.path not in cache:
            vector = embed(note.content)
            if vector is not None:
                cache[note.path] = vector
                changed = True
    if changed:
        _write_cache(cache)
    return cache


def semantic_search(query: str, top_k: int = 5) -> list[store.MemoryHit]:
    """Cosine-similarity search over the cached note embeddings."""
    query_vector = embed(query)
    if query_vector is None:
        return []
    cache = ensure_indexed()
    scored: list[tuple[float, str]] = []
    for path, vector in cache.items():
        similarity = _cosine(query_vector, vector)
        if similarity >= MIN_SIMILARITY:
            scored.append((similarity, path))
    scored.sort(reverse=True)
    vault = {note.path: note for note in store.vault_notes()}
    hits: list[store.MemoryHit] = []
    for similarity, path in scored[:top_k]:
        note = vault.get(path)
        if note is not None:
            note.score = similarity
            hits.append(note)
    return hits
