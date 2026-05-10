"""
Build the Blender scene from a parsed I3DDocument.

M1 scope: every TransformGroup/Shape/Light/Camera node becomes an Empty.
Shape nodes are tagged with custom properties so M3 can find and replace them.
A top-level Empty holds the Y-up→Z-up axis conversion; all i3d roots parent to it.
"""
from __future__ import annotations

from typing import Iterable

import bpy
from mathutils import Matrix, Euler

from . import axis_convert
from . import xml_parser as xp


def build_empties(
    doc: xp.I3DDocument,
    *,
    apply_axis_conversion: bool = True,
    forward_axis: str = "+Y",
    wrap_in_container: bool = True,
    collection: bpy.types.Collection | None = None,
) -> tuple[bpy.types.Object | None, dict[int, bpy.types.Object]]:
    """Create an Empty per scene node. Returns (root_object_or_None, node_id -> object map).

    When `wrap_in_container` is False, no top-level Empty is created; the i3d's
    scene-root TransformGroups become top-level objects in Blender. The axis
    conversion is then applied to each top-level scene-root's matrix_local
    (so its world transform still carries the rotation). This is the
    round-trip-friendly mode — exporting back to i3d preserves the original
    nodeId numbering because no extra wrapping node shifts the count.
    """
    if collection is None:
        collection = bpy.context.collection

    nodes_by_id: dict[int, bpy.types.Object] = {}

    root: bpy.types.Object | None = None
    if wrap_in_container:
        root = bpy.data.objects.new(doc.name, None)
        root.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(root)
        if apply_axis_conversion:
            root.matrix_world = axis_convert.import_root_matrix(forward=forward_axis)

    def _walk(node: xp.SceneNode, parent: bpy.types.Object | None):
        obj = _make_empty_for_node(node)
        collection.objects.link(obj)
        if parent is not None:
            obj.parent = parent
        nodes_by_id[node.node_id] = obj
        for child in node.children:
            _walk(child, obj)

    for r in doc.scene_roots:
        _walk(r, root)

    # No wrapping root: pre-multiply the axis-conversion rotation into each
    # top-level scene-root's matrix so the visible orientation matches what
    # the wrapping mode would have produced. Explicit component assignment
    # avoids quirks with the matrix_world setter on objects with children.
    if root is None and apply_axis_conversion:
        import math
        from mathutils import Matrix, Euler
        R = axis_convert.import_root_matrix(forward=forward_axis)
        for r in doc.scene_roots:
            obj = nodes_by_id[r.node_id]
            # Recompose the original local matrix from the parsed XML so we
            # don't depend on whatever Blender currently has cached.
            loc_m = Matrix.Translation(r.translation)
            rot_m = Euler(
                (
                    math.radians(r.rotation[0]),
                    math.radians(r.rotation[1]),
                    math.radians(r.rotation[2]),
                ),
                'ZYX',
            ).to_matrix().to_4x4()
            sx, sy, sz = r.scale
            scale_m = Matrix.Diagonal((sx, sy, sz, 1.0))
            original_local = loc_m @ rot_m @ scale_m
            new_local = R @ original_local
            new_loc, new_rot_q, new_scale = new_local.decompose()
            obj.location = new_loc
            obj.rotation_mode = 'ZYX'
            obj.rotation_euler = new_rot_q.to_euler('ZYX')
            obj.scale = new_scale

    return root, nodes_by_id


def _make_empty_for_node(node: xp.SceneNode) -> bpy.types.Object:
    obj = bpy.data.objects.new(node.name, None)
    if node.kind == "Shape":
        obj.empty_display_type = 'CUBE'
        obj["_i3d_shape_id"] = node.shape_id if node.shape_id is not None else -1
        obj["_i3d_material_ids"] = list(node.material_ids)
        obj["_i3d_skin_bind_node_ids"] = list(node.skin_bind_node_ids)
        obj["_i3d_non_renderable"] = bool(node.non_renderable)
    elif node.kind == "Light":
        obj.empty_display_type = 'SPHERE'
    elif node.kind == "Camera":
        obj.empty_display_type = 'CONE'
    else:
        obj.empty_display_type = 'PLAIN_AXES'

    obj["_i3d_node_id"] = node.node_id
    obj["_i3d_kind"] = node.kind
    if not node.visibility:
        obj.hide_viewport = True
        obj.hide_render = True
    obj.empty_display_size = 0.1

    obj.rotation_mode = 'ZYX'
    obj.location = node.translation
    obj.rotation_euler = Euler(
        axis_convert.degrees_to_radians_zyx(node.rotation),
        'ZYX',
    )
    obj.scale = node.scale
    return obj


def collect_shape_placeholders(
    nodes_by_id: dict[int, bpy.types.Object]
) -> list[bpy.types.Object]:
    return [obj for obj in nodes_by_id.values() if obj.get("_i3d_kind") == "Shape"]
