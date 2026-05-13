"""
Apply parsed AnimDocument to a Blender armature: create one Action per clip,
write fcurves on pose_bones for both location and rotation_euler.

Bone ordering: the .anim file references bones by INDEX (DFS order in the
i3d Scene). We use the armature's own DFS bone order, which our import
guarantees matches the original i3d. If counts mismatch, we skip extra
tracks defensively.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import bpy

from . import anim_reader as ar


def _bone_dfs_order(arm_data) -> list:
    """Return pose-bone names in DFS order from the armature."""
    names = []
    seen = set()

    def walk(bone):
        if bone.name in seen:
            return
        seen.add(bone.name)
        names.append(bone.name)
        for c in bone.children:
            walk(c)

    # Start from root bones (those without parents).
    roots = [b for b in arm_data.bones if b.parent is None]
    for r in roots:
        walk(r)
    return names


def apply_animation(
    arm_obj, doc: ar.AnimDocument, *,
    node_id_to_bone: dict[int, str] | None = None,
    fps: float = 30.0,
    apply_location: bool = False,
    log=None,
):
    """Build a Blender Action for every clip in `doc` and bind the first one
    to the armature. Returns a stats dict."""
    import bpy

    stats = {"actions_built": 0, "fcurves_written": 0, "tracks_unmatched": 0,
             "keyframes_total": 0, "max_frame": 0.0}
    if arm_obj is None or arm_obj.type != 'ARMATURE':
        if log:
            log.warning("anim_apply: arm_obj is not an Armature")
        return stats

    from mathutils import Matrix, Euler

    bone_names = _bone_dfs_order(arm_obj.data)
    if log:
        log.info("anim_apply: armature has %d bones in DFS order", len(bone_names))

    # Use quaternion pose channels. Some GIANTS bones animate near euler
    # gimbal-lock zones (e.g. spine eulers parked at Y=π/2); writing eulers
    # to fcurves makes Blender LINEARLY interpolate through wild intermediate
    # values. Quaternions avoid this entirely (Blender slerps them).
    for bone_name in bone_names:
        pb = arm_obj.pose.bones.get(bone_name)
        if pb is not None:
            pb.rotation_mode = 'QUATERNION'

    # GIANTS keyframes are absolute bone-local transforms in the .anim's own
    # parameterisation, NOT in the i3d XML's rest frame. KF[0] of each track
    # (the magic=0 "rest" KF) is the .anim's rest baseline — we use it to
    # compute pose-space deltas that Blender can drive against its own rest.

    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    anim_data = arm_obj.animation_data

    first_action = None
    max_frame = 0.0

    for clip in doc.clips:
        action = bpy.data.actions.new(name=f"i3dAnim_{clip.name}")
        # Mark the action as having a user so Blender doesn't garbage it
        # when the file is reloaded. Set id_root before adding fcurves.
        try:
            action.id_root = 'OBJECT'
        except (AttributeError, TypeError):
            pass
        action.use_fake_user = True

        already_written: set[str] = set()
        for track in clip.bone_tracks:
            # The .anim references bones by i3d node id (stored in each
            # track's header). The first track is layout=short_intro and
            # has bone_node_id=-1 — we infer it (cow_root in samples) by
            # falling back to the first DFS bone if the lookup fails.
            bone_name = None
            if node_id_to_bone is not None and track.bone_node_id != -1:
                bone_name = node_id_to_bone.get(track.bone_node_id)
            if bone_name is None and bone_names:
                # Best guess for the short_intro track: the root bone.
                bone_name = bone_names[0] if track.layout == "short_intro" else None
            if bone_name is None:
                stats["tracks_unmatched"] += 1
                continue
            if bone_name in already_written:
                # Some clips have a low-key std track for the root bone
                # alongside the short_intro track — skip the duplicate.
                stats["tracks_unmatched"] += 1
                continue
            already_written.add(bone_name)
            # The first track's intro KF holds bind/init data, not a real
            # pose — skip it and use the first standard KF as the baseline.
            if track.layout == "short_intro":
                anim_kfs = track.keyframes[1:] if len(track.keyframes) > 1 else track.keyframes
            else:
                anim_kfs = track.keyframes
            n_kf = len(anim_kfs)
            if n_kf == 0:
                continue

            data_path_quat = f'pose.bones["{bone_name}"].rotation_quaternion'

            # KF[1] (== anim_kfs[0]) is the .anim's rest baseline. Remaining
            # KFs are absolute in that frame; pose-space delta = rest^-1 @ kf.
            rest_kf = anim_kfs[0]
            rest_mat = (
                Matrix.Translation(rest_kf.location)
                @ Euler(rest_kf.rotation_euler, 'XYZ').to_matrix().to_4x4()
            )
            inv_rest = rest_mat.inverted()

            frames: list[float] = []
            delta_locs: list[tuple[float, float, float]] = []
            delta_quats: list[tuple[float, float, float, float]] = []
            prev_quat = None
            for kf in anim_kfs:
                frame = kf.time_ms / 1000.0 * fps
                if frame > max_frame:
                    max_frame = frame
                kf_mat = (
                    Matrix.Translation(kf.location)
                    @ Euler(kf.rotation_euler, 'XYZ').to_matrix().to_4x4()
                )
                delta = inv_rest @ kf_mat
                d_loc = delta.to_translation()
                d_quat = delta.to_quaternion()
                # Flip sign so consecutive quaternions take the short path —
                # otherwise Blender's linear fcurve interpolation can flip
                # halfway and produce a 360° spin.
                if prev_quat is not None and d_quat.dot(prev_quat) < 0:
                    d_quat = -d_quat
                prev_quat = d_quat
                frames.append(frame)
                delta_locs.append((d_loc.x, d_loc.y, d_loc.z))
                delta_quats.append((d_quat.w, d_quat.x, d_quat.y, d_quat.z))

            fcurves = []
            if apply_location:
                data_path_loc = f'pose.bones["{bone_name}"].location'
                for i in range(3):
                    fc = action.fcurves.new(data_path=data_path_loc, index=i)
                    fcurves.append((fc, "loc", i))
            for i in range(4):
                fc = action.fcurves.new(data_path=data_path_quat, index=i)
                fcurves.append((fc, "quat", i))

            for fc, kind, axis in fcurves:
                fc.keyframe_points.add(count=n_kf)
                for k_idx in range(n_kf):
                    if kind == "loc":
                        val = delta_locs[k_idx][axis]
                    else:
                        val = delta_quats[k_idx][axis]
                    pt = fc.keyframe_points[k_idx]
                    pt.co = (frames[k_idx], val)
                    pt.interpolation = 'LINEAR'
                fc.keyframe_points.sort()
                fc.update()
                stats["fcurves_written"] += 1
            stats["keyframes_total"] += n_kf * len(fcurves)

        if first_action is None:
            first_action = action
        stats["actions_built"] += 1

    stats["max_frame"] = max_frame

    if first_action is not None:
        anim_data.action = first_action
        # Blender 4.4+ slotted actions: legacy fcurves need a slot bound for
        # evaluation. If one exists from setting .action above, use it;
        # otherwise create one for this armature object.
        try:
            if hasattr(first_action, "slots"):
                if len(first_action.slots) > 0:
                    target_slot = first_action.slots[0]
                else:
                    target_slot = first_action.slots.new(
                        id_type='OBJECT', name=arm_obj.name,
                    )
                if hasattr(anim_data, "action_slot"):
                    anim_data.action_slot = target_slot
        except Exception as e:
            if log:
                log.warning("anim_apply: action_slot binding failed: %s", e)

    # Stretch the scene's timeline so the animation actually fits.
    scene = bpy.context.scene
    scene.frame_start = 1
    new_end = int(max_frame) + 5 if max_frame > 0 else scene.frame_end
    if new_end > scene.frame_end:
        scene.frame_end = new_end
    scene.render.fps = int(fps)

    if log:
        log.info(
            "anim_apply: built %d actions, %d fcurves, %d KFs, max frame=%.1f, "
            "%d tracks unmatched",
            stats["actions_built"], stats["fcurves_written"],
            stats["keyframes_total"], stats["max_frame"], stats["tracks_unmatched"],
        )
    return stats
