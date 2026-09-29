"""Mini Jarvis - Phase 9: own speech-to-text (main plan sections 9.0-9.2).

Replaces the clipboard/Handy input with a real microphone pipeline:

    mic -> ring buffer -> Silero VAD (ONNX, bundled with faster-whisper)
        -> completed speech segments -> faster-whisper 'small' -> transcript

Nothing here talks to the orchestrator: this module is the standalone
feed the plan's test order 9.4.1 asks for (python -m mini_jarvis.stt.feed
prints transcripts to the console first). io/stt_feed.py wraps it in the
same interface the clipboard feed exposes, so swapping the input source
later is a one-line change in the orchestrator.

VAD segmentation runs Silero over a rolling window every ~400ms: segments
whose end is already inside the buffer are closed and transcribed
immediately, a segment still touching the buffer edge is speech in
progress and waits for min_silence_duration_ms of trailing quiet (or is
force-cut at max_speech_duration_s so one long ramble can't grow forever).

Config (config.json): stt_model, stt_device, stt_compute_type,
stt_language, stt_input_device.
"""

import queue
import threading
import time

import numpy as np

from mini_jarvis import config, events

SAMPLE_RATE = 16000
BLOCK_SAMPLES = 1024          # 64ms per capture block, VAD works in 512s
WINDOW_S = 8.0                # rolling buffer kept while idle
MAX_SEGMENT_S = 20.0          # hard cap on one transcription
MIN_SILENCE_MS = 500          # trailing quiet that closes a segment
CHECK_EVERY_S = 0.4           # how often the VAD re-segments the buffer
MIN_TRANSCRIPT_CHARS = 1

# English-only by design for now: language is pinned from config ("en")
# and task is always "transcribe", so Whisper never switches to its
# translate behavior on non-English input. The prompt is the standard
# lever for proper-noun accuracy (app names, file names, commands) -
# override or blank it via stt_initial_prompt in config.json.
ENGLISH_ASSISTANT_PROMPT = (
    "English voice commands for a Windows desktop assistant. Open apps such as "
    "Chrome, Spotify, Notepad, Word, Edge, Excel, Docker, Terminal. Search the "
    "web, play videos on YouTube, set the volume, take a screenshot. Find, copy, "
    "move, delete, or create files on the desktop, in Documents, or in Downloads. "
    "Remember a note, set a reminder, and ask questions."
)


class _Capture:
    """Mic capture via sounddevice, with a PyAudio fallback.

    Both backends push float32 mono 16kHz blocks into a queue; the
    device is opened on a worker thread because the callback must not
    block.
    """

    def __init__(self, blocks: "queue.Queue[np.ndarray]") -> None:
        self._blocks = blocks
        self._backend = "none"
        self._stream = None

    def _resolve_device(self, wanted: str) -> str | int | None:
        if not wanted:
            return None
        if wanted.isdigit():
            return int(wanted)
        import sounddevice as sd

        for index, device in enumerate(sd.query_devices()):
            if wanted.lower() in str(device.get("name", "")).lower():
                return index
        return None

    def _open_sounddevice(self, device) -> None:
        import sounddevice as sd

        def callback(indata, _frames, _time, status):  # noqa: ARG001
            if status:
                events.emit("stt_status", message=f"mic: {status}")
            self._blocks.put(indata[:, 0].copy())

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCK_SAMPLES,
            device=device,
            callback=callback,
        )
        self._stream.start()
        self._backend = "sounddevice"

    def _open_pyaudio(self, device) -> None:
        import pyaudio

        pa = pyaudio.PyAudio()
        index = None
        if device is not None:
            if isinstance(device, int):
                index = device
            else:
                for i in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(i)
                    if device.lower() in str(info.get("name", "")).lower():
                        index = i
                        break
        self._stream = pa.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=BLOCK_SAMPLES,
            input_device_index=index,
        )

        def callback(in_data, _frame_count, _time_info, _status):  # noqa: ARG001
            self._blocks.put(
                np.frombuffer(in_data, dtype=np.float32).copy()
            )

        self._stream.start_stream(callback)
        self._backend = "pyaudio"

    def start(self, wanted: str) -> str:
        """Open the mic; returns the backend actually used ('none' on failure)."""
        device = self._resolve_device(wanted)
        for opener in (self._open_sounddevice, self._open_pyaudio):
            try:
                opener(device)
                return self._backend
            except Exception as error:  # noqa: BLE001 - any backend failure -> next
                events.emit("stt_status", message=f"mic via {opener.__name__} failed: {error}")
        return "none"

    def stop(self) -> None:
        try:
            if self._backend == "sounddevice":
                self._stream.stop()
                self._stream.close()
            elif self._backend == "pyaudio":
                self._stream.stop_stream()
                self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        self._backend = "none"


