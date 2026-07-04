"""
Parse a single decrypted Entity's payload into a ShapeData.

The entity wire format (matching Donkie/I3DShapesTool):

    Outer cipher stream:
        [u32 entity_count]
        per entity: [u32 type][u32 size][bytes data*size]   (cipher-decrypted)

    Inner data buffer (plaintext, parsed here):
        I3DPart header:
            [u32 name_len][bytes name][align 4][u32 id]
        I3DShape contents (when type==1):
            [vec4 bounding_volume]
            [u32 corner_count][u32 num_subsets][u32 vertex_count][u32 options]
            [subset]*num_subsets    -- 4 u32 + (UV-density floats if v>=6)
            [tri]*tri_count         -- u16 or u32 indices, tri_count = corner_count/3
            align(4)
            [vec3 position]*vertex_count
            [vec3 normal]*vertex_count        if HasNormals
            [vec4 tangent]*vertex_count       if HasTangents and v>=5
            [vec2 uv]*vertex_count   x4       per HasUV{1..4}
            [vec4 color]*vertex_count         if HasVertexColor
            [4 floats weight]*vertex_count    if HasSkinning && !SingleBlend
            [N bytes index]*vertex_count       N=4 normal, N=1 single-blend
            [float generic]*vertex_count      if HasGeneric
            [u32 num_attachments]
            attachments... (variable)
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field


# I3DShapeOptions bitflags
OPT_HAS_NORMALS = 0x001
OPT_HAS_UV1 = 0x002
OPT_HAS_UV2 = 0x004
OPT_HAS_UV3 = 0x008
OPT_HAS_UV4 = 0x010
OPT_HAS_VERTEX_COLOR = 0x020
OPT_HAS_SKINNING = 0x040
OPT_HAS_TANGENTS = 0x080
OPT_SINGLE_BLEND_WEIGHTS = 0x100
OPT_HAS_GENERIC = 0x200


class _R:
    """Tiny endian-aware reader over an in-memory plaintext buffer."""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)
        self._len = len(data)

    def read(self, n: int) -> bytes:
        b = self._buf.read(n)
        if len(b) != n:
            raise EOFError(f"want {n} bytes at offset {self._buf.tell()-len(b)}, got {len(b)}")
        return b

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.read(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.read(4))[0]

    def vec3(self) -> tuple[float, float, float]:
        return struct.unpack("<fff", self.read(12))

    def vec4(self) -> tuple[float, float, float, float]:
        return struct.unpack("<ffff", self.read(16))

    def align(self, word: int = 4) -> None:
        m = self._buf.tell() % word
        if m:
            self._buf.seek(word - m, 1)

    def tell(self) -> int:
        return self._buf.tell()

    def remaining(self) -> int:
        return self._len - self._buf.tell()


@dataclass
class ShapeSubset:
    first_vertex: int
    num_vertices: int
    first_index: int
    num_indices: int
    uv_density: list[float] = field(default_factory=list)


@dataclass
class ShapeData:
    name: str
    shape_id: int
    bounding_volume: tuple[float, float, float, float]
    vertex_count: int
    corner_count: int
    options: int
    subsets: list[ShapeSubset]
    triangles: list[tuple[int, int, int]]            # 0-indexed (raw disk values)
    positions: list[tuple[float, float, float]]
    normals: list[tuple[float, float, float]] | None
    tangents: list[tuple[float, float, float, float]] | None
    uv_sets: list[list[tuple[float, float]] | None]  # length 4
    vertex_colors: list[tuple[float, float, float, float]] | None
    blend_weights: list[tuple[float, float, float, float]] | None
    blend_indices: list[tuple[int, ...]] | None      # tuple of 4 (or 1 if single-blend)
    single_blend: bool
    generic_data: list[float] | None
    attachments_raw: list[bytes]
    vtx_compression: float | None = None             # FS25 v>=9


def parse_shape_entity(data: bytes, file_version: int) -> ShapeData:
    r = _R(data)
    name = "<unparsed>"

    def _err(section: str) -> str:
        return (f"{section} for shape {name!r} (entity size {len(data)}, "
                f"file pos {r.tell()})")

    try:
        # I3DPart header
        name_len = r.i32()
        if name_len < 0 or name_len > 4096:
            raise ValueError(f"unreasonable shape name length {name_len}")
        name = r.read(name_len).decode("ascii", errors="replace")
        r.align(4)
        shape_id = r.u32()

        # I3DShape.ReadContents
        bounding = r.vec4()
        corner_count = r.u32()
        num_subsets = r.u32()
        vertex_count = r.u32()
        options = r.u32()
    except EOFError as e:
        raise ValueError(f"EOF in header: {e}") from e
    print(f"[i3d-shape] entity name={name!r} id={shape_id} verts={vertex_count} "
          f"corners={corner_count} subsets={num_subsets} options={options:#x}")

    has_normals = bool(options & OPT_HAS_NORMALS)
    has_uv = [bool(options & (OPT_HAS_UV1 << i)) for i in range(4)]
    has_color = bool(options & OPT_HAS_VERTEX_COLOR)
    has_skin = bool(options & OPT_HAS_SKINNING)
    has_tangents = bool(options & OPT_HAS_TANGENTS)
    single_blend = bool(options & OPT_SINGLE_BLEND_WEIGHTS)
    has_generic = bool(options & OPT_HAS_GENERIC)

    # ---- v9 subset layout ----
    # Donkie's v2-7 spec is: per subset
    #   [u32 firstVertex][u32 numVertices][u32 firstIndex][u32 numIndices]
    #   then UVDensity per HasUV flag (v>=6).
    # FS25 v9 adds:
    #   * an extra leading u32 at the start of each subset (always 0 in
    #     our cattle sample — purpose unknown).
    #   * a 4-byte field AFTER the subset section, BEFORE triangles
    #     (also 0 here; possibly VtxCompression).
    subsets: list[ShapeSubset] = []
    for _ in range(num_subsets):
        if file_version >= 9:
            _subset_extra = r.u32()  # mystery u32 at subset start; always 0 here
        s = ShapeSubset(
            first_vertex=r.u32(),
            num_vertices=r.u32(),
            first_index=r.u32(),
            num_indices=r.u32(),
        )
        if file_version >= 6:
            for i in range(4):
                if has_uv[i]:
                    s.uv_density.append(r.f32())
        subsets.append(s)

    # Post-subsets / pre-triangles 4-byte field (v>=9). Maybe VtxCompression.
    vtx_compression: float | None = None
    if file_version >= 9:
        vtx_compression = r.f32()

    try:
        is_int_idx = vertex_count > 0x10000
        tri_count = corner_count // 3
        triangles: list[tuple[int, int, int]] = []
        if is_int_idx:
            for _ in range(tri_count):
                triangles.append(struct.unpack("<III", r.read(12)))
        else:
            for _ in range(tri_count):
                triangles.append(struct.unpack("<HHH", r.read(6)))
        r.align(4)
    except EOFError as e:
        raise ValueError(f"EOF in triangles: {_err('triangles')}: {e}") from e

    try:
        positions = [r.vec3() for _ in range(vertex_count)]
    except EOFError as e:
        raise ValueError(f"EOF in positions: {_err('positions')}: {e}") from e

    normals: list[tuple[float, float, float]] | None = None
    if has_normals:
        try:
            normals = [r.vec3() for _ in range(vertex_count)]
        except EOFError as e:
            raise ValueError(f"EOF in normals: {_err('normals')}: {e}") from e

    tangents: list[tuple[float, float, float, float]] | None = None
    if has_tangents and file_version >= 5:
        try:
            tangents = [r.vec4() for _ in range(vertex_count)]
        except EOFError as e:
            raise ValueError(f"EOF in tangents: {_err('tangents')}: {e}") from e

    uv_sets: list[list[tuple[float, float]] | None] = [None, None, None, None]
    for i in range(4):
        if not has_uv[i]:
            continue
        uvs: list[tuple[float, float]] = []
        try:
            if 4 <= file_version <= 5:
                for _ in range(vertex_count):
                    v = r.f32(); u = r.f32()
                    uvs.append((u, v))
            else:
                for _ in range(vertex_count):
                    u = r.f32(); v = r.f32()
                    uvs.append((u, v))
        except EOFError as e:
            raise ValueError(f"EOF in UV{i+1}: {_err(f'UV{i+1}')}: {e}") from e
        uv_sets[i] = uvs

    vertex_colors: list[tuple[float, float, float, float]] | None = None
    if has_color:
        try:
            vertex_colors = [r.vec4() for _ in range(vertex_count)]
        except EOFError as e:
            raise ValueError(f"EOF in colors: {_err('colors')}: {e}") from e

    blend_weights: list[tuple[float, float, float, float]] | None = None
    blend_indices: list[tuple[int, ...]] | None = None
    if has_skin:
        try:
            if not single_blend:
                blend_weights = [
                    struct.unpack("<ffff", r.read(16)) for _ in range(vertex_count)
                ]
                num_idx = 4
            else:
                num_idx = 1
            blend_indices = [tuple(r.read(num_idx)) for _ in range(vertex_count)]
        except EOFError as e:
            raise ValueError(f"EOF in skinning: {_err('skinning')}: {e}") from e

    generic_data: list[float] | None = None
    if has_generic:
        try:
            generic_data = [r.f32() for _ in range(vertex_count)]
        except EOFError as e:
            raise ValueError(f"EOF in generic: {_err('generic')}: {e}") from e

    try:
        num_attachments = r.u32()
        attachments_raw: list[bytes] = []
        for _ in range(num_attachments):
            flags = r.u32()
            floats_bytes = b""
            if flags & 4:
                floats_bytes = r.read(12)
            n = r.i32()
            a_data = r.read(max(0, n))
            attachments_raw.append(
                struct.pack("<I", flags) + floats_bytes + struct.pack("<i", n) + a_data
            )
    except EOFError as e:
        # Many shapes simply have no attachment section at all — skip
        # silently if the file ended cleanly after vertex data.
        if r.remaining() == 0:
            num_attachments = 0
            attachments_raw = []
        else:
            raise ValueError(f"EOF in attachments: {_err('attachments')}: {e}") from e

    return ShapeData(
        name=name,
        shape_id=shape_id,
        bounding_volume=bounding,
        vertex_count=vertex_count,
        corner_count=corner_count,
        options=options,
        subsets=subsets,
        triangles=triangles,
        positions=positions,
        normals=normals,
        tangents=tangents,
        uv_sets=uv_sets,
        vertex_colors=vertex_colors,
        blend_weights=blend_weights,
        blend_indices=blend_indices,
        single_blend=single_blend,
        generic_data=generic_data,
        attachments_raw=attachments_raw,
        vtx_compression=vtx_compression,
    )
