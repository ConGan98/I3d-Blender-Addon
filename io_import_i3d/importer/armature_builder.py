"""
Build a Blender Armature from a TransformGroup subtree, replacing the M1
empties for that subtree with EditBones inside a single Armature object.

Algorithm:
  1. Collect every nodeId referenced by ANY <Shape>'s skinBindNodeIds list.
  2. Find the LCA (lowest common ancestor) of those nodes in the scene tree.
     That LCA TransformGroup becomes the **armature object**; all of its
     TransformGroup descendants become **bones**.
  3. In edit mode: create an EditBone per bone, with head/tail/roll computed
     from the cumulative TransformGroup matrices in armature-local space.
  4. Delete the bone empties; reparent any non-bone children to the armature
     object so the rest of the scene hierarchy survives.

Tail derivation priority:
  - One child bone   -> tail = child.head
  - Multiple children-> tail = avg direction × max child distance
  - Leaf bone        -> tail = head + parent's bone-axis × fallback_length
                        (final fallback: head + (0, fallback_length, 0))
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import bpy

from . import xml_parser as xp


def _local_matrix(node: xp.SceneNode):
    import mathutils
    loc = mathutils.Matrix.Translation(node.translation)
    # GIANTS uses intrinsic ZY'X'' Euler (rotate Z first, then Y', then X''),
    # which is equivalent to matrix M = Rz(z) @ Ry(y) @ Rx(x). Blender's Euler
    # order naming reads RIGHT-TO-LEFT in matrix multiplication, so 'ZYX' would
    # produce Rx @ Ry @ Rz (wrong); 'XYZ' produces Rz @ Ry @ Rx (correct for
    # GIANTS). This only matters for joints with multiple non-zero rotation
    # components (cow/goat spine_01 is (0, -90, 0) so single-axis is unaffected,
    # but pig spine_01 is (-90, -88.5, 90) where the difference is huge).
    rot = mathutils.Euler(
        (
            math.radians(node.rotation[0]),
            math.radians(node.rotation[1]),
            math.radians(node.rotation[2]),
        ),
        'XYZ',
    ).to_matrix().to_4x4()
    sx, sy, sz = node.scale
    scale = mathutils.Matrix.Diagonal((sx, sy, sz, 1.0))
    return loc @ rot @ scale


def _find_armature_root(doc: xp.I3DDocument) -> tuple[int | None, set[int]]:
    """Return (armature_root_node_id, set_of_bone_node_ids).

    armature_root is the LCA of all skinBindNodeIds; it becomes the Armature
    OBJECT. Its TransformGroup descendants become bones.
    """
    referenced: set[int] = set()
    for n in doc.all_nodes():
        if n.kind == "Shape":
            referenced.update(n.skin_bind_node_ids)
    if not referenced:
        return None, set()

    parent_of: dict[int, int | None] = {}

    def walk_parent(node: xp.SceneNode, parent_id: int | None):
        parent_of[node.node_id] = parent_id
        for c in node.children:
            walk_parent(c, node.node_id)

    for r in doc.scene_roots:
        walk_parent(r, None)

    by_id = doc.by_node_id()

    def ancestors_inclusive(nid: int) -> list[int]:
        out: list[int] = []
        cur: int | None = nid
        while cur is not None:
            out.append(cur)
            cur = parent_of.get(cur)
        return out

    common: set[int] | None = None
    for nid in referenced:
        anc = set(ancestors_inclusive(nid))
        common = anc if common is None else common & anc
    if not common:
        return None, set()

    any_ref = next(iter(referenced))
    chain = ancestors_inclusive(any_ref)  # bottom-up
    lca: int | None = None
    for nid in chain:
        if nid in common:
            lca = nid
            break
    if lca is None:
        return None, set()

    # If the LCA itself is referenced as a deformer (i.e., it's both the
    # would-be armature object AND a bone), promote its parent to be the
    # armature root so the LCA can exist as a real bone inside.
    arm_root = lca
    if lca in referenced:
        parent = parent_of.get(lca)
        if parent is not None:
            arm_root = parent
        # If LCA has no parent (top-level), we keep it as both object root
        # AND a bone — handled below by including arm_root in `bones`.

    bones: set[int] = set()
    arm_node = by_id[arm_root]

    def collect(node: xp.SceneNode):
        if node.kind == "TransformGroup":
            bones.add(node.node_id)
        for c in node.children:
            collect(c)

    for c in arm_node.children:
        collect(c)

    # Edge case: LCA was top-level and referenced — keep it as a bone too.
    if arm_root == lca and lca in referenced:
        bones.add(lca)

    return arm_root, bones


def _compute_armature_local_matrices(
    doc: xp.I3DDocument,
    armature_root_id: int,
    bones: set[int],
):
    """nodeId -> 4x4 matrix in armature-local space (i.e., LCA = identity)."""
    import mathutils

    by_id = doc.by_node_id()
    out: dict[int, mathutils.Matrix] = {}

    def walk(node: xp.SceneNode, my_matrix):
        if node.node_id != armature_root_id and node.node_id in bones:
            out[node.node_id] = my_matrix
        for c in node.children:
            cm = my_matrix @ _local_matrix(c)
            walk(c, cm)

    walk(by_id[armature_root_id], mathutils.Matrix.Identity(4))
    return out


def build_armature(
    doc: xp.I3DDocument,
    nodes_by_id: dict,
    *,
    bone_display_size: float = 0.05,
    exact_bone_orientation: bool = False,
):
    """Replace the empties for the LCA + its TG descendants with one Armature.

    Returns (armature_object, node_id_to_bone_name_map) or (None, {}) if the
    document has no skinned shapes.

    `exact_bone_orientation` builds each bone with the joint's exact rest
    orientation (bones point along the joint +Y, which looks odd) instead of the
    default geometric point-at-child bones. Needed for `.i3d.anim` playback,
    where keyframes are local transforms in the original joint frame.
    """
    import bpy
    import mathutils

    arm_root_id, bone_ids = _find_armature_root(doc)
    if arm_root_id is None or not bone_ids:
        return None, {}

    by_id = doc.by_node_id()
    arm_root_node = by_id[arm_root_id]
    arm_root_empty = nodes_by_id[arm_root_id]

    matrices = _compute_armature_local_matrices(doc, arm_root_id, bone_ids)

    # DFS-ordered list of bone node IDs. Critical for round-trip: any
    # downstream exporter (e.g. StjerneIdioten's i3d exporter) walks the
    # armature's bones in their internal order and assigns sequential
    # nodeIds. To preserve the ORIGINAL i3d nodeIds, the bone creation
    # order MUST match the source XML's DFS traversal order.
    bone_ids_ordered: list[int] = []
    _seen_for_dfs: set[int] = set()

    def _collect_dfs(node):
        if node.node_id in bone_ids and node.node_id not in _seen_for_dfs:
            bone_ids_ordered.append(node.node_id)
            _seen_for_dfs.add(node.node_id)
        for c in node.children:
            _collect_dfs(c)

    _collect_dfs(arm_root_node)
    # Append any leftover bones (shouldn't happen in normal cases) to be safe.
    for nid in bone_ids:
        if nid not in _seen_for_dfs:
            bone_ids_ordered.append(nid)

    # Names: use TransformGroup names; ensure uniqueness for Blender.
    name_of: dict[int, str] = {}
    used_names: set[str] = set()
    for nid in bone_ids_ordered:
        n = by_id[nid]
        nm = n.name or f"bone_{nid}"
        base = nm
        i = 1
        while nm in used_names:
            i += 1
            nm = f"{base}.{i:03d}"
        used_names.add(nm)
        name_of[nid] = nm

    # Parent of each bone (within the armature).
    parent_of_node: dict[int, int | None] = {}

    def _walk_parents(node, parent_id):
        parent_of_node[node.node_id] = parent_id
        for c in node.children:
            _walk_parents(c, node.node_id)

    _walk_parents(arm_root_node, None)

    parent_bone_of: dict[int, int | None] = {}
    for nid in bone_ids_ordered:
        p = parent_of_node.get(nid)
        # Walk up parents until we hit a bone or the armature root (None).
        while p is not None and p not in bone_ids and p != arm_root_id:
            p = parent_of_node.get(p)
        if p == arm_root_id:
            parent_bone_of[nid] = None
        else:
            parent_bone_of[nid] = p

    # Children-of-bone (only direct children within the armature)
    children_of_bone: dict[int, list[int]] = {nid: [] for nid in bone_ids}
    for nid, p in parent_bone_of.items():
        if p is not None and p in children_of_bone:
            children_of_bone[p].append(nid)

    # ---- Create the Armature -----------------------------------------------
    arm_data = bpy.data.armatures.new(arm_root_node.name)
    arm_obj = bpy.data.objects.new(arm_root_node.name, arm_data)

    # Capture the world transform the bones should inherit (this is whatever
    # axis conversion + parent-chain transforms the source empty had).
    bpy.context.view_layer.update()
    src_world = arm_root_empty.matrix_world.copy()
    src_parent = arm_root_empty.parent
    arm_obj.rotation_mode = arm_root_empty.rotation_mode

    # Approach: bake src_world INTO each bone's head/tail (in matrices below)
    # and leave arm_obj itself at identity world. This sidesteps Blender's
    # parent-inverse / depsgraph-staleness issues entirely — the armature
    # object can have any local TRS; what we draw is determined by the
    # bone positions themselves, which we put in absolute world coords.
    arm_obj.parent = None
    arm_obj.matrix_world = mathutils.Matrix.Identity(4)
    bpy.context.view_layer.update()

    # Pre-multiply every bone matrix by src_world so head/tail extraction below
    # produces world-space coordinates directly.
    matrices = {nid: src_world @ m for nid, m in matrices.items()}

    # Same collection
    for col in list(arm_root_empty.users_collection):
        col.objects.link(arm_obj)

    # Re-parent any non-bone children of the empty to the armature object
    # (preserving their world transform).
    for child in list(arm_root_empty.children):
        if child not in (nodes_by_id.get(b) for b in bone_ids):
            wm = child.matrix_world.copy()
            child.parent = arm_obj
            child.matrix_world = wm

    # Make sure the armature is the active selection so EDIT mode works
    bpy.context.view_layer.objects.active = arm_obj

    # ---- Edit mode: create EditBones --------------------------------------
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = arm_data.edit_bones
    bone_name_to_node_id: dict[str, int] = {}

    try:
        for nid in bone_ids_ordered:
            nm = name_of[nid]
            eb = edit_bones.new(nm)
            if nid not in matrices:
                # Defensive: should not happen now that we promote the LCA
                # if it's referenced. Place a tiny placeholder bone so the
                # armature stays self-consistent.
                eb.head = mathutils.Vector((0.0, 0.0, 0.0))
                eb.tail = mathutils.Vector((0.0, bone_display_size, 0.0))
                bone_name_to_node_id[nm] = nid
                continue
            m = matrices[nid]
            head = m.to_translation()

            if exact_bone_orientation:
                # Rest frame = the joint's EXACT orientation, so .i3d.anim
                # keyframes (which are local transforms in that frame) apply
                # correctly. Bones point along each joint's +Y (they look odd in
                # the viewport) — fine for an animation-preview rig; NOT used for
                # the mesh-edit round-trip, where geometric bones are kept.
                length = bone_display_size
                _cd = [
                    (matrices[k].to_translation() - head).length
                    for k in children_of_bone.get(nid, []) if k in matrices
                ]
                _cd = [d for d in _cd if d > 1e-6]
                if _cd:
                    length = max(_cd)
                eb.head = mathutils.Vector((0.0, 0.0, 0.0))
                eb.tail = mathutils.Vector((0.0, length, 0.0))
                orient = m.to_quaternion().to_matrix().to_4x4()
                orient.translation = head
                eb.matrix = orient
                bone_name_to_node_id[nm] = nid
                continue

            dir_x = (m.to_3x3() @ mathutils.Vector((1.0, 0.0, 0.0))).normalized()

            kids = children_of_bone.get(nid, [])
            if len(kids) == 1 and kids[0] in matrices:
                tail = matrices[kids[0]].to_translation()
                if (tail - head).length < 1e-7:
                    tail = head + dir_x * bone_display_size
            elif len(kids) > 1:
                best = None
                best_len = 0.0
                for k in kids:
                    if k not in matrices:
                        continue
                    c_pos = matrices[k].to_translation()
                    d = c_pos - head
                    proj = d.dot(dir_x)
                    if proj > best_len:
                        best_len = proj
                        best = k
                if best is not None and best_len > 1e-6:
                    tail = matrices[best].to_translation()
                else:
                    avg = mathutils.Vector((0.0, 0.0, 0.0))
                    count = 0
                    for k in kids:
                        if k not in matrices:
                            continue
                        d = matrices[k].to_translation() - head
                        if d.length > 1e-6:
                            avg += d.normalized()
                            count += 1
                    if count > 0:
                        tail = head + (avg / count).normalized() * bone_display_size
                    else:
                        tail = head + dir_x * bone_display_size
            else:
                tail = head + dir_x * bone_display_size

            if (tail - head).length < 1e-7:
                tail = head + mathutils.Vector((0.0, bone_display_size, 0.0))

            eb.head = head
            eb.tail = tail
            eb.roll = 0.0  # TODO(M9): minimize twist relative to parent

            bone_name_to_node_id[nm] = nid

        # Set parent links
        for nid in bone_ids_ordered:
            if name_of[nid] not in edit_bones:
                continue
            eb = edit_bones[name_of[nid]]
            p = parent_bone_of.get(nid)
            if p is not None and p in name_of and name_of[p] in edit_bones:
                parent_eb = edit_bones[name_of[p]]
                eb.parent = parent_eb
                if (eb.head - parent_eb.tail).length < 1e-5:
                    eb.use_connect = True
    finally:
        # Always exit EDIT mode — leaving Blender stuck in EDIT after an
        # error makes the rest of the import unusable.
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

    # ---- Replace bone empties in nodes_by_id; remove originals ------------
    # We keep arm_obj as the entry for arm_root_id, and map each bone's nodeId
    # to the armature object (so the rest of the pipeline can still look up
    # objects). Skin binder will use the bone-name map separately.
    for nid in bone_ids_ordered:
        empty = nodes_by_id.get(nid)
        if empty is not None and empty != arm_obj:
            # Reparent any of the empty's children that are NOT bones
            for child in list(empty.children):
                # Children in bone_ids are already getting deleted next anyway
                if child.get("_i3d_node_id") in bone_ids:
                    continue
                wm = child.matrix_world.copy()
                child.parent = arm_obj
                child.matrix_world = wm
            try:
                bpy.data.objects.remove(empty, do_unlink=True)
            except ReferenceError:
                pass
        nodes_by_id[nid] = arm_obj

    # Replace LCA empty too (we already created arm_obj from its data).
    if arm_root_empty != arm_obj:
        try:
            bpy.data.objects.remove(arm_root_empty, do_unlink=True)
        except ReferenceError:
            pass
    nodes_by_id[arm_root_id] = arm_obj

    # Stash the bone-name map on the armature object as a custom prop
    arm_obj["_i3d_bone_name_to_node_id"] = bone_name_to_node_id
    # And the inverse: nodeId -> bone name (string keys for json-friendly storage)
    arm_obj["_i3d_node_id_to_bone_name"] = {str(k): v for k, v in name_of.items()}
    # Stash the world transform baked into the bones (axis conversion + parent
    # chain). anim_apply needs it to re-apply the conversion to the root track,
    # whose .anim keyframes are in the raw GIANTS frame — otherwise the root
    # cancels the conversion and the whole model plays tipped 90°. Row-major
    # 16-float flatten for json-friendly custom-prop storage.
    arm_obj["_i3d_src_world"] = [v for row in src_world for v in row]

    # Preserve each joint's ORIGINAL i3d transform on the bone itself. Blender's
    # head/tail/roll is a lossy proxy for a Maya-style joint matrix (it can't
    # represent +X-along-bone orientation exactly without reshaping the bone),
    # so any export that wants to round-trip rotations needs the source values.
    # Stored on the Bone (armature data) so they survive save/reload.
    for nid in bone_ids_ordered:
        bn = name_of.get(nid)
        if bn is None or bn not in arm_data.bones:
            continue
        src = by_id[nid]
        bone = arm_data.bones[bn]
        bone["_i3d_node_id"] = nid
        bone["_i3d_translation"] = list(src.translation)
        bone["_i3d_rotation_zyx_deg"] = list(src.rotation)
        bone["_i3d_scale"] = list(src.scale)

    # Re-parent the armature into the original hierarchy slot (e.g. under
    # pigSkeleton) so re-export keeps the same TG layout. Bone head/tail are
    # already in world coordinates so we need arm_obj.matrix_world to stay
    # identity after parenting; setting matrix_parent_inverse to the parent's
    # inverse-world achieves that.
    if src_parent is not None:
        bpy.context.view_layer.update()
        arm_obj.parent = src_parent
        arm_obj.matrix_parent_inverse = src_parent.matrix_world.inverted()

    return arm_obj, name_of
