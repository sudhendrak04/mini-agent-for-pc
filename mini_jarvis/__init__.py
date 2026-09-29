"""Mini Jarvis - a fully local, voice-controlled desktop assistant.

Package layout:
    core/   orchestrator + safety gate
    nlu/    classifier (rules + Laya), slots, slot filler, planner
    io/     input feeds (own STT microphone feed)
    stt/    mic -> Silero VAD -> faster-whisper pipeline (Phase 9)
    tools/  the tool registry and plain-Python tools
    memory/ Obsidian-backed memory (Phase 7)

Run with:  python main.py
"""
