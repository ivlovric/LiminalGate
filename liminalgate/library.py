"""List, download, and delete recorded sessions on disk."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from liminalgate.errors import NotFoundError

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9T._-]{0,64}$")


def resolve_session(root: Path, subject: str, session_id: str) -> Path:
    if not _ID.match(subject) or not _ID.match(session_id):
        raise NotFoundError("unknown session")
    folder = (root / f"sub-{subject}" / f"ses-{session_id}").resolve()
    root_resolved = root.resolve()
    if root_resolved not in folder.parents or not folder.is_dir():
        raise NotFoundError("unknown session")
    return folder


def session_files(folder: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        rows.append({"name": rel, "bytes": path.stat().st_size})
    return rows


def file_inside(folder: Path, relative: str) -> Path:
    target = (folder / relative).resolve()
    if folder.resolve() not in target.parents and target != folder.resolve():
        raise NotFoundError("file is outside the session")
    if not target.is_file():
        raise NotFoundError("file not found")
    return target


def zip_session(folder: Path) -> bytes:
    """One archive of the session directory, paths rooted at sub-*/ses-*."""

    buffer = io.BytesIO()
    root = folder.parent.parent
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    return buffer.getvalue()


def delete_session(folder: Path) -> None:
    for path in sorted(folder.rglob("*"), reverse=True):
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    folder.rmdir()
    parent = folder.parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
