"""Commit a new library file set, preserving every pre-existing user file."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import tempfile

from app_paths import temporary_directory
from errors import DownloadError


def _move_new(source, target):
    if os.name == 'nt':
        from ctypes import wintypes
        move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileExW
        move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        move.restype = wintypes.BOOL
        # COPY_ALLOWED | WRITE_THROUGH, deliberately without REPLACE_EXISTING.
        if not move(str(source), str(target), 0x2 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        # POSIX hard links publish the complete file without replacing a winner.
        os.link(source, target)
        source.unlink()


def commit_library(root, artifacts, check_cancelled=lambda: None):
    """Stage under app data, publish footprints first and symbols last.

    This v1 operation creates new libraries only. A collision is an error.
    Failed/cancelled commits remove only files this operation created and whose
    contents still match, leaving externally changed files untouched.
    """
    root = Path(root).resolve()
    paths = [(artifact, (root / artifact.path).resolve()) for artifact in artifacts]
    if len({str(path).casefold() for _, path in paths}) != len(paths):
        raise DownloadError('元件库输出路径冲突')
    pretty_dirs = {path.parent for _, path in paths if path.parent != root}
    for _, path in paths:
        if not path.is_relative_to(root) or path == root:
            raise DownloadError('元件库输出路径越界')
        if path.exists():
            raise DownloadError('KiCad 库已存在，请更换库名或目录；首版不支持追加：' + str(path))
    if any(path.exists() for path in pretty_dirs):
        raise DownloadError('KiCad 封装库目录已存在，请更换库名或目录')
    made_dirs, committed = [], []
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='library-', dir=temporary_directory()) as staging:
        staged = []
        for index, (artifact, target) in enumerate(paths):
            check_cancelled()
            source = Path(staging) / str(index)
            with source.open('wb') as stream:
                stream.write(artifact.payload)
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((artifact, source, target))
        try:
            for directory in sorted(pretty_dirs):
                directory.mkdir()  # exclusive reservation; no existing directory is reused
                made_dirs.append(directory)
            for artifact, source, target in staged:
                check_cancelled()
                _move_new(source, target)
                committed.append((artifact, target))
        except BaseException:
            for artifact, target in reversed(committed):
                try:
                    if target.read_bytes() == artifact.payload:
                        target.unlink()
                except OSError:
                    pass
            for directory in reversed(made_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
    return [(artifact, str(target)) for artifact, target in committed]
