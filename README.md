# Mini Jarvis

A fully local, voice-controlled Windows desktop assistant. It listens through
your microphone, works out what you meant, asks before anything risky, and
shows what it is doing on a small floating orb.

Everything runs on your machine: speech recognition, intent classification,
planning, memory, and the UI. No cloud services, no API keys.

```
speak  ->  mic  ->  Silero VAD  ->  faster-whisper  ->  rules / Laya
                                                         |
                                       regex slots (+ LLM slot filler)
                                                         |
                                        safety gate  ->  tool registry
                                                         |
                       apps, browser, volume, files, typing, memory, reminders
```

A floating orb, fed by the same event stream the console prints, mirrors the
assistant's state in real time.

---

## What it does

| | |
|---|---|
| **Open things** | apps (`open chrome`), websites (`open youtube`, `open github.com`), files/folders |
| **Search** | web (`google python asyncio`), YouTube (`play lofi beats on youtube`), files (`find report.docx`) |
| **Control** | volume (`set volume to 30`), screenshots, typing into any window (`type hello in notepad`) |
| **File operations** | find, create, copy, move, delete — with a "which file did you mean?" step that refuses to guess |
| **Memory** | `remember that the demo is on friday` — stored as Markdown in your Obsidian vault, searchable by keyword and meaning |
| **Reminders** | `remind me to call mom at 5pm` — fires a Windows toast while the assistant runs |
| **Questions** | `what did I say about the demo` (answers from your memory) or `what is the capital of france` (general knowledge) |
| **Compound commands** | `open notepad and set volume to 40` — split and run; anything ambiguous goes to a local LLM planner |
| **Safety** | every risky action needs a spoken or typed `yes` |
| **Orb UI** | frameless always-on-top window that animates for listening / transcribing / thinking / confirming / acting |

---

## Requirements

