"""
Apply a parsed AnimDocument to a Blender armature: one Action per clip, driving
pose bones so the skinned mesh deforms correctly.

Approach — forward kinematics on the DEFORMATION, not the local pose
-------------------------------------------------------------------
The .i3d.anim stores each bone's ABSOLUTE local transform (relative to its
parent) in the raw GIANTS joint frame, as (translation.xyz, Euler-XYZ-radians).
What actually deforms the mesh is each bone's WORLD transform relative to its
bind (rest) world:  D[b] = animWorld[b] @ restWorld[b]^-1.

Working in Blender/armature space (armature object is at identity), with
`src_world` = the axis-conversion + parent chain baked into the bones at import:

    D[b] = src_world @ JA[b] @ JR[b]^-1 @ src_world^-1

    JR[b] = forward-kinematic accumulation of the joint REST locals
            (from each bone's `_i3d_translation` / `_i3d_rotation_zyx_deg`)
    JA[b] = forward-kinematic accumulation of the ANIM locals (the keyframes)

D[b] is independent of how the Blender bone is *drawn*, so we no longer need the
"exact orientation" (sideways) bones — normal point-at-child bones deform
correctly. We realise D[b] by setting the target pose matrix
    target[b] = D[b] @ bone.matrix_local
and converting that to Blender's pose `matrix_basis` (which the fcurves drive):
    root:  basis = matloc[b]^-1 @ target[b]
    child: basis = (matloc[parent]^-1 @ matloc[b])^-1 @ target[parent]^-1 @ target[b]

Bones without an anim track stay at rest (basis = identity) and simply follow
their animated parent through Blender's own pose FK.
"""
from __future__ import annotations

from math import radians
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import bpy

from . import anim_reader as ar


def _bone_dfs_order(arm_data) -> list:
    """Return pose-bone names in DFS order (roots first, then children)."""
    names = []
    seen = set()

    def walk(bone):
        if bone.name in seen:
            return
        seen.add(bone.name)
        names.append(bone.name)
        for c in bone.children:
            walk(c)

    for r in [b for b in arm_data.bones if b.parent is None]:
        walk(r)
    return names


