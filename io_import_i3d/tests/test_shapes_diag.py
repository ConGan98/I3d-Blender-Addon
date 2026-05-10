"""
Diagnostic: dump entity headers + parse each shape with offset tracking.
"""
from __future__ import annotations

import importlib.util
import struct
import sys
import types
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1]
SAMPLE = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.shapes"


def _load(pkg_name: str, mods: list[str]):
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(ADDON_DIR / "importer")]
    sys.modules[pkg_name] = pkg
    out = {}
    for m in mods:
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{m}", ADDON_DIR / "importer" / f"{m}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{m}"] = mod
        out[m] = mod
    for m in mods:
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{m}", ADDON_DIR / "importer" / f"{m}.py"
        )
        spec.loader.exec_module(out[m])
    return out


def main() -> int:
    mods = _load(
        "io_import_i3d_pkg",
        ["shapes_keyconst", "shapes_cipher", "shapes_entity"],
    )
    cipher_mod = mods["shapes_cipher"]
    se = mods["shapes_entity"]

    # Read raw entity headers + data buffers (decrypted but unparsed)
    with open(SAMPLE, "rb") as f:
        header = cipher_mod.FileHeader.read(f)
        cipher = cipher_mod.I3DCipher(header.seed)
        stream = cipher_mod.CipherStream(f, cipher)
        entity_count = struct.unpack("<I", stream.read(4))[0]
        print(f"version={header.version} seed=0x{header.seed:02x} entities={entity_count}")
        entities = []
        for i in range(entity_count):
            etype = struct.unpack("<I", stream.read(4))[0]
            esize = struct.unpack("<I", stream.read(4))[0]
            edata = stream.read(esize)
            entities.append((etype, esize, edata))
            print(f"  entity #{i}: type={etype} size={esize} (0x{esize:x})")

    # For each Shape, walk the parser step by step, printing offsets
    def walk(data: bytes, version: int, label: str):
        r = se._R(data)
        print(f"\n--- {label} (data len = {len(data)}) ---")

        # I3DPart header
        name_len = r.i32()
        name = r.read(name_len).decode("ascii", errors="replace")
        r.align(4)
        shape_id = r.u32()
        print(f"  after header: offset={r.tell()}, name={name!r}, shape_id={shape_id}")

        # Shape contents
        bv = r.vec4()
        corner_count = r.u32()
        num_subsets = r.u32()
        vertex_count = r.u32()
        options = r.u32()
        print(f"  bv={bv} corner_count={corner_count} num_subsets={num_subsets} vertex_count={vertex_count} options=0x{options:x}")
        print(f"  after fixed header: offset={r.tell()}")

        has_normals = bool(options & se.OPT_HAS_NORMALS)
        has_uv = [bool(options & (se.OPT_HAS_UV1 << i)) for i in range(4)]
        has_color = bool(options & se.OPT_HAS_VERTEX_COLOR)
        has_skin = bool(options & se.OPT_HAS_SKINNING)
        has_tangents = bool(options & se.OPT_HAS_TANGENTS)
        single_blend = bool(options & se.OPT_SINGLE_BLEND_WEIGHTS)
        has_generic = bool(options & se.OPT_HAS_GENERIC)
        print(f"  flags: normals={has_normals} uv={has_uv} color={has_color} skin={has_skin} tangents={has_tangents} single_blend={single_blend} generic={has_generic}")

        # Subsets
        for s_i in range(num_subsets):
            r.read(16)  # 4 u32
            if version >= 6:
                for j in range(4):
                    if has_uv[j]:
                        r.f32()
        print(f"  after subsets: offset={r.tell()}")

        # Triangles
        is_int_idx = vertex_count > 0x10000
        tri_count = corner_count // 3
        tri_size_per = 12 if is_int_idx else 6
        r.read(tri_size_per * tri_count)
        print(f"  after triangles: offset={r.tell()}, tri_count={tri_count}, idx={'u32' if is_int_idx else 'u16'}")

        r.align(4)
        print(f"  after align(4): offset={r.tell()}")

        # Positions
        r.read(12 * vertex_count)
        print(f"  after positions: offset={r.tell()}")

        if has_normals:
            r.read(12 * vertex_count)
            print(f"  after normals: offset={r.tell()}")

        if has_tangents and version >= 5:
            r.read(16 * vertex_count)
            print(f"  after tangents: offset={r.tell()}")

        for i in range(4):
            if has_uv[i]:
                r.read(8 * vertex_count)
                print(f"  after uv{i+1}: offset={r.tell()}")

        if has_color:
            r.read(16 * vertex_count)
            print(f"  after color: offset={r.tell()}")

        if has_skin:
            if not single_blend:
                r.read(16 * vertex_count)
                print(f"  after blend weights: offset={r.tell()}")
                num_idx = 4
            else:
                num_idx = 1
            r.read(num_idx * vertex_count)
            print(f"  after blend indices ({num_idx}/vert): offset={r.tell()}")

        if has_generic:
            r.read(4 * vertex_count)
            print(f"  after generic: offset={r.tell()}")

        print(f"  remaining bytes before NumAttachments: {r.remaining()}")

        # Dump the trailing bytes (these are what we don't understand)
        tail_offset = r.tell()
        tail = data[tail_offset:]
        print(f"  TAIL bytes ({len(tail)} bytes from offset {tail_offset}):")
        # Print as hex in 16-byte rows; first 64 bytes
        for off in range(0, min(len(tail), 64), 16):
            row = tail[off:off+16]
            hex_part = ' '.join(f'{b:02x}' for b in row)
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in row)
            print(f"    {off:04x}: {hex_part:<48s}  {ascii_part}")
        # And as u32 little-endian (first ~16 u32s)
        if len(tail) >= 4:
            n_u32 = min(len(tail) // 4, 24)
            words = struct.unpack(f'<{n_u32}I', tail[:n_u32*4])
            print(f"  TAIL as u32 LE: {words}")
        # As floats too
        if len(tail) >= 4:
            n_f32 = min(len(tail) // 4, 24)
            floats = struct.unpack(f'<{n_f32}f', tail[:n_f32*4])
            print(f"  TAIL as f32 LE: {[f'{f:.3f}' for f in floats]}")

    for i, (etype, esize, edata) in enumerate(entities):
        walk(edata, header.version, f"entity #{i} (type={etype})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
