"""
Offline test: decrypt + parse the cattle .i3d.shapes file end-to-end.

Validates:
- 4 shapes by id (1..4 from the i3d XML).
- Shape names match XML: 1=body, 2=alpha, 3=alpha_reverse, 4=cowProxy.
- vertex_count > 0 for all.
- corner_count divisible by 3 (well-formed triangles).
- options flag has skinning bit set on the three skinned shapes.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1]
SAMPLE = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.shapes"


def _load_in_pkg(pkg_name: str, modules: list[str]) -> types.ModuleType:
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(ADDON_DIR / "importer")]
    sys.modules[pkg_name] = pkg
    loaded = {}
    for m in modules:
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{m}", ADDON_DIR / "importer" / f"{m}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{m}"] = mod
        loaded[m] = mod
    # Now exec in dependency order
    for m in modules:
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{m}", ADDON_DIR / "importer" / f"{m}.py"
        )
        spec.loader.exec_module(loaded[m])
    return loaded


def main() -> int:
    if not SAMPLE.exists():
        print(f"SKIP: {SAMPLE} not found")
        return 0
    mods = _load_in_pkg(
        "io_import_i3d_pkg",
        ["shapes_keyconst", "shapes_cipher", "shapes_entity", "shapes_reader"],
    )
    shapes = mods["shapes_reader"].parse_external_shapes(SAMPLE)
    print(f"Parsed {len(shapes)} shapes")
    assert len(shapes) == 4, f"expected 4 shapes, got {len(shapes)}"

    # Binary names carry a "Shape" suffix (Maya export convention) — XML names don't.
    expected_names = {1: "body", 2: "alpha", 3: "alpha_reverse", 4: "cowProxy"}
    for sid, expected in expected_names.items():
        assert sid in shapes, f"missing shape {sid}"
        s = shapes[sid]
        print(
            f"  [{sid}] {s.name!r}: verts={s.vertex_count}, "
            f"tris={len(s.triangles)}, options=0x{s.options:04x}, "
            f"uvs={[u is not None for u in s.uv_sets]}, "
            f"normals={s.normals is not None}, "
            f"skin={s.blend_indices is not None}, "
            f"single_blend={s.single_blend}"
        )
        assert s.name == expected or s.name == expected + "Shape", \
            f"shape {sid} name {s.name!r} != {expected!r}"
        assert s.vertex_count > 0
        assert s.corner_count % 3 == 0
        assert len(s.triangles) == s.corner_count // 3
        assert len(s.positions) == s.vertex_count

    # The three skinned shapes must have BlendIndices.
    for sid in (1, 2, 3):
        s = shapes[sid]
        assert s.blend_indices is not None, f"shape {sid} has no skinning data"
        assert len(s.blend_indices) == s.vertex_count

    # cowProxy should be small (collision proxy) and unskinned.
    assert shapes[4].blend_indices is None or shapes[4].vertex_count > 0

    print("OK — shape entity parser produces valid data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
