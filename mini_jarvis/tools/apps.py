"""Mini Jarvis - Phase 3: application launcher.

Finds and starts Windows applications by (possibly fuzzy) name.
Resolution order:

1. executables on PATH (notepad, mspaint, ...)
2. exact Start Menu shortcut match   (Google Chrome.lnk)
3. substring Start Menu match        (chrome -> Google Chrome.lnk)
4. `cmd /c start`                    (uses the App Paths registry:
                                      chrome, spotify, firefox, ...)
5. ToolFailure listing the closest Start Menu names found
"""

import os
import shutil
import subprocess
import time
from difflib import get_close_matches
from pathlib import Path

from mini_jarvis.tools import ToolFailure

_START_MENU_DIRS = [
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    Path(os.environ.get("APPDATA", ""))
    / "Microsoft" / "Windows" / "Start Menu" / "Programs",
]

# Apps whose spoken name differs from their process name
_APP_PROCESS_NAMES = {
    "file explorer": "explorer",
    "word": "winword",
    "edge": "msedge",
    "chrome": "chrome",
    "notepad": "notepad",
    "terminal": "windowsterminal",
}


def _lnk_target_process(shortcut: Path) -> str:
    """Resolve a .lnk to its target exe name (for focusing after launch)."""
    script = (
        "(New-Object -ComObject WScript.Shell).CreateShortcut("
        f"'{shortcut}').TargetPath"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        target = result.stdout.strip()
        if target:
            return Path(target).stem
    except (OSError, subprocess.TimeoutExpired):
        pass
    return shortcut.stem


def focus_app(app_name: str, timeout: float = 8.0) -> None:
    """Best-effort: bring an app's main window to the foreground.

    Windows does not always give a freshly launched window focus (the
    foreground-lock applies, e.g. when the user is typing in a console).
    Tools that type afterwards depend on the window being focused, so we
    force it via WScript.Shell.AppActivate.
    """
    key = app_name.strip().lower()
    process = _APP_PROCESS_NAMES.get(key, key.split()[0] if key.split() else key)
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$deadline=(Get-Date).AddSeconds(5);"
        f"$name='{process}';"
        "do {"
        "  $p = Get-Process -Name $name -ErrorAction SilentlyContinue |"
        "  Where-Object { $_.MainWindowTitle } | Select-Object -First 1;"
        "  if ($p) { break };"
        "  Start-Sleep -Milliseconds 300"
        "} while ((Get-Date) -lt $deadline);"
        "if ($p) { (New-Object -ComObject WScript.Shell).AppActivate($p.Id) | Out-Null }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        pass  # best effort only

# Built once per session; rglob over Start Menu is not free.
_shortcuts: list[Path] | None = None


def _all_shortcuts() -> list[Path]:
    global _shortcuts
    if _shortcuts is None:
        _shortcuts = []
        for base in _START_MENU_DIRS:
            if base.is_dir():
                _shortcuts.extend(base.rglob("*.lnk"))
    return _shortcuts


def _close_matches(name: str) -> list[str]:
    stems = [p.stem.lower() for p in _all_shortcuts()]
    return get_close_matches(name.lower(), stems, n=3, cutoff=0.4)


# Names people say that map to a different real app name
_ALIASES = {
    "file manager": "file explorer",
    "explorer": "file explorer",
    "terminal": "terminal",
    "cmd": "command prompt",
}

# Apps launched in this session recently (normalized name -> timestamp).
# Lets "open notepad and type hello" type into the just-launched notepad
# without launching a second copy.
_RECENTLY_LAUNCHED: dict[str, float] = {}
_RECENT_WINDOW = 60.0  # seconds


def _remember_launch(app_name: str) -> None:
    _RECENTLY_LAUNCHED[app_name.strip().lower()] = time.time()


def was_recently_launched(app_name: str) -> bool:
    key = app_name.strip().lower()
    timestamp = _RECENTLY_LAUNCHED.get(key)
    return timestamp is not None and (time.time() - timestamp) <= _RECENT_WINDOW


def open_app(name: str) -> str:
    """Start a desktop application by name. Returns a confirmation string."""
    name = (name or "").strip()
    if not name:
        raise ToolFailure("no application name given")
    name = _ALIASES.get(name.lower(), name)

    # 1. On PATH
    exe = shutil.which(name)
    if exe:
        subprocess.Popen([exe], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _remember_launch(name)
        focus_app(Path(exe).stem)
        return f"launched {name}"

    target = name.lower()
    shortcuts = _all_shortcuts()

    # 2. Exact Start Menu shortcut name
    for shortcut in shortcuts:
        if shortcut.stem.lower() == target:
            os.startfile(shortcut)
            _remember_launch(name)
            focus_app(_lnk_target_process(shortcut))
            return f"launched {shortcut.stem}"

    # 3. Substring match, shortest stem first
    #    (prefers "Google Chrome" over "Google Chrome Beta")
    matches = [s for s in shortcuts if target in s.stem.lower()]
    if matches:
        matches.sort(key=lambda p: len(p.stem))
        os.startfile(matches[0])
        _remember_launch(name)
        focus_app(_lnk_target_process(matches[0]))
        return f"launched {matches[0].stem}"

    # 4. cmd start - resolves App Paths registry entries (chrome, spotify,
    #    firefox, ...). `start` exits 1 when the app does not exist; it
    #    returns immediately after launching, so run() is fast.
    try:
        result = subprocess.run(
            ["cmd", "/c", "start", "", name],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        result = None
        print(f"[APPS] shell start error: {e}", flush=True)
    if result is not None and result.returncode == 0:
        _remember_launch(name)
        focus_app(name)
        return f"launched {name} (via shell)"

    # 5. Nothing worked - say what we *could* have launched instead.
    close = _close_matches(name)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise ToolFailure(f"could not find an app called '{name}'.{hint}")
