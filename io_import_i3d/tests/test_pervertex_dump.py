"""Hex/struct dump of body's per-vertex section transitions."""
from __future__ import annotations

import importlib.util
import struct
import sys
import types
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1]
SAMPLE = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.shapes"


def _load(pkg, mods):
    p = types.ModuleType(pkg); p.__path__ = [str(ADDON_DIR / "importer")]
    sys.modules[pkg] = p
    out = {}
    for m in mods:
        spec = importlib.util.spec_from_file_location(f"{pkg}.{m}", ADDON_DIR / "importer" / f"{m}.py")
        mod = importlib.util.module_from_spec(spec); sys.modules[f"{pkg}.{m}"] = mod
        out[m] = mod
    for m in mods:
        spec = importlib.util.spec_from_file_location(f"{pkg}.{m}", ADDON_DIR / "importer" / f"{m}.py")
        spec.loader.exec_module(out[m])
    return out


def main():
    mods = _load("io_import_i3d_pkg", ["shapes_keyconst", "shapes_cipher", "shapes_entity"])
    cipher_mod = mods["shapes_cipher"]

    # Get body entity's raw decrypted data
    with open(SAMPLE, "rb") as f:
        header = cipher_mod.FileHeader.read(f)
        cipher = cipher_mod.I3DCipher(header.seed)
        stream = cipher_mod.CipherStream(f, cipher)
        struct.unpack("<I", stream.read(4))  # entity_count
        # Skip first 2 entities to reach body (entity #2)
        for _ in range(2):
            etype = struct.unpack("<I", stream.read(4))[0]
            esize = struct.unpack("<I", stream.read(4))[0]
            stream.read(esize)
        # body
        etype = struct.unpack("<I", stream.read(4))[0]
        esize = struct.unpack("<I", stream.read(4))[0]
        body_data = stream.read(esize)
        print(f"body entity: type={etype}, size={esize}")

    # Two layout hypotheses for v9:
    # A) options, VtxCompression, subsets, triangles  (current parser)
    # B) options, subsets, VtxCompression, triangles  (alternative)
    # For body: name=9 ("bodyShape"), header=20, BV=16, 4u32=16. After header+meta = 52.
    # subset for body (HasUV1, v>=6) = 16+4 = 20.
    # triangles = 13280*6 = 79680.
    #
    # Option A: VtxComp(4) at 52-56, subsets at 56-76, tris at 76-79756.
    # Option B: subsets at 52-72, VtxComp(4) at 72-76, tris at 76-79756.
    # Both produce tris at 76 -> we already verified tri[0]=(0,0,0).

    print("\n--- bytes 48..80 (around options/subsets/VtxCompression boundary) ---")
    for off in range(48, 80, 4):
        chunk = body_data[off:off+4]
        u32 = struct.unpack("<I", chunk)[0]
        f32 = struct.unpack("<f", chunk)[0]
        i32 = struct.unpack("<i", chunk)[0]
        print(f"  [{off:5d}] {chunk.hex()}  u32={u32}  i32={i32}  f32={f32:.4g}")

    # Per-vertex sections (NEW parser, Option A):
    # tris end at 79756 (since 76 + 79680 = 79756). align(4) no pad.
    # positions: 79756 + 8259*12 = 178864
    # normals: 178864 + 99108 = 277972
    # tangents: 277972 + 8259*16 = 410116
    # uv1: 410116 + 8259*8 = 476188
    # weights: 476188 + 8259*16 = 608332
    # indices: 608332 + 8259*4 = 641368
    # trailer (4 bytes): 641368-641372
    # num_att (4 bytes): 641372-641376 ✓ (entity size)

    weights_start = 79756 + 8259*12 + 8259*12 + 8259*16 + 8259*8
    print(f"\n--- bytes around BlendWeights start ({weights_start}) ---")
    for off in range(weights_start - 16, weights_start + 32, 4):
        if off < 0 or off + 4 > len(body_data):
            continue
        chunk = body_data[off:off+4]
        u32 = struct.unpack("<I", chunk)[0]
        f32 = struct.unpack("<f", chunk)[0]
        marker = "  <- weights[0] start" if off == weights_start else ""
        print(f"  [{off:6d}] {chunk.hex()}  u32={u32}  f32={f32:.4g}{marker}")

    indices_start = weights_start + 8259*16
    print(f"\n--- bytes around BlendIndices start ({indices_start}) ---")
    for off in range(indices_start - 16, indices_start + 32, 4):
        if off < 0 or off + 4 > len(body_data):
            continue
        chunk = body_data[off:off+4]
        u32 = struct.unpack("<I", chunk)[0]
        f32 = struct.unpack("<f", chunk)[0]
        marker = "  <- indices[0] start" if off == indices_start else ""
        # As bytes (4 bone indices)
        bones = struct.unpack("<BBBB", chunk)
        print(f"  [{off:6d}] {chunk.hex()}  u32={u32}  f32={f32:.4g}  bytes={bones}{marker}")

    # Entity end: should reach exactly esize.
    trailer_start = indices_start + 8259*4
    print(f"\n--- bytes at trailer ({trailer_start}) ---")
    chunk = body_data[trailer_start:trailer_start+8]
    print(f"  trailer 8 bytes: {chunk.hex()}  u32x2={struct.unpack('<II', chunk)}  f32x2={struct.unpack('<ff', chunk)}")
    print(f"  end of data: {len(body_data)}, expected end: {trailer_start+8}")


if __name__ == "__main__":
    main()
