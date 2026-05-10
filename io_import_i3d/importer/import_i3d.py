"""
Top-level orchestrator. Imports run as: parse XML -> (M2/M3) decrypt+parse shapes
-> build empties -> (M4) replace bone subtrees with armature -> (M5) bind skin
-> (M6) materials -> (M7/M8) animation. M1 implements parse XML + build empties.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy

from . import xml_parser as xp
from . import scene_builder
from . import mesh_builder
from . import shapes_reader
from . import armature_builder
from . import skin_binder
from . import material_builder
from . import anim_reader
from . import anim_apply
from . import log as _log


class I3DImportError(RuntimeError):
    pass


def run(*, context: bpy.types.Context, filepath: str, options: dict[str, Any]) -> int:
    prefs = _get_prefs(context)
    log = _log.get_logger(getattr(prefs, "log_level", "INFO"))

    path = Path(filepath)
    if not path.exists():
        raise I3DImportError(f"File not found: {filepath}")

    log.info("Parsing %s", path)
    try:
        doc = xp.parse_i3d(path)
    except Exception as e:
        raise I3DImportError(f"XML parse failed: {e}") from e

    log.info(
        "Loaded i3d v%s '%s': %d files, %d materials, %d scene roots, anim=%s, shapes=%s",
        doc.version, doc.name,
        len(doc.files), len(doc.materials), len(doc.scene_roots),
        bool(doc.animation), bool(doc.external_shapes_file),
    )

    apply_axis = options.get('axis_convention', 'AUTO') == 'AUTO'
    forward_axis = options.get('forward_axis', '+Y')
    wrap_in_container = options.get('wrap_in_container', True)
    root, nodes_by_id = scene_builder.build_empties(
        doc,
        apply_axis_conversion=apply_axis,
        forward_axis=forward_axis,
        wrap_in_container=wrap_in_container,
    )
    log.info(
        "Built %d empties (root=%s, forward=%s, wrap=%s)",
        len(nodes_by_id),
        root.name if root else "<no wrap>",
        forward_axis,
        wrap_in_container,
    )

    # M3: decrypt + parse .i3d.shapes; replace Shape empties with mesh objects.
    shapes: dict = {}
    if doc.external_shapes_file:
        shapes_path = path.parent / doc.external_shapes_file
        if shapes_path.exists():
            try:
                shapes = shapes_reader.parse_external_shapes(shapes_path)
                log.info("Parsed %d shapes from %s", len(shapes), shapes_path.name)
                mesh_builder.replace_shape_empties_with_meshes(nodes_by_id, shapes)
                log.info("Built mesh objects for shapes")
            except Exception as e:
                log.warning("Failed to load shapes (%s); leaving Shape empties in place", e)
        else:
            log.warning("External shapes file not found: %s", shapes_path)

    # M4: armature builder — replace bone empties with one Armature object.
    bone_size = float(options.get('bone_display_size', 0.05))
    arm_obj = None
    node_id_to_bone: dict = {}
    try:
        arm_obj, node_id_to_bone = armature_builder.build_armature(
            doc, nodes_by_id, bone_display_size=bone_size,
        )
        if arm_obj is not None:
            log.info("Built armature '%s' with %d bones", arm_obj.name, len(node_id_to_bone))
        else:
            log.info("No skinned shapes — skipping armature build")
    except Exception as e:
        log.warning("Armature build failed (%s); continuing with empties for bones", e)

    # M5: skin binding — vertex groups + Armature modifier on each skinned mesh.
    skin_stats = {"meshes_bound": 0, "vertex_groups_total": 0}
    if arm_obj is not None and shapes:
        try:
            skin_stats = skin_binder.bind_skins(doc, nodes_by_id, shapes, arm_obj, log=log)
            log.info(
                "Skin: %d meshes bound, %d vertex groups total, %d weights dropped",
                skin_stats["meshes_bound"], skin_stats["vertex_groups_total"],
                skin_stats["weights_dropped"],
            )
        except Exception as e:
            log.warning("Skin binding failed (%s)", e)

    # M6: materials
    if options.get('import_materials', True) and shapes:
        n_mats = material_builder.build_materials(
            doc, nodes_by_id,
            data_path=getattr(prefs, 'data_path', '') or '',
            data_s_path=getattr(prefs, 'data_s_path', '') or '',
            log=log,
        )
        log.info("Created %d materials", n_mats)

    # M7/M8: animation import — parse .i3d.anim and create Blender Actions.
    anim_stats = {"actions_built": 0, "fcurves_written": 0}
    if (
        options.get('import_animations', True)
        and arm_obj is not None
        and doc.animation
        and doc.animation.external_file
    ):
        anim_path = path.parent / doc.animation.external_file
        if anim_path.exists():
            try:
                anim_doc = anim_reader.parse_anim(anim_path)
                log.info(
                    "Anim: char=%r %d clips parsed",
                    anim_doc.character_name, len(anim_doc.clips),
                )
                anim_stats = anim_apply.apply_animation(
                    arm_obj, anim_doc,
                    node_id_to_bone=node_id_to_bone,
                    log=log,
                )
            except Exception as e:
                log.warning("Anim import failed (%s); meshes & rig still imported", e)
        else:
            log.warning("External anim file not found: %s", anim_path)

    # Active selection: prefer the wrapping root, otherwise the first scene root.
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    selection_target = root
    if selection_target is None and doc.scene_roots:
        selection_target = nodes_by_id.get(doc.scene_roots[0].node_id)
    if selection_target is not None:
        selection_target.select_set(True)
        context.view_layer.objects.active = selection_target

    n_bones = len(node_id_to_bone)
    return {
        "n_objects": len(nodes_by_id) + (1 if root is not None else 0),
        "n_bones": n_bones,
        "n_meshes_bound": skin_stats["meshes_bound"],
        "n_vertex_groups": skin_stats["vertex_groups_total"],
        "n_weights_dropped": skin_stats.get("weights_dropped", 0),
        "n_actions": anim_stats.get("actions_built", 0),
    }


def _get_prefs(context: bpy.types.Context):
    addon = context.preferences.addons.get(__package__.split('.')[0])
    return addon.preferences if addon else None
