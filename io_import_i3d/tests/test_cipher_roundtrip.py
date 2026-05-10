"""
Offline test of the .i3d.shapes cipher port.

Validates:
- KEY_CONST table integrity (length + pinned SHA-256).
- FileHeader parses the user's cattle file as version=9, seed=0x49.
- Cipher decrypts the first 4 bytes (entity count) to a sane value (1..1000).
- Cipher decrypts the next bytes (entity-type tag + name) into something
  resembling a structured i3d entity entry.

Run: python io_import_i3d/tests/test_cipher_roundtrip.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import struct
import sys
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1]
SAMPLE = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.shapes"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Make `from .shapes_keyconst import KEY_CONST` resolvable in shapes_cipher.
import os
os.environ.setdefault("PYTHONPATH", str(ADDON_DIR / "importer"))
keyconst = _load("io_import_i3d.importer.shapes_keyconst", ADDON_DIR / "importer" / "shapes_keyconst.py")
sys.modules["shapes_keyconst"] = keyconst
# shapes_cipher does `from .shapes_keyconst import KEY_CONST` — patch the relative import
# by injecting a parent package shim.
import types
pkg = types.ModuleType("io_import_i3d_pkg")
pkg.__path__ = [str(ADDON_DIR / "importer")]
sys.modules["io_import_i3d_pkg"] = pkg
sys.modules["io_import_i3d_pkg.shapes_keyconst"] = keyconst
spec = importlib.util.spec_from_file_location(
    "io_import_i3d_pkg.shapes_cipher",
    ADDON_DIR / "importer" / "shapes_cipher.py",
)
cipher_mod = importlib.util.module_from_spec(spec)
sys.modules["io_import_i3d_pkg.shapes_cipher"] = cipher_mod
spec.loader.exec_module(cipher_mod)


def main() -> int:
    if not SAMPLE.exists():
        print(f"SKIP: sample not found at {SAMPLE}")
        return 0

    # 1. KEY_CONST integrity
    assert len(keyconst.KEY_CONST) == 4096, len(keyconst.KEY_CONST)
    packed = struct.pack(f"<{len(keyconst.KEY_CONST)}I", *keyconst.KEY_CONST)
    digest = hashlib.sha256(packed).hexdigest()
    expected = "1aae1e71fe620d1b77797b1ffed6e9f7fe23e2713b4f46b8edadb1083b23ccc1"
    assert digest == expected, f"KEY_CONST sha256 drifted: {digest}"
    print(f"KEY_CONST sha256 OK: {digest}")

    # 2. File header
    with open(SAMPLE, "rb") as f:
        header = cipher_mod.FileHeader.read(f)
        print(f"Header: version={header.version}, seed=0x{header.seed:02X}")
        assert header.version == 9
        assert header.seed == 0x49

        cipher = cipher_mod.I3DCipher(header.seed)
        stream = cipher_mod.CipherStream(f, cipher)

        # 3. Decrypt first u32 (entity count). i3d XML lists 4 Shape nodes,
        #    so we expect entityCount in a small range — typically 4..50.
        first4 = stream.read(4)
        assert len(first4) == 4
        entity_count = struct.unpack("<I", first4)[0]
        print(f"Entity count (decrypted u32 #1): {entity_count}")
        if not (1 <= entity_count <= 1000):
            print(f"WARN: entity count {entity_count} outside expected sane range 1..1000")
            return 1

        # 4. Decrypt the entity-type tag of the first entity (separate 4-byte
        #    read, matching how Donkie's Entity.cs reads it).
        type_bytes = stream.read(4)
        if len(type_bytes) == 4:
            etype = struct.unpack("<I", type_bytes)[0]
            print(f"  first entity type: {etype} (1=Shape, 2=Spline)")
            assert etype in (1, 2), f"unexpected entity type {etype}"

        # entity_count == 4 matches the four <Shape> nodes in the XML
        # (body, alpha, alpha_reverse, cowProxy) — strong evidence the
        # cipher port is byte-correct. Further entity-payload decoding
        # (7-bit-length-prefixed name, padded payload) lives in
        # shapes_entity.py (M3).
        assert entity_count == 4, f"expected 4 entities, got {entity_count}"

    print("OK — cipher port produces structurally plausible decrypt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
