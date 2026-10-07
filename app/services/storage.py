"""Saving uploads to disk and safely unpacking shapefile archives."""

from __future__ import annotations

import shutil
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.exceptions import FileProcessingError, FileTooLargeError

CHUNK = 1024 * 1024

# Shapefile components we are willing to extract. Anything else is ignored.
SHAPEFILE_EXTENSIONS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
REQUIRED_EXTENSIONS = {".shp", ".shx", ".dbf"}


def save_upload(stream: BinaryIO, dest: Path, max_bytes: int) -> int:
    """Stream an upload to `dest`, aborting if it exceeds `max_bytes`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with dest.open("wb") as out:
        while chunk := stream.read(CHUNK):
            written += len(chunk)
            if written > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise FileTooLargeError(
                    f"File exceeds the maximum allowed size of {max_bytes // (1024 * 1024)} MB."
                )
            out.write(chunk)
    return written


def _is_junk(name: str) -> bool:
    parts = PurePosixPath(name.replace("\\", "/")).parts
    return any(p == "__MACOSX" for p in parts) or (bool(parts) and parts[-1].startswith("._"))


def extract_shapefile(
    zip_path: Path, dest_dir: Path, max_uncompressed: int, max_members: int
) -> Path:
    """Extract exactly one shapefile from a zip into `dest_dir`; return the .shp path.

    Safety measures:
    * zip-slip: member paths are never used for writing. Only the file *stem and
      extension* are inspected, and files are written under fixed names inside
      `dest_dir`, so '../../etc/passwd' style entries cannot escape.
    * zip bombs: the member count and the real number of bytes written are capped.
    """
    if not zipfile.is_zipfile(zip_path):
        raise FileProcessingError("The uploaded file is not a valid .zip archive.")

    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and not _is_junk(i.filename)]
        if len(infos) > max_members:
            raise FileProcessingError(f"Archive contains too many files (limit {max_members}).")

        # Group candidate members by (directory, lower-case stem).
        groups: dict[tuple[str, str], dict[str, zipfile.ZipInfo]] = defaultdict(dict)
        for info in infos:
            p = PurePosixPath(info.filename.replace("\\", "/"))
            ext = p.suffix.lower()
            if ext in SHAPEFILE_EXTENSIONS:
                groups[(str(p.parent), p.stem.lower())][ext] = info

        shapefiles = {key: parts for key, parts in groups.items() if ".shp" in parts}
        if not shapefiles:
            raise FileProcessingError("No .shp file found inside the archive.")
        if len(shapefiles) > 1:
            names = ", ".join(sorted(f"{stem}.shp" for _, stem in shapefiles))
            raise FileProcessingError(
                f"Archive contains {len(shapefiles)} shapefiles ({names}); "
                "please upload one shapefile per archive."
            )

        parts = next(iter(shapefiles.values()))
        missing = sorted(REQUIRED_EXTENSIONS - parts.keys())
        if missing:
            raise FileProcessingError(
                "Incomplete shapefile; missing required component(s): " + ", ".join(missing) + "."
            )

        total = 0
        for ext, info in parts.items():
            target = dest_dir / f"layer{ext}"  # fixed name: never derived from the archive
            with zf.open(info) as src, target.open("wb") as out:
                while chunk := src.read(CHUNK):
                    total += len(chunk)
                    if total > max_uncompressed:
                        raise FileProcessingError(
                            "Archive is too large once decompressed (possible zip bomb)."
                        )
                    out.write(chunk)

    return dest_dir / "layer.shp"


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
