"""Mini Jarvis - file search (Phase 5) + destructive operations (Phase 8).

find_file searches the configured roots for files whose name contains
the spoken words. delete/move/copy resolve "which file did you mean"
through _resolve_single_match, the most conservative code in the
assistant (plan section 8.2): a full existing path is used directly,
otherwise the same search runs and

  - exactly one match  -> proceed (the registry still gates the action)
  - zero matches       -> ToolFailure, reported not guessed around
  - several matches    -> refuse and list them; never pick one

A DANGEROUS delete must never guess, so every ambiguity becomes a
question back to the user, not a choice made for them.
"""

import os
import shutil
import time
from pathlib import Path

from mini_jarvis import config
from mini_jarvis.tools import ToolFailure

# Fallback if config.json has no search_paths: these sit under the home dir
_DEFAULT_SEARCH_DIRS = ["Desktop", "Documents", "Downloads"]

# Safety valves so "find report" over huge drives does not run forever
_MAX_MATCHES = 20
_MAX_SECONDS_PER_PATH = 10.0
_MAX_SECONDS_TOTAL = 20.0


def config_search_paths() -> list[str]:
    """Search roots for find_file, from config.json (or sane defaults)."""
    paths = config.load().search_paths
    if paths:
        return [str(Path(p).expanduser()) for p in paths]
    home = Path.home()
    return [str(home / d) for d in _DEFAULT_SEARCH_DIRS]


# Directories that are pure library/system clutter for a name search
_JUNK_DIRS = {"__pycache__", "node_modules", "site-packages"}


def _junk_dir(dirname: str) -> bool:
    lowered = dirname.lower()
    return (
        dirname.startswith((".", "$"))
        or lowered in _JUNK_DIRS
        or lowered.startswith(("venv", ".venv", "env", "build", "dist"))
    )


def _find_file_adapter(name: str, search_path: str | None = None) -> str:
    """Registry glue: a single spoken search_path becomes a one-item list."""
    if search_path:
        folder = _resolve_folder(search_path)
        if folder is None:
            raise ToolFailure(f"folder '{search_path}' does not exist")
        return find_file(name, [str(folder)])
    return find_file(name, None)


def _search_name(name: str, roots: list[Path]) -> tuple[list[Path], list[str]]:
    """The walk find_file and the destructive tools share.

    Returns the matching paths (case-insensitive substring of the file
    name) and the roots that were actually searched. Capped by match
    count and elapsed time so huge drives cannot stall the loop.
    """
    needle = name.strip().lower()
    matches: list[Path] = []
    searched: list[str] = []
    total_started = time.time()

    for base in roots:
        if not base.is_dir():
            continue
        searched.append(str(base))
        started = time.time()
        for root, dirs, files in os.walk(base):
            # skip system/hidden clutter and library/venv directories
            dirs[:] = [d for d in dirs if not _junk_dir(d)]
            for fname in files:
                if needle in fname.lower():
                    matches.append(Path(root) / fname)
                    if len(matches) >= _MAX_MATCHES:
                        break
            if (
                len(matches) >= _MAX_MATCHES
                or time.time() - started > _MAX_SECONDS_PER_PATH
                or time.time() - total_started > _MAX_SECONDS_TOTAL
            ):
                break
        if len(matches) >= _MAX_MATCHES or time.time() - total_started > _MAX_SECONDS_TOTAL:
            break
    return matches, searched


def find_file(name: str, search_paths: list[str] | None = None) -> str:
    """Search for files whose name contains `name` (case-insensitive).

    search_paths overrides config.json when given. Returns a summary of
    the first matches found; a fruitless search is reported, not an error.
    """
    if not (name or "").strip():
        raise ToolFailure("no file name given")

    roots = [Path(p).expanduser() for p in (search_paths or config_search_paths())]
    matches, searched = _search_name(name, roots)

    if not matches:
        where = ", ".join(searched) if searched else "the configured paths"
        return f"no file matching '{name}' found (searched {where})"

    head = [str(m) for m in matches[:10]]
    more = f"\n  ...and {len(matches) - 10} more" if len(matches) > 10 else ""
    return f"found {len(matches)} match(es):\n  " + "\n  ".join(head) + more


def _resolve_single_match(name: str, search_path: str | None = None) -> Path:
    """The one place that decides "which file did you mean" (section 8.2)."""
    name = (name or "").strip().strip('"')
    candidate = Path(name).expanduser()
    if candidate.is_file():
        return candidate

    if search_path:
        folder = _resolve_folder(search_path)
        if folder is None:
            raise ToolFailure(f"folder '{search_path}' does not exist")
        roots = [folder]
    else:
        roots = [Path(p) for p in config_search_paths()]
    matches, searched = _search_name(name, roots)
    if not matches:
        where = ", ".join(searched) if searched else "the configured paths"
        raise ToolFailure(f"no file matching '{name}' found (searched {where})")
    if len(matches) > 1:
        listing = "\n  ".join(str(m) for m in matches[:10])
        more = f"\n  ...and {len(matches) - 10} more" if len(matches) > 10 else ""
        raise ToolFailure(
            f"{len(matches)} files match '{name}' - say the full name or "
            f"the folder to be more specific:\n  {listing}{more}"
        )
    return matches[0]


