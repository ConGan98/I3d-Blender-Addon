"""
Replace Shape empties (created in M1) with bpy.types.Mesh objects built from
parsed ShapeData. Vertex positions stay in shape-local Y-up; the import root's
+X 90° rotation propagates to put the mesh in Blender's Z-up world space.

UVs are flipped on V (GIANTS uses top-left origin, Blender uses bottom-left).

Triangle indices are 0-indexed (raw disk values) — no +1 shift.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import bpy

from . import shapes_entity as se


def replace_shape_empties_with_meshes(
    nodes_by_id,
    shapes,
):
    """For every Shape-empty in `nodes_by_id` whose `_i3d_shape_id` matches a
    key in `shapes`, swap it for a real mesh object preserving name, parent,
    transform, custom props, and child links. Mutates `nodes_by_id` in place.
    """
    import bpy  # noqa: F401  — runtime import (Blender only)

    for node_id in list(nodes_by_id.keys()):
        empty = nodes_by_id[node_id]
        if empty.get("_i3d_kind") != "Shape":
            continue
        shape_id = empty.get("_i3d_shape_id", -1)
        if shape_id < 0 or shape_id not in shapes:
            continue
        shape = shapes[shape_id]
        mesh_obj = _swap_empty_for_mesh(empty, shape)
        nodes_by_id[node_id] = mesh_obj


def _swap_empty_for_mesh(empty, shape: se.ShapeData):
    import bpy

    me = _build_mesh(shape, name=empty.name)
    obj = bpy.data.objects.new(empty.name, me)

    # Copy parent + transform components directly (NOT via matrix_local).
    # Setting matrix_local first then changing rotation_mode causes the
    # effective rotation to mutate because Blender keeps the raw euler
    # values across mode-change but reinterprets them in the new order.
    obj.parent = empty.parent
    obj.rotation_mode = empty.rotation_mode  # set BEFORE rotation_euler
    obj.location = empty.location.copy()
    obj.rotation_euler = empty.rotation_euler.copy()
    obj.scale = empty.scale.copy()

    # Copy i3d custom props
    for k in list(empty.keys()):
        if k.startswith("_i3d_"):
            obj[k] = empty[k]

    # Visibility
    if empty.hide_viewport:
        obj.hide_viewport = True
        obj.hide_render = True

    # Place in same collection(s)
    for col in list(empty.users_collection):
        col.objects.link(obj)

    # Re-parent the empty's children to the new mesh object so the hierarchy survives
    for child in list(empty.children):
        # Preserve world transform when re-parenting
        wm = child.matrix_world.copy()
        child.parent = obj
        child.matrix_world = wm

    bpy.data.objects.remove(empty, do_unlink=True)
    return obj


def _build_mesh(shape: se.ShapeData, name: str):
    import bpy

    me = bpy.data.meshes.new(name)

    # Defensive: drop triangles with out-of-range OR degenerate (repeated)
    # vertex indices. Both can crash Blender's vert_to_face_map cache build.
    # GIANTS exports always include a leading (0,0,0) placeholder triangle.
    vc = shape.vertex_count
    safe_tris = []
    dropped = 0
    for tri in shape.triangles:
        a, b, c = tri
        if not (0 <= a < vc and 0 <= b < vc and 0 <= c < vc):
            dropped += 1
            continue
        if a == b or b == c or a == c:
            dropped += 1
            continue
        safe_tris.append((a, b, c))

    me.from_pydata(list(shape.positions), [], safe_tris)

    # mesh.validate() before any cache-touching operation. verbose=False to
    # avoid spamming the console; clean_customdata=False to preserve attribs.
    me.validate(verbose=False, clean_customdata=False)
    me.update()

    if dropped:
        me["_i3d_dropped_tris"] = dropped

    # UV layer. GIANTS stores UVs already in Blender's bottom-left convention,
    # so we use them directly (no V flip).
    if shape.uv_sets[0] is not None:
        uvs = shape.uv_sets[0]
        uv_layer = me.uv_layers.new(name="UVMap")
        if uv_layer is not None:
            for poly in me.polygons:
                for li, vi in zip(poly.loop_indices, poly.vertices):
                    if 0 <= vi < len(uvs):
                        u, v = uvs[vi]
                        uv_layer.data[li].uv = (u, v)

    # Mark all polygons smooth-shaded; rely on Blender's auto-computed
    # vertex normals. Authored split normals are deferred — Blender 4.5's
    # `normals_split_custom_set_from_vertices` crashes during the
    # vert_to_face cache build on certain meshes; we'll re-enable this in
    # M9 with a known-safe path.
    for poly in me.polygons:
        poly.use_smooth = True

    me["_i3d_options"] = int(shape.options)
    me["_i3d_shape_id"] = int(shape.shape_id)
    if shape.vtx_compression is not None:
        me["_i3d_vtx_compression"] = float(shape.vtx_compression)

    return me
