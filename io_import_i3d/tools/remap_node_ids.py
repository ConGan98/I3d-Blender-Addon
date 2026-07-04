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
_ORDER_PREFIX = re.compile(r"^\d+_")


def _strip_dup_suffix(name: str) -> str:
    """Strip Blender's `.001`, `.002`, ... auto-rename suffix (3 digits)."""
    return _BLENDER_DUP_SUFFIX.sub("", name) if name else name


def _strip_order_prefix(name: str) -> str:
    """Strip a leading numeric ordering prefix like `01_`, `02_`.

    Some Blender/GIANTS workflows prefix top-level nodes (`01_cattleSkeleton`,
    `02_cattleHolstein`) for readability. The original .i3d has the bare name
    (`cattleSkeleton`), so matching must tolerate the prefix or the skeleton
    root / mesh-group nodeIds won't be restored and the .anim can't bind.
    """
    return _ORDER_PREFIX.sub("", name) if name else name


def _name_candidates(name: str):
    """Yield lookup keys for `name`, most specific first, de-duplicated."""
    seen = set()
    for cand in (
        name,
        _strip_dup_suffix(name),
        _strip_order_prefix(name),
        _strip_order_prefix(_strip_dup_suffix(name)),
    ):
        if cand and cand not in seen:
            seen.add(cand)
            yield cand


def _match_orig_el(el, name_to_orig_el, shapeid_to_orig_el):
    """Find the original element that an exported node corresponds to.

    Shape nodes are matched by `shapeId` first: the exporter preserves shape
    references verbatim, whereas Blender mangles duplicate object *names*
    (`body` → `body.001`) so name-stripping would collapse distinct LOD
    meshes (`body`, `body1`, `body2`) onto the same original node and clobber
    their per-node transforms. Everything else (bones/joints, whose names are
    unique and preserved) matches by name, with the dup-suffix fallback.
    """
    if el.tag == "Shape":
        sid = el.attrib.get("shapeId")
        if sid:
            try:
                m = shapeid_to_orig_el.get(int(sid))
            except ValueError:
                m = None
            if m is not None:
                return m
    nm = el.attrib.get("name")
    if not nm:
        return None
    for cand in _name_candidates(nm):
        if cand in name_to_orig_el:
            return name_to_orig_el[cand]
    return None