# Spoken folder words and phrases that mean a real folder ("in desktop",
# "into my documents", "the downloads folder")
_FOLDER_ALIASES = {
    "docs": "documents",
    "my desktop": "desktop",
    "my documents": "documents",
    "my downloads": "downloads",
    "desktop folder": "desktop",
    "documents folder": "documents",
    "downloads folder": "downloads",
}


def _resolve_folder(name: str) -> Path | None:
    """A spoken folder name becomes an existing directory, or None.

    'desktop' resolves against the home dir and the configured search
    roots; an absolute path must already exist. Never invents a folder.
    """
    raw = (name or "").strip().strip('"').lower()
    raw = _FOLDER_ALIASES.get(raw, raw)
    path = Path(raw).expanduser()
    if path.is_dir():
        return path
    if path.is_absolute():
        return None
    candidates = [Path.home() / path]
    candidates += [Path(p) / path for p in config_search_paths()]
    for folder in candidates:
        if folder.is_dir():
            return folder
    return None


def _resolve_destination(destination: str) -> Path:
    """A spoken folder name becomes an existing directory, or fails."""
    folder = _resolve_folder(destination)
    if folder is None:
        raise ToolFailure(f"destination folder '{destination}' does not exist")
    return folder


def _delete_file_prepare(slots: dict) -> dict:
    """Registry pre-gate: resolve the target file before the DANGEROUS
    prompt, so the confirmation names the exact path and ambiguity is
    refused before asking for approval (section 8.2 order)."""
    path = _resolve_single_match(slots.get("name", ""), slots.get("search_path"))
    return {"name": str(path)}


def _transfer_file_prepare(slots: dict) -> dict:
    """Pre-gate resolution for move/copy: file and destination folder."""
    path = _resolve_single_match(slots.get("name", ""), slots.get("search_path"))
    return {
        "name": str(path),
        "destination": str(_resolve_destination(slots.get("destination", ""))),
    }


def delete_file(name: str, search_path: str | None = None) -> str:
    path = _resolve_single_match(name, search_path)
    try:
        path.unlink()
    except OSError as error:
        raise ToolFailure(f"could not delete {path}: {error}") from None
    return f"deleted {path}"


def move_file(name: str, destination: str, search_path: str | None = None) -> str:
    path = _resolve_single_match(name, search_path)
    dest = _resolve_destination(destination)
    if dest.is_dir():
        dest = dest / path.name
    try:
        shutil.move(str(path), str(dest))
    except OSError as error:
        raise ToolFailure(f"could not move {path}: {error}") from None
    return f"moved {path} to {dest}"


def copy_file(name: str, destination: str, search_path: str | None = None) -> str:
    path = _resolve_single_match(name, search_path)
    dest = _resolve_destination(destination)
    if dest.is_dir():
        dest = dest / path.name
    try:
        shutil.copy2(str(path), str(dest))
    except OSError as error:
        raise ToolFailure(f"could not copy {path}: {error}") from None
    return f"copied {path} to {dest}"


def create_file(name: str, destination: str | None = None) -> str:
    """Create an empty file; '.txt' is added when no extension was spoken.

    Lives with the other file tools even though the plan's section 8.1
    did not list creation - Nitro's live testing showed "create text.txt
    on desktop" otherwise routes to TYPE_TEXT, which types the words into
    whatever app matches, instead of making anything.
    """
    name = (name or "").strip().strip('"')
    if not name:
        raise ToolFailure("no file name given")
    if any(ch in name for ch in "\\/:*?|<>"):
        raise ToolFailure(f"'{name}' is not a valid file name")
    if not Path(name).suffix:
        name += ".txt"
    folder = (
        _resolve_destination(destination) if destination else Path.home() / "Desktop"
    )
    path = folder / name
    if path.exists():
        raise ToolFailure(f"'{path}' already exists")
    try:
        path.write_text("", encoding="utf-8")
    except OSError as error:
        raise ToolFailure(f"could not create {path}: {error}") from None
    return f"created {path}"


if __name__ == "__main__":
    import tempfile

    scratch = Path(tempfile.mkdtemp(prefix="jarvis-fileops-"))
    second = Path(tempfile.mkdtemp(prefix="jarvis-fileops-dest-"))
    (scratch / "unique-photo.jpg").write_text("x", encoding="utf-8")
    (scratch / "report.txt").write_text("x", encoding="utf-8")
    (scratch / "report-final.txt").write_text("x", encoding="utf-8")
    (scratch / "budget.xlsx").write_text("x", encoding="utf-8")

    def show(label: str, fn, *args):
        try:
            print(f"[{label}] {fn(*args)}")
        except ToolFailure as failure:
            print(f"[{label}] ToolFailure: {failure}")

    show("copy   ", copy_file, "budget.xlsx", str(second), str(scratch))
    show("move   ", move_file, "unique-photo.jpg", str(second), str(scratch))
    show("create ", create_file, "new-note.txt", str(scratch))
    show("create2", create_file, "plain-name", str(second))  # gains .txt
    show("create-dupe", create_file, "plain-name", str(second))  # exists -> refuse
    show("delete-many", delete_file, "report", str(scratch))  # two matches -> refuse
    show("delete ", delete_file, "budget.xlsx", str(second))  # single match in dest
    show("delete-none", delete_file, "no-such-file.xyz", str(scratch))
    print(f"[scratch] {scratch}")
    print(f"[dest   ] {[p.name for p in second.iterdir()]}")
