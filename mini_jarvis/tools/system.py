"""Mini Jarvis - Phase 5: system tools (screen, volume, typing)."""

import time

from mini_jarvis.config import PROJECT_ROOT
from mini_jarvis.tools import ToolFailure

# Screenshots live inside the project folder: <project>\pictures
_SCREENSHOT_DIR = PROJECT_ROOT / "pictures"

try:
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    _PYCAW_OK = True
except ImportError:
    _PYCAW_OK = False

try:
    import pyautogui

    pyautogui.FAILSAFE = False  # corner-fling abort off: not needed here
    _PYAUTOGUI_OK = True
except ImportError:
    _PYAUTOGUI_OK = False

from PIL import ImageGrab


def take_screenshot() -> str:
    """Save a full-screen capture into <project>\\pictures and return the path."""
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = _SCREENSHOT_DIR / f"shot_{time.strftime('%Y%m%d_%H%M%S')}.png"
    image = ImageGrab.grab()
    image.save(path)
    return f"saved screenshot to {path}"


def _master_volume():
    """The Windows master volume interface (default output device)."""
    # pycaw 2026 wraps the endpoint in an AudioDevice whose EndpointVolume
    # property does the COM Activate + QueryInterface for us.
    return AudioUtilities.GetSpeakers().EndpointVolume


def change_volume(direction: str, amount: int) -> str:
    """Change the system volume by an exact amount, or set it outright."""
    if not _PYCAW_OK:
        raise ToolFailure("pycaw is not installed - cannot control volume")
    try:
        volume = _master_volume()
        if direction == "set":
            level = max(0, min(int(amount), 100))
        elif direction == "up":
            level = min(round(volume.GetMasterVolumeLevelScalar() * 100) + int(amount), 100)
        elif direction == "down":
            level = max(round(volume.GetMasterVolumeLevelScalar() * 100) - int(amount), 0)
        else:
            raise ToolFailure(f"unknown volume direction {direction!r}")
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        actual = round(volume.GetMasterVolumeLevelScalar() * 100)
        return f"volume now at {actual}%"
    except ToolFailure:
        raise
    except Exception as e:
        raise ToolFailure(f"could not change the system volume: {e}")


# Places that are folders, not apps. Slot extraction hands "on desktop"
# style tails to type_text; launching the first Start Menu shortcut whose
# name contains the word (Docker Desktop!) and typing into it is never
# what was asked, so these fail with a pointer to the right tool.
_FOLDER_WORDS = frozenset(
    "desktop documents downloads docs pictures music videos "
    "desktop folder documents folder downloads folder".split()
)


def type_text(text: str, where: str | None = None) -> str:
    """Type text into the currently focused window.

    If `where` names an application, that app is launched first and given
    a moment to take focus. Requires the SENSITIVE confirmation from the
    safety layer - typing goes wherever the user's cursor lives.
    """
    if not _PYAUTOGUI_OK:
        raise ToolFailure("pyautogui is not installed - cannot type")
    text = text or ""
    if not text:
        raise ToolFailure("nothing to type")

    launched_note = ""
    if where:
        if where.strip().lower() in _FOLDER_WORDS:
            raise ToolFailure(
                f"cannot type into '{where}' - that is a folder, not an app. "
                "To create a file there, say 'create <name> in <folder>'"
            )
        from mini_jarvis.tools.apps import focus_app, open_app, was_recently_launched

        if was_recently_launched(where):
            # An earlier sub-command ("open notepad and type hello") just
            # launched this app. Do not open a second copy - only re-grab
            # focus, because the user's typed confirmation put the console
            # back in front.
            focus_app(where)
            time.sleep(0.5)
        else:
            open_app(where)  # launches AND forces the window to the foreground
            time.sleep(1.0)  # small grace period for the window to settle
        launched_note = f" in {where}"
    else:
        time.sleep(1.5)  # a short window to click into the target field

    pyautogui.write(text, interval=0.02)
    return f"typed {len(text)} characters{launched_note}"


def _type_text_adapter(text: str, where: str | None = None) -> str:
    """Registry-facing signature for TYPE_TEXT (name kept for confirm text)."""
    return type_text(text, where)