def apply_animation(
    arm_obj, doc: ar.AnimDocument, *,
    node_id_to_bone: dict[int, str] | None = None,
    anim_id_to_name: dict[int, str] | None = None,
    fps: float = 30.0,
    apply_location: bool = True,
    log=None,
):
    """Build a Blender Action for every clip in `doc` and bind the first one to
    the armature. Returns a stats dict.

    `anim_id_to_name` maps the .anim's own bone node ids (from the ANIMATION i3d,
    e.g. cattleCalfAnimations.i3d — calf uses 4..48) to bone NAMES, so tracks
    bind by name when the animation lives in a separate file from the model.
    Falls back to `node_id_to_bone` (the model's own id->name).
    """
    import bpy
    from mathutils import Matrix, Euler

    stats = {"actions_built": 0, "fcurves_written": 0, "tracks_unmatched": 0,
             "keyframes_total": 0, "max_frame": 0.0}
    if arm_obj is None or arm_obj.type != 'ARMATURE':
        if log:
            log.warning("anim_apply: arm_obj is not an Armature")
        return stats

    bones = arm_obj.data.bones
    pbones = arm_obj.pose.bones
    bone_names = _bone_dfs_order(arm_obj.data)
    if log:
        log.info("anim_apply: armature has %d bones in DFS order", len(bone_names))

    # Quaternion channels — GIANTS bones park near euler gimbal zones; quaternions
    # let Blender slerp cleanly instead of linearly blowing through wild eulers.
    for bn in bone_names:
        pb = pbones.get(bn)
        if pb is not None:
            pb.rotation_mode = 'QUATERNION'

    # src_world = axis conversion (+ parent chain) baked into the bones at import.
    src_world = Matrix.Identity(4)
    raw_sw = arm_obj.get("_i3d_src_world")
    if raw_sw and len(raw_sw) == 16:
        src_world = Matrix([raw_sw[0:4], raw_sw[4:8], raw_sw[8:12], raw_sw[12:16]])
    inv_src = src_world.inverted_safe()

    def _joint_local(bone) -> "Matrix":
        """The bone's REST local transform in the GIANTS joint frame, rebuilt
        from the source props stashed at import (same convention as the importer:
        Blender Euler order 'XYZ' == GIANTS intrinsic ZY'X'')."""
        t = bone.get("_i3d_translation")
        r = bone.get("_i3d_rotation_zyx_deg")
        s = bone.get("_i3d_scale")
        loc = Matrix.Translation((t[0], t[1], t[2])) if t else Matrix.Identity(4)
        rot = (Euler((radians(r[0]), radians(r[1]), radians(r[2])), 'XYZ')
               .to_matrix().to_4x4()) if r else Matrix.Identity(4)
        scl = Matrix.Diagonal((s[0], s[1], s[2], 1.0)) if s else Matrix.Identity(4)
        return loc @ rot @ scl

    # Static per-bone data: joint rest FK (JR), Blender rest arm-space (matloc),
    # and the precomputed inverses the per-frame math needs.
    JR: dict[str, "Matrix"] = {}
    inv_JR: dict[str, "Matrix"] = {}
    matloc: dict[str, "Matrix"] = {}
    rest_rel_inv: dict[str, "Matrix"] = {}   # (matloc[parent]^-1 @ matloc[b])^-1
    missing_props = 0
    for bn in bone_names:
        bone = bones[bn]
        if bone.get("_i3d_translation") is None:
            missing_props += 1
        matloc[bn] = bone.matrix_local.copy()
        local = _joint_local(bone)
        p = bone.parent
        JR[bn] = (JR[p.name] @ local) if (p is not None and p.name in JR) else local
        inv_JR[bn] = JR[bn].inverted_safe()
        if p is not None:
            rest_rel_inv[bn] = matloc[bn].inverted_safe() @ matloc[p.name]
        else:
            rest_rel_inv[bn] = matloc[bn].inverted_safe()
    if missing_props and log:
        log.warning("anim_apply: %d bone(s) lack _i3d source transforms — their "
                    "rest FK may be off (re-import so build_armature stores them)",
                    missing_props)

    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    anim_data = arm_obj.animation_data

    first_action = None
    max_frame = 0.0

    def _clip_is_sane(clip) -> tuple[bool, str]:
        """Reject clips the .i3d.anim decoder mis-read. A desynced clip parses
        fewer tracks than `bone_count` and/or yields non-physical keyframe
        values; binding such a clip explodes the skinned mesh. Better to skip
        it (and say so) than to deform the model. Seen on ultra-short clips
        (e.g. 2-frame runFwd*)."""
        if len(clip.bone_tracks) < clip.bone_count:
            return False, (f"{len(clip.bone_tracks)}/{clip.bone_count} tracks "
                           f"recovered")
        for t in clip.bone_tracks:
            for kf in t.keyframes:
                if any(abs(x) > 50.0 for x in kf.rotation_euler):
                    return False, "non-physical rotation (decoder desync)"
                if any(abs(x) > 1.0e4 for x in kf.location):
                    return False, "non-physical translation (decoder desync)"
        return True, ""

    skipped_clips: list[str] = []
    for clip in doc.clips:
        sane, why = _clip_is_sane(clip)
        if not sane:
            skipped_clips.append(clip.name)
            if log:
                log.warning("anim_apply: skipping clip %r — %s; the decoder "
                            "can't read this clip cleanly yet, so it would "
                            "deform the mesh.", clip.name, why)
            continue
        # Resolve each track to a bone name -> its keyframe list.
        anim_kfs_by_bone: dict[str, list] = {}
        for track in clip.bone_tracks:
            bone_name = None
            if track.bone_node_id != -1:
                if anim_id_to_name is not None:
                    bone_name = anim_id_to_name.get(track.bone_node_id)
                if bone_name is None and node_id_to_bone is not None:
                    bone_name = node_id_to_bone.get(track.bone_node_id)
            if bone_name is None and track.layout == "short_intro" and bone_names:
                bone_name = bone_names[0]
            if bone_name is None or bone_name not in pbones or bone_name in anim_kfs_by_bone:
                stats["tracks_unmatched"] += 1
                continue
            # Drop the short_intro track's init KF (bind data, not a pose).
            kfs = (track.keyframes[1:]
                   if track.layout == "short_intro" and len(track.keyframes) > 1
                   else track.keyframes)
            if kfs:
                anim_kfs_by_bone[bone_name] = kfs

        if not anim_kfs_by_bone:
            continue

        # Shared timeline: the longest track's sample times (GIANTS clips keyframe
        # all bones together, so indices line up; shorter tracks clamp to last).
        ref = max(anim_kfs_by_bone.values(), key=len)
        times = [kf.time_ms for kf in ref]
        n = len(times)

        # Root re-anchor: the .i3d.anim bakes a constant bind offset into the
        # root track's translation (e.g. Y≈-0.56) that the model's rest pose
        # doesn't carry, so every frame shoves the whole rig off-centre. Subtract
        # (frame-0 anim translation − rest translation) from the root each frame:
        # frame 0 then lands exactly on rest, and real per-frame root motion is
        # preserved relative to it. Only the root (DFS bone 0) needs this; child
        # bones' translations already match their rest.
        root_bn = bone_names[0] if bone_names else None
        root_loc_offset = None
        if root_bn in anim_kfs_by_bone:
            kf0 = anim_kfs_by_bone[root_bn][0]
            rest_t = _joint_local(bones[root_bn]).to_translation()
            from mathutils import Vector
            root_loc_offset = Vector(kf0.location) - rest_t

        def _anim_local(bn: str, i: int) -> "Matrix":
            kfs = anim_kfs_by_bone.get(bn)
            if kfs is None:
                return _joint_local(bones[bn])          # static bone: hold rest
            kf = kfs[i] if i < len(kfs) else kfs[-1]     # clamp short tracks
            loc = kf.location
            if bn == root_bn and root_loc_offset is not None:
                loc = (loc[0] - root_loc_offset[0],
                       loc[1] - root_loc_offset[1],
                       loc[2] - root_loc_offset[2])
            return (Matrix.Translation(loc)
                    @ Euler(kf.rotation_euler, 'XYZ').to_matrix().to_4x4())

        action = bpy.data.actions.new(name=f"i3dAnim_{clip.name}")
        try:
            action.id_root = 'OBJECT'
        except (AttributeError, TypeError):
            pass
        action.use_fake_user = True

        # (frame, location, quaternion) series per animated bone.
        series: dict[str, list] = {bn: [] for bn in anim_kfs_by_bone}
        prev_q: dict[str, object] = {bn: None for bn in anim_kfs_by_bone}

        for i in range(n):
            frame = times[i] / 1000.0 * fps
            if frame > max_frame:
                max_frame = frame

            JA: dict[str, "Matrix"] = {}
            target: dict[str, "Matrix"] = {}
            for bn in bone_names:                       # DFS: parents before children
                bone = bones[bn]
                p = bone.parent
                local = _anim_local(bn, i)
                JA[bn] = (JA[p.name] @ local) if (p is not None and p.name in JA) else local
                D = src_world @ JA[bn] @ inv_JR[bn] @ inv_src
                target[bn] = D @ matloc[bn]

                if bn not in series:
                    continue
                if p is not None and p.name in target:
                    basis = rest_rel_inv[bn] @ target[p.name].inverted_safe() @ target[bn]
                else:
                    basis = rest_rel_inv[bn] @ target[bn]
                loc = basis.to_translation()
                q = basis.to_quaternion()
                pq = prev_q[bn]
                if pq is not None and q.dot(pq) < 0.0:   # short-path for linear interp
                    q = -q
                prev_q[bn] = q
                series[bn].append((frame, loc, q))

        # Write fcurves from the series.
        for bn, samples in series.items():
            nkf = len(samples)
            if nkf == 0:
                continue
            fcurves = []
            if apply_location:
                dp_loc = f'pose.bones["{bn}"].location'
                for idx in range(3):
                    fcurves.append((action.fcurves.new(data_path=dp_loc, index=idx), "loc", idx))
            dp_quat = f'pose.bones["{bn}"].rotation_quaternion'
            for idx in range(4):
                fcurves.append((action.fcurves.new(data_path=dp_quat, index=idx), "quat", idx))
            for fc, kind, ax in fcurves:
                fc.keyframe_points.add(count=nkf)
                for k in range(nkf):
                    frame, loc, q = samples[k]
                    val = loc[ax] if kind == "loc" else (q.w, q.x, q.y, q.z)[ax]
                    pt = fc.keyframe_points[k]
                    pt.co = (frame, val)
                    pt.interpolation = 'LINEAR'
                fc.keyframe_points.sort()
                fc.update()
                stats["fcurves_written"] += 1
            stats["keyframes_total"] += nkf * len(fcurves)

        if first_action is None:
            first_action = action
        stats["actions_built"] += 1

    stats["max_frame"] = max_frame
    stats["clips_skipped"] = skipped_clips
    if skipped_clips and log:
        log.warning("anim_apply: skipped %d unreadable clip(s): %s",
                    len(skipped_clips), ", ".join(skipped_clips))

    if first_action is not None:
        anim_data.action = first_action
        # Blender 4.4+ slotted actions: legacy fcurves need a slot bound to eval.
        try:
            if hasattr(first_action, "slots"):
                target_slot = (first_action.slots[0] if len(first_action.slots) > 0
                               else first_action.slots.new(id_type='OBJECT', name=arm_obj.name))
                if hasattr(anim_data, "action_slot"):
                    anim_data.action_slot = target_slot
        except Exception as e:
            if log:
                log.warning("anim_apply: action_slot binding failed: %s", e)

    # Loop: give every imported action a CYCLES (REPEAT) modifier so the bound
    # clip repeats seamlessly to fill the timeline instead of playing once.
    for act in bpy.data.actions:
        if not act.name.startswith("i3dAnim_"):
            continue
        for fc in act.fcurves:
            if not fc.modifiers:
                m = fc.modifiers.new(type='CYCLES')
                m.mode_before = 'REPEAT'
                m.mode_after = 'REPEAT'

    # Scene range spans the longest clip; cyclic modifiers loop shorter clips.
    scene = bpy.context.scene
    scene.frame_start = 1
    new_end = int(round(max_frame)) + 1 if max_frame > 0 else scene.frame_end
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
