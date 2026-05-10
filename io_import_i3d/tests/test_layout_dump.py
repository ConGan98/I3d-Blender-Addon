"""Dump bytes 0..120 of each shape's payload + bytes around BlendIndices to
nail down the v9 subset/header structure."""
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
    mods = _load("io_import_i3d_pkg", ["shapes_keyconst", "shapes_cipher"])
    cipher_mod = mods["shapes_cipher"]

    with open(SAMPLE, "rb") as f:
        header = cipher_mod.FileHeader.read(f)
        cipher = cipher_mod.I3DCipher(header.seed)
        stream = cipher_mod.CipherStream(f, cipher)
        struct.unpack("<I", stream.read(4))  # entity_count
        entities = []
        for _ in range(4):
            etype = struct.unpack("<I", stream.read(4))[0]
            esize = struct.unpack("<I", stream.read(4))[0]
            edata = stream.read(esize)
            entities.append((etype, esize, edata))

    # entities order: alpha, alpha_reverse, body, cowProxy
    for idx, (et, sz, dat) in enumerate(entities):
        # Detect shape — read name from header
        name_len = struct.unpack("<I", dat[0:4])[0]
        name = dat[4:4+name_len].decode('ascii', errors='replace')
        # Header end (after align + shape_id u32)
        hdr_end = ((4 + name_len + 3) // 4) * 4 + 4
        bv = struct.unpack("<ffff", dat[hdr_end:hdr_end+16])
        cc, ns, vc, opt = struct.unpack("<IIII", dat[hdr_end+16:hdr_end+32])
        opts_end = hdr_end + 32

        print(f"\n=== shape #{idx}: {name!r} ===")
        print(f"  size={sz}, hdr_end={hdr_end}, opts_end={opts_end}")
        print(f"  cc={cc}, ns={ns}, vc={vc}, opt=0x{opt:x}")

        # Dump bytes opts_end..opts_end+96 as u32, f32 simultaneously
        print(f"  --- bytes from end-of-options ({opts_end}) up to +96 ---")
        for off in range(opts_end, min(opts_end + 96, len(dat) - 4), 4):
            chunk = dat[off:off+4]
            u32 = struct.unpack("<I", chunk)[0]
            f32 = struct.unpack("<f", chunk)[0]
            print(f"    [{off:5d} = +{off-opts_end:3d}]  u32={u32:11d}  f32={f32:12.4g}  hex={chunk.hex()}")


if __name__ == "__main__":
    main()
