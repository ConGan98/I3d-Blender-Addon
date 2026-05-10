"""
Sanity-check decoded vertex positions: print first/last positions per shape +
bounding box, and compare to the BoundingVolume reported in the file.

A cow body should be roughly: -1m to 1m in X (width), 0 to 2m in Y (height
ground-to-back in GIANTS Y-up), -1m to 1m in Z (length). The cowProxy is a
collision capsule around it.
"""
from __future__ import annotations

import importlib.util
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
    mods = _load("io_import_i3d_pkg", ["shapes_keyconst", "shapes_cipher", "shapes_entity", "shapes_reader"])
    shapes = mods["shapes_reader"].parse_external_shapes(SAMPLE)
    for sid, s in shapes.items():
        print(f"\n[{sid}] {s.name!r}: vc={s.vertex_count}, vtx_compression={s.vtx_compression}")
        print(f"  BoundingVolume (file):  ({s.bounding_volume[0]:.3f}, {s.bounding_volume[1]:.3f}, {s.bounding_volume[2]:.3f}, r={s.bounding_volume[3]:.3f})")

        xs = [p[0] for p in s.positions]
        ys = [p[1] for p in s.positions]
        zs = [p[2] for p in s.positions]
        print(f"  Positions bbox:         ({min(xs):.3f}..{max(xs):.3f}, {min(ys):.3f}..{max(ys):.3f}, {min(zs):.3f}..{max(zs):.3f})")
        print(f"  First 3 positions: {[tuple(round(c, 4) for c in p) for p in s.positions[:3]]}")
        print(f"  Last  3 positions: {[tuple(round(c, 4) for c in p) for p in s.positions[-3:]]}")

        # Check for NaN / Inf
        nan_count = sum(1 for p in s.positions if any(c != c or abs(c) == float('inf') for c in p))
        if nan_count:
            print(f"  ** {nan_count} positions contain NaN/Inf!")

        # Check magnitudes — anything > 1000m is suspicious
        big = [p for p in s.positions if max(abs(p[0]), abs(p[1]), abs(p[2])) > 1000]
        if big:
            print(f"  ** {len(big)} positions have a coord > 1000m, e.g. {big[0]}")

        # If skinned: dump first vertex's blend indices/weights
        if s.blend_indices is not None:
            bi0 = s.blend_indices[0]
            bw0 = s.blend_weights[0] if s.blend_weights else (1.0,)
            print(f"  Vertex 0 blend: indices={tuple(bi0)}, weights={tuple(round(w,3) for w in bw0)}, weight_sum={sum(bw0):.4f}")
            # Sum-of-weights check across all vertices
            if not s.single_blend:
                sums = [sum(s.blend_weights[i]) for i in range(min(s.vertex_count, 1000))]
                bad = [v for v in sums if abs(v - 1.0) > 1e-3]
                print(f"  Weight-sum check (first {len(sums)} verts): {len(bad)} not~1.0")


if __name__ == "__main__":
    main()
