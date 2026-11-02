from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
from pathlib import Path


def _entries(root: Path) -> list[Path]:
    paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        paths.extend(base / d for d in dirnames)
        paths.extend(base / f for f in filenames)
    return sorted(paths, key=lambda p: p.relative_to(root).as_posix())


def _info(name: str, mode: int, mtime: int, kind: bytes = tarfile.REGTYPE) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode
    info.mtime = mtime
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def write_deterministic_tar(root: Path, out: Path, *, arc_prefix: str, mtime: int) -> str:
    """Write root as an uncompressed tar with normalized metadata; return its SHA-256."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as tar:
        tar.addfile(_info(arc_prefix, 0o755, mtime, tarfile.DIRTYPE))
        for path in _entries(root):
            name = f"{arc_prefix}/{path.relative_to(root).as_posix()}"
            st = path.lstat()
            if stat.S_ISLNK(st.st_mode):
                info = _info(name, 0o777, mtime, tarfile.SYMTYPE)
                info.linkname = os.readlink(path)
                tar.addfile(info)
            elif stat.S_ISDIR(st.st_mode):
                tar.addfile(_info(name, 0o755, mtime, tarfile.DIRTYPE))
            else:
                mode = 0o755 if st.st_mode & stat.S_IXUSR else 0o644
                data = path.read_bytes()
                info = _info(name, mode, mtime)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    payload = buffer.getvalue()
    out.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def extract(tar_path: Path, dest: Path) -> None:
    with tarfile.open(tar_path) as tar:
        tar.extractall(dest, filter="tar")
