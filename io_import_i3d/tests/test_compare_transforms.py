"""
Side-by-side compare of bone & node transforms between original and fixed i3d.

Reveals which nodes have changed translation/rotation/scale, which is the
root cause of "mesh messes up when animation plays" — animation keyframes are
applied as deltas relative to the rest pose, so a different rest pose
produces different motion.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

ORIG = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d"
FIXED = Path(__file__).resolve().parents[2] / "Untitled_fixed.i3d"


def _parse_vec3(s, default=(0.0, 0.0, 0.0)):
    if not s:
        return default
    parts = s.split()
    if len(parts) != 3:
        return default
    return tuple(float(p) for p in parts)


def _walk_scene(scene_el):
    if scene_el is None:
        return
    for el in scene_el.iter():
        if el.tag in {"TransformGroup", "Shape", "Light", "Camera"}:
            yield el


def _collect(scene_el):
    out = {}
    for el in _walk_scene(scene_el):
        nm = el.attrib.get("name")
        if not nm:
            continue
        out[nm] = {
            "kind": el.tag,
            "nodeId": el.attrib.get("nodeId", ""),
            "translation": _parse_vec3(el.attrib.get("translation"), (0.0, 0.0, 0.0)),
            "rotation": _parse_vec3(el.attrib.get("rotation"), (0.0, 0.0, 0.0)),
            "scale": _parse_vec3(el.attrib.get("scale"), (1.0, 1.0, 1.0)),
        }
    return out


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def main():
    orig_tree = ET.parse(ORIG)
    fixed_tree = ET.parse(FIXED)
    orig = _collect(orig_tree.getroot().find("Scene"))
    fixed = _collect(fixed_tree.getroot().find("Scene"))

    common = sorted(set(orig.keys()) & set(fixed.keys()))
    only_orig = sorted(set(orig.keys()) - set(fixed.keys()))
    only_fixed = sorted(set(fixed.keys()) - set(orig.keys()))

    if only_orig:
        print(f"Missing from fixed ({len(only_orig)}): {only_orig[:5]}...")
    if only_fixed:
        print(f"Only in fixed ({len(only_fixed)}): {only_fixed[:5]}...")

    print(f"\nComparing {len(common)} common nodes — translation/rotation/scale deltas:\n")
    print(f"  {'name':<40s} {'dtrans':>10s} {'drot':>10s} {'dscale':>10s}")
    print("  " + "-" * 78)
    big_diffs = []
    for nm in common:
        a = orig[nm]
        b = fixed[nm]
        dt = _dist(a["translation"], b["translation"])
        dr = _dist(a["rotation"], b["rotation"])
        ds = _dist(a["scale"], b["scale"])
        if dt > 0.001 or dr > 0.1 or ds > 0.001:
            print(f"  {nm:<40s} {dt:>10.4f} {dr:>10.4f} {ds:>10.4f}")
            big_diffs.append((nm, a, b, dt, dr))

    if big_diffs:
        print(f"\n{len(big_diffs)} nodes have non-trivial transform differences.")
        print("\nFirst 5 with full triplets:")
        for nm, a, b, dt, dr in big_diffs[:5]:
            print(f"\n  {nm} ({a['kind']}, nodeId orig={a['nodeId']} fixed={b['nodeId']}):")
            print(f"    orig:  trans={a['translation']}  rot={a['rotation']}  scale={a['scale']}")
            print(f"    fixed: trans={b['translation']}  rot={b['rotation']}  scale={b['scale']}")
    else:
        print("\nNo transform differences. The rest pose round-trips cleanly.")


if __name__ == "__main__":
    main()
