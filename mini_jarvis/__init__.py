"""Mini Jarvis - a fully local, voice-controlled desktop assistant.

Package layout:
    core/   orchestrator + safety gate
    nlu/    classifier (rules + Laya), slots, planner
    io/     input feeds (clipboard today; own STT in Phase 9)
    tools/  the tool registry and plain-Python tools
    memory/ Obsidian-backed memory (Phase 7)

Run with:  python main.py
"""