def _orig_node_id(el):
    v = el.attrib.get("nodeId")
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _walk_scene(scene_el):
    """Yield (element, name) for every node inside <Scene>, depth-first."""
    if scene_el is None:
        return
    for el in scene_el.iter():
        if el.tag in _NODE_KINDS:
            yield el


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

    # Build element lookups from the original. Names key bones/joints (unique,
    # preserved across the round-trip); shapeIds key Shape meshes (stable even
    # when Blender mangles duplicate object names).
    orig_els = list(_walk_scene(orig_scene))
    name_to_orig_el: dict[str, ET.Element] = {}
    for el in orig_els:
        nm = el.attrib.get("name")
        if nm and nm not in name_to_orig_el:
            name_to_orig_el[nm] = el
    shapeid_to_orig_el: dict[int, ET.Element] = {}
    for el in orig_els:
        if el.tag != "Shape":
            continue
        sid = el.attrib.get("shapeId")
        if not sid:
            continue
        try:
            shapeid_to_orig_el[int(sid)] = el
        except ValueError:
            continue
    max_orig_id = max(
        (i for i in (_orig_node_id(el) for el in orig_els) if i is not None),
        default=0,
    )

    exp_tree = ET.parse(exported_path)
    exp_root = exp_tree.getroot()
    exp_scene = exp_root.find("Scene")
    if exp_scene is None:
        raise SystemExit(f"{exported_path} has no <Scene> block")

    # Collect all scene elements with their CURRENT (export) nodeIds in one
    # pass — important to avoid re-walking after we mutate, which would
    # apply the remap twice on already-rewritten ids. We also stash each
    # element's matched original counterpart for the transform-restore pass.
    next_fresh_id = max_orig_id + 1
    id_remap: dict[int, int] = {}
    unmapped_names: list[str] = []
    elements_to_rewrite: list[tuple[ET.Element, int, ET.Element | None]] = []
    used_new_ids: set[int] = set()
    collided_names: list[str] = []

    def _fresh_id() -> int:
        nonlocal next_fresh_id
        while next_fresh_id in used_new_ids:
            next_fresh_id += 1
        nid = next_fresh_id
        next_fresh_id += 1
        return nid

    for el in _walk_scene(exp_scene):
        nm = el.attrib.get("name")
        nid = el.attrib.get("nodeId")
        if not nm or not nid:
            continue
        try:
            old_id = int(nid)
        except ValueError:
            continue
        orig_el = _match_orig_el(el, name_to_orig_el, shapeid_to_orig_el)
        orig_id = _orig_node_id(orig_el) if orig_el is not None else None
        if orig_id is not None and orig_id not in used_new_ids:
            id_remap[old_id] = orig_id
            used_new_ids.add(orig_id)
            # Rename the export node back to the original's canonical name so
            # LOD/duplicate suffixes (body.001) don't leak into the .i3d.
            canon = orig_el.attrib.get("name")
            if canon and canon != nm:
                el.set("name", canon)
        else:
            # No match, OR the matched original id was already claimed by an
            # earlier node — happens when the export carries meshes the
            # reference lacks (e.g. horns) so shapeIds/names collide. Never
            # emit a duplicate nodeId: assign a fresh one and don't restore a
            # transform from a mismatched original.
            if orig_id is not None:
                collided_names.append(nm)
                orig_el = None
            else:
                unmapped_names.append(nm)
            id_remap[old_id] = _fresh_id()
        elements_to_rewrite.append((el, old_id, orig_el))

    # Identify the skeleton subtree — the only nodes whose transforms get
    # restored. Meshes skin to their bones via `skinBindNodeIds`, so those
    # referenced ids are the animation joints; any node whose subtree contains
    # a joint is part of the skeleton (the joints + their container). The mesh
    # hierarchy (LOD groups, Shapes, horns, cowProxy) contains no such node and
    # is left exactly as the exporter oriented it.
    skin_target_ids: set[int] = set()
    for el in _walk_scene(exp_scene):
        attr = el.attrib.get("skinBindNodeIds")
        if not attr:
            continue
        for tok in attr.split():
            try:
                skin_target_ids.add(int(tok))
            except ValueError:
                continue

    skeletal_old_ids: set[int] = set()

    def _mark_skeletal(el) -> bool:
        nid = el.attrib.get("nodeId")
        try:
            my_id = int(nid) if nid is not None else None
        except ValueError:
            my_id = None
        contains = my_id is not None and my_id in skin_target_ids
        for child in el:
            if child.tag in _NODE_KINDS and _mark_skeletal(child):
                contains = True
        if contains and my_id is not None:
            skeletal_old_ids.add(my_id)
        return contains

    for el in exp_scene:
        if el.tag in _NODE_KINDS:
            _mark_skeletal(el)

    # Rewrite each element's nodeId exactly once using the captured old id.
    rewrite_count = 0
    for el, old_id, _orig in elements_to_rewrite:
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

    # Restore original translation/rotation/scale from each node's matched
    # original counterpart. This undoes axis-conversion / bone-decomposition
    # drift so .i3d.anim keyframes (authored against the original rest pose)
    # play correctly.
    #
    # Restricted to the SKELETON subtree (joints + their container). The entire
    # mesh hierarchy — LOD/mesh groups, Shapes, horns, cowProxy — is left with
    # the orientation the exporter wrote. Restoring a mesh group (e.g. the
    # `cattleHolstein` group's +90 X axis residue) would rotate everything
    # under it; restoring a Shape from a mismatched reference would tip a LOD
    # body. Only the animation joints need their original rest transforms.
    transform_restored = 0
    if restore_transforms:
        for el, old_id, orig_el in elements_to_rewrite:
            if orig_el is None or old_id not in skeletal_old_ids:
                continue
            changed = False
            for attr in ("translation", "rotation", "scale"):
                want = orig_el.attrib.get(attr)
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
    if collided_names:
        print(f"  WARNING: {len(collided_names)} node(s) collided with an already-used id and")
        print("  got FRESH ids instead (kept the file valid). This means the export")
        print("  contains meshes the reference .i3d does not (e.g. extra LOD/horn")
        print("  shapes), so their shapeIds/names don't line up. The skeleton and")
        print("  animation are unaffected, but for clean mesh names/ids the")
        print("  reference must be the exact model you imported. Nodes:")
        for nm in collided_names[:20]:
            print(f"    {nm!r}")
        if len(collided_names) > 20:
            print(f"    ... +{len(collided_names) - 20} more")
    # Also report missing nodes (in original, not in export) — the .anim
    # file is most likely to complain about these.
    exp_names = {el.attrib.get("name") for el in _walk_scene(exp_scene)}
    missing = [n for n in name_to_orig_el if n not in exp_names]
    if missing:
        print(f"  WARNING: {len(missing)} node(s) in original are MISSING from export:")
        for nm in missing[:20]:
            print(f"    {nm!r} (original nodeId={_orig_node_id(name_to_orig_el[nm])})")
        if len(missing) > 20:
            print(f"    ... +{len(missing) - 20} more")
        print("  GIANTS Editor will warn 'Transform group id N not found' for any")
        print("  of these that the .i3d.anim references. Add them back in Blender")
        print("  and re-export, or accept the missing animations.")


def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="Remap exported .i3d nodeIds back to the original's numbering.",
    )
    ap.add_argument("original", type=Path, help="the original imported .i3d")
    ap.add_argument("exported", type=Path, help="the freshly exported .i3d")
    ap.add_argument("output", type=Path, help="destination path for the fixed .i3d")
    ap.add_argument(
        "--vertex-rotate",
        choices=("none", "x180"),
        default="none",
        help=(
            "Rotation applied to inline mesh vertices/normals. "
            "'none' (default) suits a native GIANTS export (Forward -Z, Up Y) "
            "whose vertices are already in the Y-up bone frame. Use 'x180' only "
            "if your export axis setting leaves the mesh flipped relative to the "
            "skeleton."
        ),
    )
    ap.add_argument(
        "--no-restore-transforms",
        action="store_true",
        help="Do not copy the original node translation/rotation/scale onto the export.",
    )
    args = ap.parse_args()

    remap(
        args.original,
        args.exported,
        args.output,
        restore_transforms=not args.no_restore_transforms,
        rotate_vertices=(args.vertex_rotate == "x180"),
    )


if __name__ == "__main__":
    main()
