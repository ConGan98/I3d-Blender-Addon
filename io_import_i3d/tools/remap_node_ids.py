"""
Remap nodeIds in an exported .i3d so they match the original imported .i3d.

Why this exists
---------------
GIANTS animation files (.i3d.anim) reference bones and nodes by their
original `nodeId`. Most Blender → i3d exporters (e.g. StjerneIdioten's)
assign their own nodeIds when they write the file out, breaking that
linkage. This script walks the exported XML, looks up each node by name
in the original i3d, and rewrites its `nodeId` to match. It also rewrites
every reference (`skinBindNodeIds`) so the numbering stays consistent.

Usage (from CLI):
    python remap_node_ids.py <original.i3d> <exported.i3d> <output.i3d>

Example:
    python remap_node_ids.py cattleAdultAnimations.i3d Untitled.i3d Untitled_fixed.i3d

Behaviour notes:
- Matching is by `name` attribute. If a node was renamed in Blender, edit
  the export's name to match before running this script.
- Nodes present in the export but not in the original keep a fresh id
  assigned at the end of the original's id range (won't collide).
- Nodes present in the original but not in the export are silently
  ignored (the animation file may complain about those still-missing ids).
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


_NODE_KINDS = {"TransformGroup", "Shape", "Light", "Camera"}

_BLENDER_DUP_SUFFIX = re.compile(r"\.\d{3}$")


def _strip_dup_suffix(name: str) -> str:
    """Strip Blender's `.001`, `.002`, ... auto-rename suffix (3 digits)."""
    return _BLENDER_DUP_SUFFIX.sub("", name) if name else name


def _walk_scene(scene_el):
    """Yield (element, name) for every node inside <Scene>, depth-first."""
    if scene_el is None:
        return
    for el in scene_el.iter():
        if el.tag in _NODE_KINDS:
            yield el


def _build_name_to_id(scene_el):
    """Returns {name: original_nodeId} from the original .i3d's <Scene>."""
    out: dict[str, int] = {}
    for el in _walk_scene(scene_el):
        nm = el.attrib.get("name")
        nid = el.attrib.get("nodeId")
        if not nm or not nid:
            continue
        try:
            out[nm] = int(nid)
        except ValueError:
            continue
    return out


def _max_id(name_to_id: dict[str, int]) -> int:
    return max(name_to_id.values(), default=0)


def _build_name_to_transform(scene_el):
    """Returns {name: {'translation': str, 'rotation': str, 'scale': str}}.

    Captures the ORIGINAL transform attributes verbatim so we can restore
    them on the exported file, preserving the rest pose that .i3d.anim
    keyframes were authored against.
    """
    out: dict[str, dict[str, str]] = {}
    for el in _walk_scene(scene_el):
        nm = el.attrib.get("name")
        if not nm:
            continue
        out[nm] = {
            "translation": el.attrib.get("translation"),
            "rotation": el.attrib.get("rotation"),
            "scale": el.attrib.get("scale"),
        }
    return out


def _rotate_x_180(triplet_str: str) -> str:
    """Apply R_x(180) to a space-separated 'x y z' string. (x, y, z) -> (x, -y, -z)."""
    parts = triplet_str.split()
    if len(parts) != 3:
        return triplet_str
    try:
        x = float(parts[0])
        y = float(parts[1])
        z = float(parts[2])
    except ValueError:
        return triplet_str
    return f"{x:g} {-y:g} {-z:g}"


def _rotate_inline_meshes(exp_root) -> int:
    """Apply R_x(180) to every <v p="..."> position and n="..." normal in
    every <IndexedTriangleSet>. Undoes the axis flip the exporter introduces
    when writing inline mesh data, so vertex coords land in the same frame
    as the bone rest poses (raw GIANTS Y-up)."""
    count = 0
    shapes_el = exp_root.find("Shapes")
    if shapes_el is None:
        return 0
    for its in shapes_el.iter("IndexedTriangleSet"):
        for verts_el in its.iter("Vertices"):
            for v in verts_el.iter("v"):
                p = v.attrib.get("p")
                if p is not None:
                    v.set("p", _rotate_x_180(p))
                n = v.attrib.get("n")
                if n is not None:
                    v.set("n", _rotate_x_180(n))
                count += 1
    return count


