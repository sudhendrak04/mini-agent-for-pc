"""Mini Jarvis - Phase 7: the ASK intent's unified answer pipeline.

Three tiers, tried in order:

  tier 1  keyword FTS over the vault - fast, exact wording
  tier 2  embedding cosine search - paraphrases the keywords miss
  tier 3  general knowledge straight from the local model

Tier 1 and 2 both run the retrieved notes through one Ollama chat call
("answer using ONLY these notes"), which keeps answers honest about what
came from Nitro's own memory. With memory_synthesize=false in config,
tiers 1/2 skip the LLM and return the raw matching notes instead. Tier 3
always needs the model - there is nothing to fall back to.

Test standalone:  python -m mini_jarvis.memory.recall "what did I say about X"
"""

import json
import sys
import urllib.request

from mini_jarvis import config, events
from mini_jarvis.memory import embeddings, store

OLLAMA_CHAT = "http://localhost:11434/api/chat"
REQUEST_TIMEOUT = 90.0
MAX_SUMMARY_TOKENS = 300

_RAG_PROMPT = """You are the memory unit of a local Windows voice assistant.
Answer the user's question using ONLY the saved memory notes below.
If the notes do not contain the answer, say exactly that in one short
sentence. Keep the answer to one or two sentences, spoken style.

Memory notes:
{notes}"""

_GENERAL_PROMPT = """You are a concise local voice assistant.
Answer the question in one or two short sentences, spoken style."""


def _chat(system: str, user: str) -> str:
    """One free-text answer from Ollama. Raises on network errors."""
    payload = {
        "model": config.load().planner_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": MAX_SUMMARY_TOKENS},
    }
    request = urllib.request.Request(
        OLLAMA_CHAT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        data = json.loads(response.read())
    return data.get("message", {}).get("content", "").strip()


def _raw_matches(hits: list[store.MemoryHit]) -> str:
    lines = []
    for hit in hits:
        line = hit.content.replace("\n", " ")
        lines.append(f"- {line}")
    return "\n".join(lines)


def _summarize(query: str, hits: list[store.MemoryHit]) -> str:
    notes_block = "\n".join(f"- {hit.content}" for hit in hits)
    system = _RAG_PROMPT.replace("{notes}", notes_block)
    return _chat(system, query)


def _present(query: str, hits: list[store.MemoryHit]) -> str:
    """Tier 1/2 presentation: synthesized unless configured otherwise."""
    if not config.load().memory_synthesize:
        return _raw_matches(hits)
    try:
        return _summarize(query, hits)
    except (OSError, ValueError):
        return _raw_matches(hits)


def _general_answer(query: str) -> str:
    try:
        return _chat(_GENERAL_PROMPT, query)
    except (OSError, ValueError) as error:
        return f"could not answer - the local model is unreachable ({error})"


def answer(query: str) -> str:
    """Memory-first answer: FTS, then semantic, then general knowledge."""
    query = (query or "").strip()
    if not query:
        return "I did not catch a question in that."
    hits = store.search_fts(query, top_k=5)
    if hits:
        events.emit("recall_tier", tier=1, source="keyword", hits=len(hits))
        return _present(query, hits)
    hits = embeddings.semantic_search(query, top_k=5)
    if hits:
        events.emit("recall_tier", tier=2, source="semantic", hits=len(hits))
        return _present(query, hits)
    events.emit("recall_tier", tier=3, source="general")
    return _general_answer(query)


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "what did I say about the wifi password"
    print(f"[RECALL] question: {query}")
    print(answer(query))
