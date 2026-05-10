"""
Offline test (runs without Blender). Parses cattleAdultAnimations.i3d and
asserts structural facts we'll rely on in M1+.

Run: python -m io_import_i3d.tests.test_xml_parse
or:  python tests/test_xml_parse.py from inside the addon folder.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1]
SAMPLE = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d"


def _load(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


xp = _load("xml_parser", ADDON_DIR / "importer" / "xml_parser.py")


def main() -> int:
    if not SAMPLE.exists():
        print(f"SKIP: sample not found at {SAMPLE}")
        return 0

    doc = xp.parse_i3d(SAMPLE)

    assert doc.version == "1.6", f"version: {doc.version}"
    assert doc.name == "cattleAdultAnimations", doc.name
    assert doc.external_shapes_file == "cattleAdultAnimations.i3d.shapes"
    assert doc.animation is not None
    assert doc.animation.external_file == "cattleAdultAnimations.i3d.anim"

    assert len(doc.files) == 5, f"files: {len(doc.files)}"
    assert len(doc.materials) == 3, f"materials: {len(doc.materials)}"

    all_nodes = doc.all_nodes()
    print(f"Total scene nodes: {len(all_nodes)}")
    by_kind: dict[str, int] = {}
    for n in all_nodes:
        by_kind[n.kind] = by_kind.get(n.kind, 0) + 1
    print(f"By kind: {by_kind}")

    by_id = doc.by_node_id()
    assert 1 in by_id and by_id[1].name == "cattleSkeleton"
    assert 52 in by_id and by_id[52].kind == "Shape" and by_id[52].name == "body"
    assert by_id[52].skin_bind_node_ids == list(range(2, 51))

    spine = by_id[3]
    assert spine.name == "cow_spine_skin_jnt_01"
    assert abs(spine.translation[1] - 1.026156) < 1e-6
    assert abs(spine.rotation[1] - (-90.0)) < 1e-6

    proxy = by_id[55]
    assert proxy.name == "cowProxy"
    assert proxy.non_renderable is True
    assert proxy.visibility is False

    print("OK — XML parse looks correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
