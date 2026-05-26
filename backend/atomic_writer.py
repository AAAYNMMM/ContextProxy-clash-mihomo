import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        dir_fd = os.open(str(path), os.O_RDONLY)
    except Exception:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def atomic_write_bytes(path: Path, data: bytes, *, backup: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if backup and path.exists():
        backup_path = path.with_name(path.name + ".bak")
        atomic_write_bytes(backup_path, path.read_bytes(), backup=False)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8", backup: bool = False) -> None:
    atomic_write_bytes(path, text.encode(encoding), backup=backup)


def atomic_write_json(path: Path, data: Any, *, backup: bool = False) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, text, backup=backup)


def atomic_write_yaml(path: Path, data: Any, *, backup: bool = False) -> None:
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    atomic_write_text(path, text, backup=backup)