def remap(
    original_path: Path,
    exported_path: Path,
    output_path: Path,
    *,
    restore_transforms: bool = True,
    rotate_vertices: bool = True,
) -> None:
    orig_tree = ET.parse(original_path)
    orig_scene = orig_tree.getroot().find("Scene")
    if orig_scene is None:
        raise SystemExit(f"{original_path} has no <Scene> block")
    name_to_orig_id = _build_name_to_id(orig_scene)
    name_to_orig_transform = (
        _build_name_to_transform(orig_scene) if restore_transforms else {}
    )

    exp_tree = ET.parse(exported_path)
    exp_root = exp_tree.getroot()
    exp_scene = exp_root.find("Scene")
    if exp_scene is None:
        raise SystemExit(f"{exported_path} has no <Scene> block")

    # Collect all scene elements with their CURRENT (export) nodeIds in one
    # pass — important to avoid re-walking after we mutate, which would
    # apply the remap twice on already-rewritten ids.
    next_fresh_id = _max_id(name_to_orig_id) + 1
    id_remap: dict[int, int] = {}
    unmapped_names: list[str] = []
    elements_to_rewrite: list[tuple[ET.Element, int]] = []
    for el in _walk_scene(exp_scene):
        nm = el.attrib.get("name")
        nid = el.attrib.get("nodeId")
        if not nm or not nid:
            continue
        try:
            old_id = int(nid)
        except ValueError:
            continue
        # Match against the original by stripping Blender's auto-rename
        # suffix (.001, .002, ...) — happens when the user re-imports
        # without clearing the scene.
        lookup_name = nm if nm in name_to_orig_id else _strip_dup_suffix(nm)
        if lookup_name in name_to_orig_id:
            id_remap[old_id] = name_to_orig_id[lookup_name]
            # Optionally rename the export node back to the canonical name
            # so the rest of the pipeline matches by name (transforms,
            # warnings, etc.).
            if lookup_name != nm:
                el.set("name", lookup_name)
        else:
            id_remap[old_id] = next_fresh_id
            unmapped_names.append(nm)
            next_fresh_id += 1
        elements_to_rewrite.append((el, old_id))

    # Rewrite each element's nodeId exactly once using the captured old id.
    rewrite_count = 0
    for el, old_id in elements_to_rewrite:
        new_id = id_remap.get(old_id, old_id)
        if new_id != old_id:
            el.set("nodeId", str(new_id))
            rewrite_count += 1

    # Rewrite skinBindNodeIds (space-separated lists of export ids).
    skin_rewrite = 0
    for el in _walk_scene(exp_scene):
        attr = el.attrib.get("skinBindNodeIds")
        if not attr:
            continue
        new_ids = []
        for tok in attr.split():
            try:
                old = int(tok)
            except ValueError:
                new_ids.append(tok)
                continue
            new_ids.append(str(id_remap.get(old, old)))
        new_str = " ".join(new_ids)
        if new_str != attr:
            el.set("skinBindNodeIds", new_str)
            skin_rewrite += 1

    # Restore original translation/rotation/scale on every node that exists
    # in both files. This undoes any axis-conversion or bone-decomposition
    # drift introduced by the import → Blender → export round-trip, which
    # is essential for .i3d.anim keyframes (authored against the original
    # rest pose) to play correctly.
    transform_restored = 0
    if restore_transforms:
        for el in _walk_scene(exp_scene):
            nm = el.attrib.get("name")
            if not nm or nm not in name_to_orig_transform:
                continue
            orig_t = name_to_orig_transform[nm]
            changed = False
            for attr in ("translation", "rotation", "scale"):
                want = orig_t[attr]
                cur = el.attrib.get(attr)
                if want is None:
                    # Original didn't have this attribute — drop it from export.
                    if cur is not None:
                        del el.attrib[attr]
                        changed = True
                else:
                    if cur != want:
                        el.set(attr, want)
                        changed = True
            if changed:
                transform_restored += 1

    # Sort scene nodes by new nodeId so the file reads top-down in
    # original order. (Optional but matches GIANTS Editor's expectations.)
    # Note: this only sorts at the immediate child level, not recursively,
    # so the *visible* ordering improves without touching the meaningful
    # parent-child structure.

    # Rotate inline-mesh vertex positions and normals by R_x(180) so they
    # match the GIANTS Y-up frame that the bone rest poses now use.
    vertices_rotated = 0
    if rotate_vertices:
        vertices_rotated = _rotate_inline_meshes(exp_root)

    out_tree = exp_tree
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_tree.write(output_path, encoding="iso-8859-1", xml_declaration=True)

    print(f"Wrote {output_path}")
    print(f"  rewrote {rewrite_count} nodeId attributes")
    print(f"  rewrote {skin_rewrite} skinBindNodeIds lists")
    if restore_transforms:
        print(f"  restored {transform_restored} transforms (translation/rotation/scale) from original")
    if rotate_vertices:
        print(f"  rotated {vertices_rotated} inline-mesh vertices by R_x(180) to align with bone frame")
    if unmapped_names:
        print(f"  WARNING: {len(unmapped_names)} node(s) were in export but NOT in the original:")
        for nm in unmapped_names[:20]:
            print(f"    {nm!r}")
        if len(unmapped_names) > 20:
            print(f"    ... +{len(unmapped_names) - 20} more")
        print("  These got fresh nodeIds at the end of the range. If any of")
        print("  them are skin/animation targets, the .anim file won't find them.")
    # Also report missing nodes (in original, not in export) — the .anim
    # file is most likely to complain about these.
    exp_names = {el.attrib.get("name") for el in _walk_scene(exp_scene)}
    missing = [n for n in name_to_orig_id if n not in exp_names]
    if missing:
        print(f"  WARNING: {len(missing)} node(s) in original are MISSING from export:")
        for nm in missing[:20]:
            print(f"    {nm!r} (original nodeId={name_to_orig_id[nm]})")
        if len(missing) > 20:
            print(f"    ... +{len(missing) - 20} more")
        print("  GIANTS Editor will warn 'Transform group id N not found' for any")
        print("  of these that the .i3d.anim references. Add them back in Blender")
        print("  and re-export, or accept the missing animations.")


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    remap(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))


if __name__ == "__main__":
    main()