- **Windows 10 or 11** (COM audio, `pycaw`, and `pyautogui` are Windows-only)
- **Python 3.11** (tested on 3.11.15)
- **Node.js 20+** (tested on 24.19) — for the Laya classifier worker and the orb's Electron shell
- **[Ollama](https://ollama.com)** with two models: `llama3:latest` and `nomic-embed-text`
- A microphone
- Disk: budget ~15 GB — Ollama models are the bulk (~13 GB on the reference
  machine, which also has other models), plus Laya weights ~1.7 GB,
  Whisper `small` ~0.5 GB, Electron ~0.3 GB

---

## Running it (already set up)

Two terminals.

**1. The assistant**
```powershell
cd "D:\path\to\mini-jarvis"
python main.py
```

**2. The orb**
```powershell
cd "D:\path\to\mini-jarvis\mini-jarvis-orb"
npm run dev:electron
```

Order doesn't matter: if the orb starts first it shows "Mini Jarvis offline"
and reconnects on its own (within ~15 s) once the assistant is up. Stop both
with `Ctrl+C`.

---

## Setting it up on another Windows machine

### 1. Install the prerequisites

- Python 3.11 — <https://www.python.org/downloads/windows/> ("Add to PATH")
- Node.js LTS — <https://nodejs.org/>
- Ollama — <https://ollama.com/download>, then pull the two models:
  ```powershell
  ollama pull llama3:latest
  ollama pull nomic-embed-text
  ```

### 2. Get the code

```powershell
git clone <your-repo-url> mini-jarvis
cd mini-jarvis
```

### 3. Python dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 4. Whisper speech model

faster-whisper can fetch it itself:

```powershell
python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
```

If that stalls partway (some networks throttle `huggingface_hub`), download the
four files with `curl` into a local folder instead — this is what the original
setup used:

```powershell
mkdir models\faster-whisper-small
$base = "https://huggingface.co/Systran/faster-whisper-small/resolve/main"
curl -L -o models\faster-whisper-small\config.json    "$base/config.json"
curl -L -o models\faster-whisper-small\tokenizer.json "$base/tokenizer.json"
curl -L -o models\faster-whisper-small\vocabulary.txt "$base/vocabulary.txt"
curl -L -o models\faster-whisper-small\model.bin      "$base/model.bin"
```

Then point `stt_model` at that folder in `config.json` (step 6).

### 5. Node dependencies

Two separate installs — the project root (Laya worker) and the orb:

```powershell
npm install                                        # Laya worker
cd mini-jarvis-orb
npm install
cd ..
```

If npm reports blocked install scripts, approve them (Electron and
onnxruntime need their postinstall steps):

```powershell
npm approve-scripts onnxruntime-node     # in the project root
cd mini-jarvis-orb
npm approve-scripts electron esbuild
cd ..
```

### 6. Configuration

`config.json` is intentionally **not** committed (it holds your absolute
paths). Start from the example and edit it:

```powershell
copy config.example.json config.json
notepad config.json
```

At minimum set:

| Key | Meaning |
|---|---|
| `search_paths` | folders the file tools may search |
| `memory_vault_path` | an empty folder for Jarvis's notes (e.g. a subfolder of your Obsidian vault) — created automatically |
| `stt_model` | the Whisper folder from step 4 |

Everything else has a working default. Full reference at the bottom.

### 7. First run

```powershell
python main.py
```

The first launch downloads the Laya classifier weights (~1.7 GB, one time)
and prints `[LAYA] starting worker (first run downloads ~1.7 GB)...`. After
that, startup is a few seconds.

Start the orb in a second terminal and you're live.

---

## Commands

**Assistant and components**

| Command | What it does |
|---|---|
| `python main.py` | the assistant |
| `python -m mini_jarvis.stt.feed` | speech-to-text only — prints transcripts with audio/latency timings, no tools |
| `python -m mini_jarvis.nlu.slots` | slot-extraction smoke test (no models needed) |
| `python -m mini_jarvis.memory.store` | saves fake notes into the vault and exercises the index |
| `python -m mini_jarvis.memory.recall "what did I say about X"` | three-tier recall: keyword → semantic → general knowledge |
| `python -m mini_jarvis.memory.reminders` | sets a reminder 30 s out and watches the scheduler fire |
| `python -m mini_jarvis.tools.files` | create/copy/move/delete demo in a temp folder, including the "multiple matches → refuse" case |
| `python -c "from mini_jarvis.memory import store; store.reindex_all()"` | rebuild the search index from the vault (run after deleting notes in Obsidian) |

**Orb** (in `mini-jarvis-orb/`)

| Command | What it does |
|---|---|
| `npm run dev` | widget only, in a browser tab |
| `npm run demo` | throwaway WebSocket server cycling every orb state — lets you test the UI with no assistant running |
| `npm run dev:electron` | the real floating orb |
| `npm run build` | production bundle |
| `npm start` | run the built orb |

---

## How it works

**The pipeline** — one path, no shortcuts around the safety gate:

1. **Capture** — `sounddevice` streams 16 kHz mono audio (PyAudio fallback)
2. **VAD** — Silero (ONNX, bundled with faster-whisper) segments speech; silence
   is never sent to the model, which is what stops Whisper's "thank you"
   hallucinations
3. **Transcription** — faster-whisper `small`, int8 on CPU, English-only, with
   a domain prompt (`stt_initial_prompt`) that teaches it the assistant's
   vocabulary
4. **Classification** — deterministic regex rules first (zero latency); Laya
   (a local ONNX "System 1" classifier) for everything else
5. **Slot extraction** — regexes per intent; if Laya was confident but a
   required slot is missing, one small Ollama call fills it
6. **Planning** — compound or unclassifiable commands go to Ollama, which
   returns JSON steps that run back through the same registry
7. **Safety** — SAFE runs, SENSITIVE and DANGEROUS require a spoken or typed
   `yes`
8. **Tools** — plain Python functions behind one registry

**Safety levels**

| Level | Behaviour | Examples |
|---|---|---|
| `SAFE` | runs immediately | open app, web search, volume, screenshot, find, create, memory save, ask |
| `SENSITIVE` | confirmation required | typing into a window, copy, move |
| `DANGEROUS` | always confirmation, no bypass | delete |

A spoken "yes" during a prompt answers the confirmation instead of becoming a
command. Ambiguous file targets never resolve to a guess: one match proceeds,
zero or several refuse and list what they found.

**Memory** — one Markdown file per memory in your vault, with YAML frontmatter
(`type`, `due`, `tags`, `created`), so you can read, edit, or tag anything in
Obsidian. A SQLite FTS5 index (with a `LIKE` fallback) provides keyword search;
`nomic-embed-text` embeddings provide semantic search. Deleting notes in
Obsidian and re-running `reindex_all()` clears them from search.

**The orb** — a second subscriber to the same event stream the console prints.
A small WebSocket broadcaster translates events into six states (`idle`,
`listening`, `transcribing`, `thinking`, `confirming`, `acting`); the orb only
renders what it is told. The port comes from `config.json`, so the two halves
can't drift apart.

**Project layout**

```
mini-jarvis/
  main.py                     launcher
  config.json                 your settings (gitignored; see config.example.json)
  mini_jarvis/
    config.py                 one cached, typed config loader
    events.py                 event stream + console presenter
    intent_schema.py          every intent, slot, tool and safety level in one place
    core/                     orchestrator, safety gate, orb broadcaster
    nlu/                      classifier (rules + Laya), slots, slot filler, planner
    stt/                      mic -> VAD -> Whisper pipeline
    io/                       feed adapters
    tools/                    the tool registry and every tool
    memory/                   Obsidian store, embeddings, recall, reminders, activity log
  mini-jarvis-orb/            Electron + React orb (separate npm project)
  models/                     Whisper weights (gitignored)
  laya_worker.mjs             persistent Node worker hosting the Laya model
```

**Development phases** — the assistant was built in order: Phase 0 (unify the
intent schema, config loader, event stream), Phase 7 (memory, reminders,
Q&A), Phase 8 (file create/copy/move/delete), Phase 9 (own speech-to-text +
the orb). The original design documents are the project brief.

---

## Configuration reference

| Key | Default | Meaning |
|---|---|---|
| `confidence_threshold` | `0.6` | below this Laya confidence, the command goes to the planner |
| `planner_model` | `llama3:latest` | Ollama model for planning, recall answers and slot filling |
| `search_paths` | Desktop/Documents/Downloads | roots the file tools may search |
| `memory_vault_path` | *(empty)* | folder for notes/reminders; empty disables memory |
| `memory_db_path` | *(empty)* | index location; empty puts it inside the vault |
| `embedding_model` | `nomic-embed-text` | Ollama embedding model for semantic recall |
| `memory_synthesize` | `true` | `false` returns raw matching notes instead of a written answer (faster) |
| `memory_log_activity` | `false` | log every tool run to `Activity/<date>.md` in the vault |
| `reminder_poll_seconds` | `30` | how often due reminders are checked |
| `reminder_startup_grace_minutes` | `10` | reminders overdue by more than this are marked fired silently at startup |
| `stt_model` | `small` | Whisper model name, or a path to a local model folder |
| `stt_device` | `cpu` | `cpu` or `cuda` |
| `stt_compute_type` | `int8` | `int8`, `float16`, … (int8 is fastest on CPU) |
| `stt_language` | `en` | `""` for auto-detect |
| `stt_input_device` | *(empty)* | mic name substring or index; empty = system default |
| `stt_initial_prompt` | *(empty)* | extra words to prime Whisper with (names, projects); empty = built-in assistant prompt |
| `orb_websocket_port` | `8765` | port the broadcaster serves and the orb connects to |

---

## Troubleshooting

**`[STT] no usable microphone - stt feed off`**
No input device opened. Check Windows privacy settings (microphone access),
or pin a specific mic with `stt_input_device` (try a name fragment like
`"usb"` or a device index).

**Transcripts appear while you're not talking**
Background audio (music, TV) is being picked up. Test in a quiet room. A wake
word would be the usual fix but is not implemented.

**`Ollama is not reachable` / answers fail**
Ollama isn't running, or the models aren't pulled:
`ollama serve` then `ollama list`. Memory saving and file tools work without
Ollama; planning, questions, and slot filling do not.

**Whisper download stalls at a few MB**
`huggingface_hub` is throttled on your network. Use the `curl` recipe in step 4.

**npm warns about blocked install scripts**
`npm approve-scripts electron esbuild` (in `mini-jarvis-orb/`) and
`npm approve-scripts onnxruntime-node` (in the project root).

**Orb shows "Mini Jarvis offline"**
The assistant isn't running, or it's broadcasting on a different port. The orb
reads `orb_websocket_port` from the project root `config.json`; both must
match. The orb retries on its own every few seconds.

**Transcripts are inaccurate on proper nouns**
Add the words to `stt_initial_prompt` (fastest fix), or move to a bigger model
by pointing `stt_model` at a `medium.en`/`large-v3` folder — see below.

**Deleted notes still show up in search**
The index is a cache. Run `reindex_all()` (command above) after deleting files
in Obsidian.

---

## Known limits

- **Windows only** — the audio, COM volume control, and input tooling are
  Windows-specific.
- **English-only STT** for now; translation behaviour is explicitly disabled
  so non-English speech is never silently converted.
- **No text-to-speech** — the orb and console notify you, nothing speaks.
- **No wake word** — the assistant reacts to any speech the VAD hears.
- **Whisper `small`** by default: fast, but proper nouns need the prompt (or a
  larger model). To upgrade, download `Systran/faster-whisper-medium.en` (or
  `large-v3`) the same way as step 4 and point `stt_model` at the folder.

## License

MIT
