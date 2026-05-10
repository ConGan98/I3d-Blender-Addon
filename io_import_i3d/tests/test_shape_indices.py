"""
Offline sanity check: triangle indices must be in range [0, vertex_count).
If any are out of range, that's a candidate root cause for the Blender crash
in mesh_set_custom_normals_from_verts (vert_to_face_map cache build).
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
    rc = 0
    for sid, s in shapes.items():
        max_idx = -1
        min_idx = float('inf')
        n_oor = 0
        n_degen = 0
        bad_tri_indices = []
        for ti, tri in enumerate(s.triangles):
            tri_bad = False
            for i in tri:
                if i >= s.vertex_count or i < 0:
                    n_oor += 1
                    tri_bad = True
                max_idx = max(max_idx, i)
                min_idx = min(min_idx, i)
            if tri_bad:
                bad_tri_indices.append((ti, tri))
            if len(set(tri)) < 3:
                n_degen += 1
        # GIANTS exports include a single (0,0,0) placeholder degenerate
        # triangle at index 0; the mesh builder drops it. Anything more is
        # suspicious.
        status = "OK" if (n_oor == 0 and n_degen <= 1) else "FAIL"
        print(f"  [{sid}] {s.name!r}: vc={s.vertex_count}, max_idx={max_idx}, min_idx={min_idx}, "
              f"out_of_range={n_oor}, degenerate_tris={n_degen}  {status}")
        # Show which triangles are bad
        if bad_tri_indices:
            print("    bad triangles (idx, values):")
            for ti, tri in bad_tri_indices[:5]:
                print(f"      tri[{ti}/{len(s.triangles)}] = {tri}")
        # Show first/last 3 triangles for context
        print(f"    first 3 tris: {s.triangles[:3]}")
        print(f"    last 3 tris:  {s.triangles[-3:]}")
        if n_oor or n_degen > 1:
            rc = 1
        # Also sanity-check normals length
        if s.normals is not None and len(s.normals) != s.vertex_count:
            print(f"    !! normals len {len(s.normals)} != vertex_count {s.vertex_count}")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
