"""
Read an .i3d.shapes file end-to-end:
  * Read 4-byte FileHeader (version + seed).
  * Wrap stream in CipherStream.
  * Read u32 entity_count.
  * For each entity: read [u32 type][u32 size][bytes data*size].
  * If type==1 (Shape): parse via shapes_entity.parse_shape_entity.
  * If type==2 (Spline): skip (out of v1 scope).
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO

from .shapes_cipher import CipherStream, FileHeader, I3DCipher
from .shapes_entity import ShapeData, parse_shape_entity


# Donkie supports 2..7. FS25 is 9. We allow 2..9; the cipher itself doesn't
# vary with version, and shape contents have only minor version-specific
# branches (tangents from v5, UV densities from v6).
SUPPORTED_VERSIONS = range(2, 10)


def parse_external_shapes(path: Path | str) -> dict[int, ShapeData]:
    path = Path(path)
    with open(path, "rb") as f:
        return _parse_stream(f)


def _parse_stream(f: BinaryIO) -> dict[int, ShapeData]:
    header = FileHeader.read(f)
    if header.version not in SUPPORTED_VERSIONS:
        raise ValueError(
            f"Unsupported .i3d.shapes version {header.version}; "
            f"supported: {SUPPORTED_VERSIONS.start}..{SUPPORTED_VERSIONS.stop - 1}"
        )

    cipher = I3DCipher(header.seed)
    stream = CipherStream(f, cipher)

    entity_count = struct.unpack("<I", stream.read(4))[0]
    if not (0 <= entity_count <= 1_000_000):
        raise ValueError(f"insane entity count {entity_count}")

    shapes: dict[int, ShapeData] = {}
    for i in range(entity_count):
        type_bytes = stream.read(4)
        size_bytes = stream.read(4)
        if len(type_bytes) != 4 or len(size_bytes) != 4:
            raise ValueError(f"truncated entity #{i}")
        etype = struct.unpack("<I", type_bytes)[0]
        size = struct.unpack("<I", size_bytes)[0]
        if size < 0 or size > 256 * 1024 * 1024:
            raise ValueError(f"insane entity size {size} for entity #{i}")
        data = stream.read(size)
        if len(data) != size:
            raise ValueError(f"truncated entity #{i} data: want {size}, got {len(data)}")

        if etype == 1:  # Shape
            try:
                shape = parse_shape_entity(data, header.version)
            except Exception as e:
                raise ValueError(f"failed to parse shape entity #{i}: {e}") from e
            shapes[shape.shape_id] = shape
        elif etype == 2:  # Spline — out of v1 scope
            continue
        else:
            # Unknown type; skip — Donkie's enum reserves 0 for Unknown.
            continue

    return shapes
