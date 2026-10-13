"""Private, atomic local persistence for the browser interface."""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from dotenv.parser import parse_stream

SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class StorageError(ValueError):
    """A fixed diagnostic that never includes file contents or credentials."""


def checked_path(path: Path) -> Path:
    """Reject symlinks in every component, including configured root directories."""
    path = Path(os.path.abspath(path))
    for component in (path, *path.parents):
        if component.is_symlink():
            raise StorageError("Symbolic links are not supported for application files.")
    return path


def child_path(directory: Path, filename: str) -> Path:
    if not SAFE_ID.fullmatch(filename):
        raise StorageError("Invalid file identifier.")
    root = checked_path(directory)
    path = checked_path(root / filename)
    if path.parent != root:
        raise StorageError("Invalid file identifier.")
    return path


def atomic_text(path: Path, content: str, private: bool = False) -> None:
    path = checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".web-", delete=False
        ) as stream:
            temporary = stream.name
            os.chmod(temporary, 0o600 if private else 0o644)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        checked_path(path)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read_environment(path: Path) -> dict[str, str]:
    path = checked_path(path)
    values = dotenv_values(path, interpolate=False) if path.exists() else {}
    return {**os.environ, **{key: value or "" for key, value in values.items()}}


def update_environment(path: Path, changes: dict[str, str]) -> None:
    """Preserve unrelated bindings/comments and encode values without interpolation.

    Single quoted dotenv values support escaped backslash/apostrophe and literal
    newlines, while leaving dollar signs untouched. Multiline existing bindings
    are consumed as one unit by python-dotenv's own parser.
    """
    path = checked_path(path)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    replacements = dict(changes)
    pieces = []
    for binding in parse_stream(io.StringIO(original)):
        if binding.key in changes:
            key = binding.key
            if key in replacements:
                pieces.append(
                    _binding(key, replacements.pop(key), _inline_comment(binding.original.string))
                )
            continue
        pieces.append(binding.original.string)
    if pieces and not pieces[-1].endswith("\n"):
        pieces.append("\n")
    pieces.extend(_binding(key, value) for key, value in replacements.items())
    atomic_text(path, "".join(pieces), private=True)


def _inline_comment(original: str) -> str:
    """Keep comments on edited bindings without mistaking quoted hashes for comments."""
    quote: str | None = None
    escaped = False
    in_value = False
    for index, character in enumerate(original):
        if not in_value:
            in_value = character == "="
            continue
        if escaped:
            escaped = False
        elif quote is not None and character == "\\":
            escaped = True
        elif character == quote:
            quote = None
        elif quote is None and character in {"'", '"'}:
            quote = character
        elif quote is None and character == "#":
            if index > 0 and (original[index - 1].isspace() or original[index - 1] in {"'", '"'}):
                return original[index:].splitlines()[0]
    return ""


def _binding(key: str, value: str, comment: str = "") -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    suffix = f" {comment}" if comment else ""
    return f"{key}='{escaped}'{suffix}\n"
