"""
For every skinned shape (Shape node with non-empty skinBindNodeIds), create
vertex groups + an Armature modifier on the corresponding mesh object.

`skinBindNodeIds` on each Shape is a per-shape local-to-global bone-id table.
Per-vertex `BlendIndices` index INTO that table; resolved global node IDs
map to bone names via the armature's `_i3d_node_id_to_bone_name` prop.

Per-vertex weights:
  - Standard: 4 indices + 4 weights per vertex.
  - SingleBlendWeights flag: 1 index per vertex, weight implicit = 1.0.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import bpy

from . import xml_parser as xp
from . import shapes_entity as se


def bind_skins(
    doc: xp.I3DDocument,
    nodes_by_id: dict,
    shapes: dict[int, se.ShapeData],
    arm_obj,
    *,
    log=None,
) -> dict:
    """Bind all skinned meshes. Returns a stats dict so the caller can report
    visible feedback to the user via Operator.report()."""
    stats = {
        "meshes_bound": 0,
        "vertex_groups_total": 0,
        "weights_dropped": 0,
        "bones_unmapped": 0,
        "skipped_no_mesh": 0,
        "skipped_no_skinning_data": 0,
    }
    if arm_obj is None:
        if log:
            log.warning("skin_binder: no armature object — nothing to bind")
        return stats

    raw_map = arm_obj.get("_i3d_node_id_to_bone_name", {})
    if not raw_map:
        if log:
            log.warning(
                "skin_binder: armature has no _i3d_node_id_to_bone_name property — "
                "skipping skin binding"
            )
        return stats
    # Normalize Blender's IDPropertyGroup to a plain dict.
    try:
        node_id_to_bone: dict[int, str] = {int(k): str(v) for k, v in raw_map.items()}
    except Exception as e:
        if log:
            log.warning("skin_binder: bone name map unreadable (%s)", e)
        return stats

    if log:
        log.info("skin_binder: bone-name map has %d entries", len(node_id_to_bone))

    # Verify the bones are actually present on the armature (Blender may have
    # renamed them e.g. "cow.001" if a duplicate already existed).
    actual_bones = set(b.name for b in arm_obj.data.bones)
    bone_names_present = {gid: bn for gid, bn in node_id_to_bone.items() if bn in actual_bones}
    if len(bone_names_present) != len(node_id_to_bone) and log:
        log.warning(
            "skin_binder: %d/%d bones from the name map don't exist on the armature "
            "(possible name collision with a previous import)",
            len(node_id_to_bone) - len(bone_names_present), len(node_id_to_bone),
        )

    for n in doc.all_nodes():
        if n.kind != "Shape" or not n.skin_bind_node_ids:
            continue
        if n.shape_id is None or n.shape_id not in shapes:
            continue
        mesh_obj = nodes_by_id.get(n.node_id)
        if mesh_obj is None or mesh_obj.type != 'MESH':
            stats["skipped_no_mesh"] += 1
            if log:
                log.warning(
                    "skin_binder: %s has skinBindNodeIds but no mesh object found",
                    n.name,
                )
            continue
        shape = shapes[n.shape_id]
        if shape.blend_indices is None:
            stats["skipped_no_skinning_data"] += 1
            continue

        local_to_bone: dict[int, str] = {}
        for li, gid in enumerate(n.skin_bind_node_ids):
            bn = bone_names_present.get(gid)
            if bn is not None:
                local_to_bone[li] = bn
        unmapped = len(n.skin_bind_node_ids) - len(local_to_bone)
        stats["bones_unmapped"] += unmapped
        if unmapped and log:
            log.warning(
                "skin_binder: %s: %d/%d skinBindNodeIds unmapped",
                n.name, unmapped, len(n.skin_bind_node_ids),
            )

        # Create vertex groups for all referenced bones.
        for bn in set(local_to_bone.values()):
            if bn not in mesh_obj.vertex_groups:
                mesh_obj.vertex_groups.new(name=bn)

        # Apply weights.
        n_idx = 1 if shape.single_blend else 4
        for vi in range(shape.vertex_count):
            indices = shape.blend_indices[vi]
            weights = (1.0,) if shape.single_blend else shape.blend_weights[vi]
            for j in range(n_idx):
                w = float(weights[j])
                if w <= 0.0:
                    continue
                li = int(indices[j])
                bn = local_to_bone.get(li)
                if bn is None:
                    stats["weights_dropped"] += 1
                    continue
                mesh_obj.vertex_groups[bn].add([vi], w, 'ADD')

        # NOTE: we deliberately do NOT re-parent the mesh to the armature.
        # The Armature modifier alone is enough for deformation, and keeping
        # the original i3d parent relationship preserves the scene hierarchy.

        # Ensure the Armature modifier exists and points at the armature.
        mod = mesh_obj.modifiers.get("Armature")
        if mod is None:
            mod = mesh_obj.modifiers.new("Armature", 'ARMATURE')
        mod.object = arm_obj
        mod.use_vertex_groups = True
        mod.use_bone_envelopes = False

        stats["meshes_bound"] += 1
        stats["vertex_groups_total"] += len(mesh_obj.vertex_groups)
        if log:
            log.info(
                "skin_binder: bound %s -> %d vertex groups, modifier OK",
                mesh_obj.name, len(mesh_obj.vertex_groups),
            )

    return stats
