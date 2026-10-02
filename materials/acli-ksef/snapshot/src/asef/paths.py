from __future__ import annotations

import os
import tempfile
from pathlib import Path

from platformdirs import user_data_dir


def data_dir() -> Path:
    override = os.environ.get("ASEF_DATA_DIR")
    root = Path(override).expanduser() if override else Path(user_data_dir("ASEF", "ASEF"))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def db_path() -> Path:
    return data_dir() / "asef.sqlite3"


def write_private_file(output: Path, content: bytes) -> Path:
    """Replace an output atomically without exposing invoice bytes while writing."""
    output = output.expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output
