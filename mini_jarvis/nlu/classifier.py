"""Mini Jarvis - Phase 2: intent classification.

Two-stage classifier:

1. Deterministic rules (slots.rule_classify) catch lexically unambiguous
   commands ("play X on youtube", "google X", "set volume to N", ...) -
   zero latency, zero model cost, exact intent.
2. Everything ambiguous goes to Laya, a local "System 1" decision model on
   ONNX Runtime. Laya does not generate text: it receives the transcript
   plus a choice question over a fixed intent list and returns calibrated
   probabilities per option in one forward pass.

Laya runs in a persistent Node.js subprocess (laya_worker.mjs) so the
~1.7 GB weights load once and inference stays warm. If the worker cannot
start or stops answering, this module degrades gracefully: transcripts
route to COMPLEX_TASK instead of crashing the assistant.

The intent list itself lives in intent_schema.py (the single source of
truth); this module only supplies Laya with its criteria text.

Prompt design note: criteria wording was tuned empirically (see
laya_experiment*.mjs history) - terse criteria + JSON state
({"transcript": ...}) scored dramatically better than long descriptions.

Test standalone:  python -m mini_jarvis.nlu.classifier "open chrome" "google python asyncio"
"""

import atexit
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from mini_jarvis import config, events
from mini_jarvis.intent_schema import INTENTS
from mini_jarvis.nlu.slots import normalize_command, rule_classify

PROJECT_ROOT = config.PROJECT_ROOT

# The fixed intent list: criteria text Laya scores each option against.
INTENT_OPTIONS = {name: spec.criteria for name, spec in INTENTS.items()}

# The question header Laya scores the options against.
QUESTION_INSTRUCTIONS = "Classify this voice command for a desktop assistant."

COMPLEX_TASK = "COMPLEX_TASK"


def _weights_cached() -> bool:
    cache = Path.home() / ".cache" / "receptron-laya"
    try:
        return cache.exists() and any(cache.iterdir())
    except OSError:
        return False


class LayaClassifier:
    """Speaks JSON lines to laya_worker.mjs and classifies transcripts."""

    def __init__(self, threshold: float | None = None, timeout: float = 30.0):
        self.threshold = (
            config.load().confidence_threshold if threshold is None else threshold
        )
        self.timeout = timeout
        self.available = False
        self.proc = None
        self._responses: dict[object, dict] = {}
        self._send_lock = threading.Lock()
        self._next_id = 1
        self._stderr_log = None

    def start(self) -> bool:
        """Spawn the worker and wait for the model to be ready."""
        if self.proc is not None and self.proc.poll() is None:
            return True

        events.emit(
            "laya_status",
            message=(
                "starting worker (loading cached weights)"
                if _weights_cached()
                else "starting worker (first run downloads ~1.7 GB)..."
            ),
        )
        started = time.time()
        self._stderr_log = open(PROJECT_ROOT / "laya_worker.log", "w", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(
                ["node", "laya_worker.mjs"],
                cwd=str(PROJECT_ROOT),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr_log,
                text=True,
                encoding="utf-8",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            events.emit("laya_status", message=f"could not start node worker: {e}")
            return False

        atexit.register(self.stop)
        threading.Thread(target=self._read_loop, daemon=True).start()

        if self._wait_for_ready(max_wait=900.0):
            self.available = True
            events.emit(
                "laya_status", message=f"ready in {time.time() - started:.1f}s"
            )
            return True
        events.emit(
            "laya_status", message="worker did not become ready; see laya_worker.log"
        )
        return False

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            self.proc.terminate()
        if self._stderr_log is not None:
            self._stderr_log.close()
            self._stderr_log = None

    def _read_loop(self) -> None:
        """Read worker stdout lines in the background, filed by request id."""
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg:
                self._responses[msg["id"]] = msg
            elif msg.get("ready"):
                self._responses["ready"] = True

    def _wait_for_ready(self, max_wait: float) -> bool:
        deadline = time.time() + max_wait
        while time.time() < deadline:
            if self._responses.get("ready"):
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.5)
        return False

    def classify(self, text: str) -> tuple[str, float, str]:
        """Return (intent, confidence, source).

        source is "rule" (deterministic match) or "laya" (model decision).
        Falls back to COMPLEX_TASK whenever Laya is unavailable or its top
        probability is under the confidence threshold.
        """
        text = normalize_command(text)

        rule = rule_classify(text)
        if rule is not None:
            intent, _slots = rule
            return intent, 0.99, "rule"

        if not self.available and not self.start():
            return COMPLEX_TASK, 0.0, "laya"
        if self.proc.poll() is not None:
            # Worker died - try one restart before giving up.
            events.emit("laya_status", message="worker exited; restarting...")
            self.available = False
            if not self.start():
                return COMPLEX_TASK, 0.0, "laya"

        with self._send_lock:
            request_id = self._next_id
            self._next_id += 1
            request = {
                "id": request_id,
                "state": {"transcript": text},
                "instructions": QUESTION_INSTRUCTIONS,
                "options": INTENT_OPTIONS,
            }
            try:
                self.proc.stdin.write(json.dumps(request) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                events.emit("laya_status", message="failed to send request to worker")
                self.available = False
                return COMPLEX_TASK, 0.0, "laya"

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            response = self._responses.pop(request_id, None)
            if response is not None:
                if "error" in response:
                    events.emit(
                        "laya_status",
                        message=f"request failed: {response['error']}",
                    )
                    return COMPLEX_TASK, 0.0, "laya"
                probabilities = response.get("probabilities", {})
                if not probabilities:
                    return COMPLEX_TASK, 0.0, "laya"
                intent, confidence = max(probabilities.items(), key=lambda kv: kv[1])
                if confidence < self.threshold:
                    return COMPLEX_TASK, confidence, "laya"
                return intent, confidence, "laya"
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)

        events.emit(
            "laya_status", message="no response in time; routing to COMPLEX_TASK"
        )
        self.available = False
        return COMPLEX_TASK, 0.0, "laya"


if __name__ == "__main__":
    from mini_jarvis.nlu.slots import extract_slots

    phrases = sys.argv[1:] or ["open chrome"]
    classifier = LayaClassifier()
    classifier.start()
    for phrase in phrases:
        intent, confidence, source = classifier.classify(phrase)
        slots = extract_slots(intent, phrase)
        print(f"{phrase!r} -> {intent} {confidence:.2f} [{source}] slots={slots}", flush=True)
    classifier.stop()