class SpeechSegmenter:
    """Rolling-buffer Silero VAD segmentation into completed utterances."""

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        window_s: float = WINDOW_S,
        max_segment_s: float = MAX_SEGMENT_S,
        min_silence_ms: int = MIN_SILENCE_MS,
        check_every_s: float = CHECK_EVERY_S,
    ) -> None:
        from faster_whisper import vad

        self._vad = vad
        self._rate = sample_rate
        self._max_samples = int(max_segment_s * sample_rate)
        self._window_samples = int(window_s * sample_rate)
        self._check_every = int(check_every_s * sample_rate)
        self._buffer = np.empty(0, dtype=np.float32)
        self._since_check = 0
        self._options = vad.VadOptions(
            threshold=0.5,
            min_speech_duration_ms=200,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=200,
            max_speech_duration_s=max_segment_s,
        )

    def feed(self, block: np.ndarray) -> list[np.ndarray]:
        """Add a capture block; return any speech segments that just closed."""
        self._buffer = np.concatenate(
            [self._buffer, block.astype(np.float32, copy=False)]
        )
        self._since_check += block.shape[0]
        if (
            self._since_check < self._check_every
            and self._buffer.shape[0] < self._max_samples
        ):
            return []
        self._since_check = 0
        return self._segment()

    def _segment(self) -> list[np.ndarray]:
        buffer = self._buffer
        try:
            timestamps = self._vad.get_speech_timestamps(
                buffer, self._options, self._rate
            )
        except Exception as error:  # noqa: BLE001
            events.emit("stt_status", message=f"vad error: {error}")
            self._buffer = np.empty(0, dtype=np.float32)
            return []

        if not timestamps:
            if buffer.shape[0] > self._window_samples:
                self._buffer = buffer[-self._window_samples :]
            return []

        edge = buffer.shape[0] - 512  # one VAD frame of slack
        segments: list[np.ndarray] = []
        cut = 0
        for entry in timestamps:
            begin = min(int(entry["start"]), buffer.shape[0])
            end = min(int(entry["end"]), buffer.shape[0])
            if end < edge:
                segments.append(buffer[begin:end])
                cut = end
        last = timestamps[-1]
        ongoing = int(last["end"]) >= edge
        if ongoing and buffer.shape[0] >= self._max_samples:
            begin = min(int(last["start"]), buffer.shape[0])
            segments.append(buffer[begin:])
            cut = buffer.shape[0]
        self._buffer = buffer[cut:]
        return segments

    def flush(self) -> np.ndarray | None:
        """Whatever is still buffered, for shutdown."""
        if self._buffer.shape[0] == 0:
            return None
        audio, self._buffer = self._buffer, np.empty(0, dtype=np.float32)
        return audio


class TranscriptSource:
    """Mic -> transcripts. Exposes (seq, text) pairs on .events, like the
    clipboard feed, so the orchestrator can consume either."""

    def __init__(self, on_transcript=None) -> None:
        self.events: "queue.Queue[tuple[int, str]]" = queue.Queue()
        self._on_transcript = on_transcript
        self._blocks: "queue.Queue[np.ndarray]" = queue.Queue()
        self._capture = _Capture(self._blocks)
        self._stop = threading.Event()
        self._seq = 0
        self._worker: threading.Thread | None = None

    def start(self) -> bool:
        """Load the model, open the mic, start the worker. False on failure."""
        cfg = config.load()
        if self._worker is not None and self._worker.is_alive():
            return True
        self._stop.clear()

        events.emit("stt_status", message=f"loading whisper model '{cfg.stt_model}'...")
        try:
            from faster_whisper import WhisperModel

            model = WhisperModel(
                cfg.stt_model,
                device=cfg.stt_device,
                compute_type=cfg.stt_compute_type,
            )
        except Exception as error:  # noqa: BLE001
            events.emit("stt_status", message=f"model load failed: {error}")
            return False

        events.emit("stt_status", message="opening microphone...")
        backend = self._capture.start(cfg.stt_input_device)
        if backend == "none":
            events.emit("stt_status", message="no usable microphone - stt feed off")
            return False
        events.emit(
            "stt_status",
            message=f"listening on {backend} ({cfg.stt_model}, {cfg.stt_device}) - speak now",
        )

        self._worker = threading.Thread(
            target=self._run, args=(model,), name="jarvis-stt", daemon=True
        )
        self._worker.start()
        return True

    def _run(self, model) -> None:
        cfg = config.load()
        segmenter = SpeechSegmenter()
        while not self._stop.is_set():
            try:
                block = self._blocks.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                for audio in segmenter.feed(block):
                    self._transcribe(model, audio, cfg)
            except Exception as error:  # noqa: BLE001
                # A VAD/segmentation hiccup must never kill transcription
                # for the rest of the session - report it and continue.
                events.emit("stt_status", message=f"segmentation error: {error}")
                segmenter = SpeechSegmenter()

    def _transcribe(self, model, audio: np.ndarray, cfg) -> None:
        started = time.time()
        try:
            segments, info = model.transcribe(
                audio,
                language=cfg.stt_language or None,
                task="transcribe",
                beam_size=1,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
                initial_prompt=cfg.stt_initial_prompt
                or ENGLISH_ASSISTANT_PROMPT,
            )
            text = " ".join(segment.text for segment in segments).strip()
        except Exception as error:  # noqa: BLE001
            events.emit("stt_status", message=f"transcribe failed: {error}")
            return
        if len(text) < MIN_TRANSCRIPT_CHARS:
            return
        self._seq += 1
        latency = time.time() - started
        events.emit(
            "stt_transcript",
            text=text,
            seconds=round(audio.shape[0] / SAMPLE_RATE, 2),
            latency=round(latency, 2),
        )
        if self._on_transcript is not None:
            self._on_transcript(text)
        self.events.put((self._seq, text))

    def stop(self) -> None:
        self._stop.set()
        self._capture.stop()
        self._worker = None


if __name__ == "__main__":
    source = TranscriptSource()
    if not source.start():
        raise SystemExit(1)
    print("[STT] speak normally; Ctrl+C to stop", flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        source.stop()
        print("\n[STT] stopped", flush=True)
