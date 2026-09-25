"""Cross-process coordination for managed media and maintenance."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def storage_lock(data_dir: Path, *, exclusive: bool) -> Iterator[None]:
    """Lock the existing data directory without creating a dry-run artifact."""
    directory = data_dir.resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Managed data path is not a directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
