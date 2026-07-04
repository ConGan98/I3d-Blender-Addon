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
from . import attribute_builder
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
    exact_bones = bool(options.get('exact_bone_orientation', False))
    arm_obj = None
    node_id_to_bone: dict = {}
    try:
        arm_obj, node_id_to_bone = armature_builder.build_armature(
            doc, nodes_by_id, bone_display_size=bone_size,
            exact_bone_orientation=exact_bones,
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
            apply_i3d_shader=options.get('import_attributes', True),
            log=log,
        )
        log.info("Created %d materials", n_mats)

    # i3d node attributes -> i3dio's i3d_attributes (Object + Data panels), so
    # physics/collision/render flags show up in the standard panels and
    # round-trip on re-export. No-op if i3dio isn't installed.
    if options.get('import_attributes', True):
        try:
            attr_stats = attribute_builder.apply_node_attributes(doc, nodes_by_id, log=log)
        except Exception as e:
            log.warning("i3d attribute import failed (%s); geometry/rig unaffected", e)

    # M7/M8: animation import — parse .i3d.anim and create Blender Actions.
    anim_stats = {"actions_built": 0, "fcurves_written": 0}
    if options.get('import_animations', True) and arm_obj is not None:
        anim_id_to_name: dict | None = None
        anim_path = None

        # Preferred: an explicit ANIMATION i3d (the animation lives in a separate
        # file, e.g. cattleCalfAnimations.i3d). It supplies both the bone
        # id->name table the .anim tracks reference (its own numbering, calf
        # 4..48) AND the .anim it points to.
        # Strip whitespace and surrounding quotes — Windows "Copy as path" wraps
        # the path in double quotes, which would make .exists() fail silently.
        anim_i3d_str = (options.get('animation_i3d_path', '') or '').strip().strip('"').strip("'")
        if not anim_i3d_str:
            # Auto-detect: a sibling animation i3d in the model's folder is any
            # *.i3d that has a matching *.i3d.anim next to it (the model itself
            # usually has no .anim sibling, so it won't self-match).
            for cand in sorted(path.parent.glob('*.i3d')):
                if cand != path and cand.with_name(cand.name + '.anim').exists():
                    anim_i3d_str = str(cand)
                    log.info("Auto-detected animation i3d: %s", cand.name)
                    break
        if anim_i3d_str:
            anim_i3d_path = Path(anim_i3d_str)
            # Tolerate pointing at the .i3d.anim binary instead of the .i3d XML:
            # derive the sibling .i3d (strip the trailing .anim).
            if anim_i3d_path.suffix.lower() == ".anim" and anim_i3d_path.name.lower().endswith(".i3d.anim"):
                derived = anim_i3d_path.with_name(anim_i3d_path.name[:-len(".anim")])
                if derived.exists():
                    log.info("Animation i3d: using %s (derived from the .anim you picked)", derived.name)
                    anim_i3d_path = derived
            if anim_i3d_path.exists():
                try:
                    anim_i3d_doc = xp.parse_i3d(anim_i3d_path)
                    anim_id_to_name = {
                        n.node_id: n.name
                        for n in anim_i3d_doc.all_nodes() if n.name
                    }
                    if anim_i3d_doc.animation and anim_i3d_doc.animation.external_file:
                        anim_path = anim_i3d_path.parent / anim_i3d_doc.animation.external_file
                    log.info("Animation source: %s (%d named nodes, anim=%s)",
                             anim_i3d_path.name, len(anim_id_to_name),
                             anim_path.name if anim_path else "<none>")
                except Exception as e:
                    log.warning("Failed to read animation i3d %s (%s)", anim_i3d_path, e)
            else:
                log.warning("Animation i3d not found: %s", anim_i3d_path)

        # Fallback: a .anim referenced by the imported model itself.
        if anim_path is None and doc.animation and doc.animation.external_file:
            anim_path = path.parent / doc.animation.external_file

        if anim_path is not None and anim_path.exists():
            try:
                anim_doc = anim_reader.parse_anim(anim_path)
                log.info(
                    "Anim: char=%r %d clips parsed",
                    anim_doc.character_name, len(anim_doc.clips),
                )
                anim_stats = anim_apply.apply_animation(
                    arm_obj, anim_doc,
                    node_id_to_bone=node_id_to_bone,
                    anim_id_to_name=anim_id_to_name,
                    log=log,
                )
            except Exception as e:
                log.warning("Anim import failed (%s); meshes & rig still imported", e)
        else:
            log.warning(
                "No .anim to import — set 'Animation i3d' to the animations file "
                "(e.g. cattleCalfAnimations.i3d) whose sibling .anim holds the clips"
            )

    # Order prefix: rename objects with 01_, 02_, … per sibling group so the
    # outliner keeps the original i3d order (Blender sorts alphabetically).
    if options.get('order_prefix', True):
        n_renamed = scene_builder.apply_order_prefixes(doc, nodes_by_id)
        log.info("Order prefix: renamed %d object(s) to preserve i3d order", n_renamed)

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
