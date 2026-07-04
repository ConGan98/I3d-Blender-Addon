"""
graft_skeleton.py -- put a KNOWN-GOOD skeleton into a Blender export.

Why this exists
---------------
For a MESH-ONLY edit (re-skin / re-texture / reshape the visible mesh), the
skeleton never needs to change. But round-tripping it through Blender corrupts
the bone REST ORIENTATIONS (Blender bones can't hold an arbitrary joint frame,
so the exporter reinvents each rotation), and it also renumbers every nodeId.

FS animations live in a SEPARATE skeleton-only file (e.g.
cattleCalfAnimations.i3d + .anim). Two independent things must line up for the
animation to play:

  1. nodeIds -- the .i3d.anim addresses bones by nodeId (e.g. calf_root=4,
     spine=5 ... in cattleCalfAnimations.i3d). If the model's bones don't carry
     those ids, GIANTS Editor drives the WRONG bones (anim id 20 = eyeball_R,
     not calf_root) -> mesh flails "all over the place".
  2. rest pose -- the .anim keyframes are authored against the animation file's
     bone rest orientations. A Blender-mangled rest makes the skin bind
     (computed from the file's rest pose) disagree with the anim -> distortion.

Both are solved by grafting the skeleton straight from the ANIMATION i3d
(correct rest pose AND the ids the anim references), keeping the NEW mesh from
the export, and re-pointing the mesh's skinBindNodeIds to the grafted bones by
bone NAME. Optionally stamps an <Animation externalAnimFile=...> reference so
GIANTS Editor loads the clip.

The mesh lines up because the importer preserved joint POSITIONS (only
orientations were lost), and at rest the skinning delta is identity -- so
grafting the correct skeleton fixes the bind without touching a vertex.

Usage:
    # For GIANTS Editor animation preview -- source the skeleton FROM the anim
    # file so ids + rest match the .anim, and add the anim reference:
    python graft_skeleton.py cattleCalfAnimations.i3d export.i3d output.i3d \
        --preserve-skel-ids --anim-ref cattleCalfAnimations.i3d.anim

    # Plain graft (in-game; ids don't matter, binding is structural):
    python graft_skeleton.py cattleCalfHolstein.i3d export.i3d output.i3d
"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from pathlib import Path

_NODE_KINDS = {"TransformGroup", "Shape", "Light", "Camera"}

_BLENDER_DUP_SUFFIX = re.compile(r"\.\d{3}$")
_ORDER_PREFIX = re.compile(r"^\d+_")


def _strip_dup_suffix(name: str) -> str:
    return _BLENDER_DUP_SUFFIX.sub("", name) if name else name


def _strip_order_prefix(name: str) -> str:
    return _ORDER_PREFIX.sub("", name) if name else name


def _canon(name: str) -> str:
    """Canonical bone key: drop Blender dup-suffix AND order-prefix."""
    return _strip_order_prefix(_strip_dup_suffix(name or ""))


import math


def _vec3(s, default=(0.0, 0.0, 0.0)):
    if not s:
        return list(default)
    parts = s.replace(",", " ").split()
    return [float(p) for p in parts] if len(parts) == 3 else list(default)


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)]


def _mat_T(t):
    return [[1, 0, 0, t[0]], [0, 1, 0, t[1]], [0, 0, 1, t[2]], [0, 0, 0, 1]]


def _mat_S(s):
    return [[s[0], 0, 0, 0], [0, s[1], 0, 0], [0, 0, s[2], 0], [0, 0, 0, 1]]


def _mat_R(r_deg):
    """GIANTS intrinsic ZY'X'' Euler (degrees) -> 4x4, i.e. Rz @ Ry @ Rx."""
    x, y, z = (math.radians(v) for v in r_deg)
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    Rx = [[1, 0, 0, 0], [0, cx, -sx, 0], [0, sx, cx, 0], [0, 0, 0, 1]]
    Ry = [[cy, 0, sy, 0], [0, 1, 0, 0], [-sy, 0, cy, 0], [0, 0, 0, 1]]
    Rz = [[cz, -sz, 0, 0], [sz, cz, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    return _matmul(Rz, _matmul(Ry, Rx))


def _local_of(el):
    return _matmul(_mat_T(_vec3(el.attrib.get("translation"))),
                   _matmul(_mat_R(_vec3(el.attrib.get("rotation"))),
                           _mat_S(_vec3(el.attrib.get("scale"), (1.0, 1.0, 1.0)))))


def _world_positions(skel_el):
    """{canonical bone name -> (wx, wy, wz)} by forward kinematics over the
    skeleton subtree. The subtree root starts at identity (both the export and
    stock skeletons sit at the same top-level scene slot)."""
    out: dict[str, tuple] = {}
    ident = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]

    def walk(el, M):
        nm = _canon(el.attrib.get("name", ""))
        if nm and nm not in out:
            out[nm] = (M[0][3], M[1][3], M[2][3])
        for c in el:
            if c.tag in _NODE_KINDS:
                walk(c, _matmul(M, _local_of(c)))

    walk(skel_el, _matmul(ident, _local_of(skel_el)))
    return out


def _uniform_scale(exp_world: dict[str, tuple], stock_world: dict[str, tuple]) -> float:
    """Uniform scale between the export and the stock skeleton, from the ratio
    of each bone's DISTANCE to the skeleton origin (rotation-invariant, so it's
    robust to Blender's mangled export orientations). Median over all bones.
    Returns 1.0 if it can't be determined."""
    ratios = []
    for nm, s in stock_world.items():
        e = exp_world.get(nm)
        if e is None:
            continue
        sd = (s[0] ** 2 + s[1] ** 2 + s[2] ** 2) ** 0.5
        ed = (e[0] ** 2 + e[1] ** 2 + e[2] ** 2) ** 0.5
        if sd > 0.05:                 # skip the root/near-origin bones
            ratios.append(ed / sd)
    if not ratios:
        return 1.0
    ratios.sort()
    return ratios[len(ratios) // 2]


def _node_id(el) -> int | None:
    v = el.attrib.get("nodeId")
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _iter_nodes(el):
    if el.tag in _NODE_KINDS:
        yield el
    for child in el:
        yield from _iter_nodes(child)


def _all_ids(scene) -> set[int]:
    return {i for i in (_node_id(e) for e in scene.iter()) if i is not None}


def _skin_target_ids(scene) -> set[int]:
    out: set[int] = set()
    for el in scene.iter():
        attr = el.attrib.get("skinBindNodeIds")
        if not attr:
            continue
        for tok in attr.split():
            try:
                out.add(int(tok))
            except ValueError:
                pass
    return out


def _find_skeleton_by_targets(scene, targets: set[int]):
    """Top-level Scene child whose subtree contains the most skin targets."""
    best, best_hits = None, 0
    for child in scene:
        if child.tag not in _NODE_KINDS:
            continue
        ids = {_node_id(e) for e in _iter_nodes(child)}
        hits = len(ids & targets)
        if hits > best_hits:
            best, best_hits = child, hits
    return best


def _find_skeleton_by_name(scene, canon_name: str):
    """Top-level Scene child whose stripped name matches (for skeleton-only
    source files that carry no skinBindNodeIds of their own)."""
    for child in scene:
        if child.tag in _NODE_KINDS and _canon(child.attrib.get("name", "")) == canon_name:
            return child
    return None


def graft(
    skel_source_path: Path,
    export_path: Path,
    output_path: Path,
    *,
    preserve_skel_ids: bool = False,
    id_source_path: Path | None = None,
    anim_ref: str | None = None,
    scale_from_export: bool = False,
) -> None:
    src_tree = ET.parse(skel_source_path)
    src_scene = src_tree.getroot().find("Scene")
    exp_tree = ET.parse(export_path)
    exp_root = exp_tree.getroot()
    exp_scene = exp_root.find("Scene")
    if src_scene is None:
        raise SystemExit(f"{skel_source_path} has no <Scene>")
    if exp_scene is None:
        raise SystemExit(f"{export_path} has no <Scene>")

    # Optional SEPARATE id source: take the skeleton's REST from skel_source (the
    # model — the pose the mesh was skinned to) but its nodeIDs from here (the
    # animation i3d, whose ids the .anim references). Needed when the animation
    # file's skeleton is in a DIFFERENT rest pose than the model (e.g. Highland),
    # where grafting the anim skeleton would deform the mesh. Matched by name.
    id_source_map: dict[str, int] | None = None
    if id_source_path is not None:
        id_src_scene = ET.parse(id_source_path).getroot().find("Scene")
        if id_src_scene is None:
            raise SystemExit(f"{id_source_path} has no <Scene>")
        id_source_map = {}
        for el in id_src_scene.iter():
            if el.tag not in _NODE_KINDS:
                continue
            nm, i = _canon(el.attrib.get("name", "")), _node_id(el)
            if nm and i is not None and nm not in id_source_map:
                id_source_map[nm] = i

    # --- Locate the export skeleton (it has the skinBindNodeIds) ------------
    exp_targets = _skin_target_ids(exp_scene)
    if not exp_targets:
        raise SystemExit("export has no skinBindNodeIds -- nothing to graft against")
    exp_skel = _find_skeleton_by_targets(exp_scene, exp_targets)
    if exp_skel is None:
        raise SystemExit("could not locate the skeleton subtree in the export")
    exp_skel_name = _canon(exp_skel.attrib.get("name", ""))

    # --- Locate the source skeleton (anim file is skeleton-only) ------------
    src_skel = _find_skeleton_by_name(src_scene, exp_skel_name)
    if src_skel is None:
        raise SystemExit(
            f"could not find a top-level '{exp_skel_name}' skeleton in {skel_source_path.name}"
        )

    # export bone id -> name (from the export skeleton subtree)
    exp_id_to_name: dict[int, str] = {}
    for el in _iter_nodes(exp_skel):
        i, nm = _node_id(el), el.attrib.get("name")
        if i is not None and nm:
            exp_id_to_name[i] = nm

    grafted = copy.deepcopy(src_skel)

    # Optionally match the export's SIZE by scaling the grafted skeleton root.
    # A scaled model (e.g. a bigger bull) has a mesh larger than the stock
    # skeleton; the .i3d.anim stores stock-sized absolute bone transforms, so
    # playing it on re-seated bones snaps the model back to stock size. Instead
    # put the scale on the skeleton ROOT: the anim's per-bone locals then get
    # scaled ABOVE the animation, so the model stays big while it plays.
    scale_factor = None
    if scale_from_export:
        scale_factor = _uniform_scale(_world_positions(exp_skel),
                                      _world_positions(src_skel))

    # ids in the export that survive (everything except the skeleton we drop)
    exp_skel_ids = {_node_id(e) for e in _iter_nodes(exp_skel)}
    keep_ids = {i for i in _all_ids(exp_scene) if i not in exp_skel_ids}

    name_to_new_id: dict[str, int] = {}
    if id_source_map is not None:
        # Assign each grafted bone the id from the SEPARATE id-source, matched by
        # canonical name; fall back to a fresh id for names the id-source lacks.
        used = set(keep_ids)
        fresh = (max(keep_ids | set(id_source_map.values()))
                 if (keep_ids or id_source_map) else 0) + 1
        for el in _iter_nodes(grafted):
            if el.tag not in _NODE_KINDS:
                continue
            nm = _canon(el.attrib.get("name", ""))
            new = id_source_map.get(nm)
            if new is None or new in used:
                while fresh in used:
                    fresh += 1
                new = fresh
                fresh += 1
            el.set("nodeId", str(new))
            used.add(new)
            if nm:
                name_to_new_id[nm] = new
        # Renumber any surviving export node whose id collides with an assigned one.
        graft_ids = {_node_id(e) for e in _iter_nodes(grafted) if _node_id(e) is not None}
        collide = keep_ids & graft_ids
        if collide:
            nxt = max(used) + 1
            for el in exp_scene.iter():
                if _node_id(el) in collide:
                    el.set("nodeId", str(nxt))
                    nxt += 1
    elif preserve_skel_ids:
        # Keep the source skeleton's own nodeIds (so the .anim's bone ids match).
        for el in _iter_nodes(grafted):
            i, nm = _node_id(el), _canon(el.attrib.get("name", ""))
            if i is not None and nm:
                name_to_new_id[nm] = i
        graft_ids = {_node_id(e) for e in _iter_nodes(grafted) if _node_id(e) is not None}
        # Renumber any SURVIVING export node whose id collides with a grafted id.
        collide = keep_ids & graft_ids
        if collide:
            nxt = max(keep_ids | graft_ids) + 1
            for el in exp_scene.iter():
                i = _node_id(el)
                if i in collide:
                    el.set("nodeId", str(nxt))
                    nxt += 1
    else:
        # Assign fresh, collision-free ids to the grafted skeleton.
        nxt = (max(keep_ids) if keep_ids else 0) + 1
        used = set(keep_ids)
        for el in _iter_nodes(grafted):
            if el.tag not in _NODE_KINDS:
                continue
            while nxt in used:
                nxt += 1
            el.set("nodeId", str(nxt))
            used.add(nxt)
            nm = _canon(el.attrib.get("name", ""))
            if nm:
                name_to_new_id[nm] = nxt
            nxt += 1

    # --- Re-point the mesh's skinBindNodeIds: exp id -> name -> grafted id ---
    unmapped: list[str] = []
    lists_rewritten = 0
    for el in exp_scene.iter():
        attr = el.attrib.get("skinBindNodeIds")
        if not attr:
            continue
        new_tokens: list[str] = []
        for tok in attr.split():
            try:
                old = int(tok)
            except ValueError:
                new_tokens.append(tok)
                continue
            nm = _canon(exp_id_to_name.get(old, ""))
            new = name_to_new_id.get(nm)
            if new is None:
                unmapped.append(f"{el.attrib.get('name')}: bind id {old} ({nm or '?'})")
                new_tokens.append(tok)
            else:
                new_tokens.append(str(new))
        joined = " ".join(new_tokens)
        if joined != attr:
            el.set("skinBindNodeIds", joined)
            lists_rewritten += 1

    # --- Scale the grafted skeleton root to match the export's size ---------
    if scale_factor is not None and abs(scale_factor - 1.0) > 1e-4:
        cur = _vec3(grafted.attrib.get("scale"), (1.0, 1.0, 1.0))
        grafted.set("scale", f"{cur[0]*scale_factor:g} "
                             f"{cur[1]*scale_factor:g} {cur[2]*scale_factor:g}")

    # --- Swap the skeleton subtree in place (preserve top-level order) ------
    children = list(exp_scene)
    idx = children.index(exp_skel)
    exp_scene.remove(exp_skel)
    exp_scene.insert(idx, grafted)

    # --- Optionally add the animation reference so GIANTS Editor plays it ---
    if anim_ref:
        for existing in exp_root.findall("Animation"):
            exp_root.remove(existing)
        anim_el = ET.SubElement(exp_root, "Animation")
        anim_el.set("externalAnimFile", anim_ref)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    exp_tree.write(output_path, encoding="iso-8859-1", xml_declaration=True)

    n_nodes = sum(1 for _ in _iter_nodes(grafted))
    print(f"Wrote {output_path}")
    print(f"  grafted skeleton '{src_skel.attrib.get('name')}' ({n_nodes} nodes) "
          f"from {skel_source_path.name} at top-level index {idx}")
    if id_source_map is not None:
        id_mode = f"from id-source {id_source_path.name} by name (anim-compatible)"
    elif preserve_skel_ids:
        id_mode = "PRESERVED from source (anim-compatible)"
    else:
        id_mode = "freshly assigned"
    print(f"  skeleton nodeIds: {id_mode}")
    print(f"  re-pointed {lists_rewritten} skinBindNodeIds list(s) to the grafted bones")
    if scale_factor is not None:
        print(f"  scaled the grafted skeleton root by {scale_factor:.4f} to match "
              f"the export (stock-sized anim now plays at model scale)")
    if anim_ref:
        print(f"  added <Animation externalAnimFile=\"{anim_ref}\" /> for GIANTS Editor")
    if unmapped:
        print(f"  WARNING: {len(unmapped)} skin bind ref(s) had no name match in the "
              f"source skeleton (left unchanged):")
        for u in unmapped[:20]:
            print(f"    {u}")
        if len(unmapped) > 20:
            print(f"    ... +{len(unmapped) - 20} more")
    else:
        print("  all skin bind references matched the grafted skeleton by name.")


def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="Graft a known-good skeleton (e.g. from the animation i3d) "
                    "into a Blender export so the .i3d.anim plays.",
    )
    ap.add_argument("skeleton_source", type=Path,
                    help="i3d to take the skeleton FROM (the animation i3d, or the stock model)")
    ap.add_argument("export", type=Path, help="the Blender-exported .i3d (new mesh)")
    ap.add_argument("output", type=Path, help="destination for the grafted .i3d")
    ap.add_argument("--preserve-skel-ids", action="store_true",
                    help="keep the source skeleton's nodeIds so they match the .anim "
                         "(required for GIANTS Editor animation preview)")
    ap.add_argument("--id-source", type=Path, default=None,
                    help="take the skeleton REST from skeleton_source (the model) but "
                         "its nodeIds from THIS i3d (the animation i3d), matched by "
                         "bone name. Use when the animation file's skeleton is in a "
                         "different rest pose than the model (e.g. Highland)")
    ap.add_argument("--anim-ref", metavar="ANIMFILE", default=None,
                    help="add <Animation externalAnimFile=ANIMFILE/> so GE loads the clip")
    ap.add_argument("--scale-from-export", action="store_true",
                    help="scale the grafted skeleton ROOT to match the export's size "
                         "(uniform scale from bone distances). Use when you scaled the "
                         "whole rig (e.g. a bigger bull): the stock-sized .i3d.anim "
                         "then plays at the model's scale instead of shrinking it back "
                         "to stock size. No-op for an unscaled model.")
    args = ap.parse_args()
    graft(
        args.skeleton_source, args.export, args.output,
        preserve_skel_ids=args.preserve_skel_ids,
        id_source_path=args.id_source,
        anim_ref=args.anim_ref,
        scale_from_export=args.scale_from_export,
    )


if __name__ == "__main__":
    main()
