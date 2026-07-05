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

import bisect
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
    from mathutils import Matrix, Euler, Vector

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

    def _kfs_physical(kfs) -> bool:
        """False if any keyframe has non-physical values — the signature of a
        track the decoder mis-read (e.g. the belly track in the sparse 2-frame
        adult runFwd clips). We drop just that TRACK, not the whole clip, so the
        remaining bones still animate (the bad bone simply stays at rest)."""
        for kf in kfs:
            if any(abs(x) > 50.0 for x in kf.rotation_euler):
                return False
            if any(abs(x) > 1.0e4 for x in kf.location):
                return False
        return True

    dropped_tracks = 0
    for clip in doc.clips:
        # Which bone does the header-less "short_intro" track (track 0, no id)
        # belong to? The .anim orders tracks ALPHABETICALLY by bone name and omits
        # the id on the first one — so it's the single animated bone left uncovered
        # by the explicit-id tracks (for cattle that's cow_belly, alphabetically
        # first). Resolve it by elimination. (The old code sent it to the root,
        # which then played the belly's motion and moved the whole rig "off by a
        # node"; the root has its OWN explicit track and doesn't need this one.)
        _covered = set()
        for track in clip.bone_tracks:
            if track.bone_node_id == -1:
                continue
            nm = (anim_id_to_name.get(track.bone_node_id) if anim_id_to_name else None)
            if nm is None and node_id_to_bone is not None:
                nm = node_id_to_bone.get(track.bone_node_id)
            if nm in pbones:
                _covered.add(nm)
        # The .anim orders tracks alphabetically by bone name, so the header-less
        # track 0 is the alphabetically-FIRST animated bone — i.e. the smallest
        # uncovered bone name (for cattle: cow_belly). min() handles both the
        # dense case (one uncovered bone) and sparse clips (several uncovered).
        _uncovered = [bn for bn in bone_names if bn not in _covered]
        short_intro_bone = min(_uncovered) if _uncovered else (bone_names[0] if bone_names else None)

        # Resolve each track to a bone name -> its keyframe list.
        anim_kfs_by_bone: dict[str, list] = {}
        for track in clip.bone_tracks:
            bone_name = None
            if track.bone_node_id != -1:
                if anim_id_to_name is not None:
                    bone_name = anim_id_to_name.get(track.bone_node_id)
                if bone_name is None and node_id_to_bone is not None:
                    bone_name = node_id_to_bone.get(track.bone_node_id)
            if bone_name is None and track.layout == "short_intro":
                bone_name = short_intro_bone
            if bone_name is None or bone_name not in pbones or bone_name in anim_kfs_by_bone:
                stats["tracks_unmatched"] += 1
                continue
            # Drop the short_intro track's init KF (bind data, not a pose).
            kfs = (track.keyframes[1:]
                   if track.layout == "short_intro" and len(track.keyframes) > 1
                   else track.keyframes)
            if not kfs:
                continue
            # Skip a mis-decoded track (non-physical values) — keep the clip, that
            # one bone just holds its rest pose.
            if not _kfs_physical(kfs):
                dropped_tracks += 1
                continue
            anim_kfs_by_bone[bone_name] = kfs

        if not anim_kfs_by_bone:
            continue

        # Per-bone sampling arrays: sorted times + parallel (loc, quat). Convert
        # each keyframe's Euler to a quaternion now and keep the sequence in one
        # hemisphere so interpolation is short-path.
        bone_samples: dict[str, tuple] = {}
        for bn, kfs in anim_kfs_by_bone.items():
            ts = [kf.time_ms for kf in kfs]
            locs = [Vector(kf.location) for kf in kfs]
            quats = [Euler(kf.rotation_euler, 'XYZ').to_quaternion() for kf in kfs]
            for j in range(1, len(quats)):
                if quats[j].dot(quats[j - 1]) < 0.0:
                    quats[j].negate()
            bone_samples[bn] = (ts, locs, quats)

        # Shared timeline = UNION of every animated bone's keyframe times. GIANTS
        # keyframes each bone SPARSELY and INDEPENDENTLY (in one adult clip, bones
        # can have 35, 28 or a single keyframe, at different times), so the old
        # "index i lines up across all tracks" assumption desynced the rig. We
        # instead sample every bone BY TIME at each timeline point (below).
        _tset: set = set()
        for ts, _l, _q in bone_samples.values():
            _tset.update(ts)
        times = sorted(_tset)
        n = len(times)

        # No root re-anchor: the keyframes are absolute local transforms, applied
        # as-is (exactly what GIANTS Editor does). An earlier version subtracted
        # the first bone's frame-0 translation offset — a band-aid for the old
        # belly→root mis-mapping — but `bone_names[0]` is the first real bone,
        # which for the calf is calf_spine_01 (the bone that TRANSLATES the body
        # down to lie). Re-anchoring it forced the sleep loops back to standing
        # height and floated the animal. With the short_intro mapping fixed, no
        # re-anchor is needed and every clip matches GE.
        def _anim_local(bn: str, i: int) -> "Matrix":
            entry = bone_samples.get(bn)
            if entry is None:
                return _joint_local(bones[bn])          # static bone: hold rest
            ts, locs, quats = entry
            t = times[i]
            idx = bisect.bisect_left(ts, t)
            if idx <= 0:
                loc, q = locs[0], quats[0]
            elif idx >= len(ts):
                loc, q = locs[-1], quats[-1]
            elif ts[idx] == t:
                loc, q = locs[idx], quats[idx]
            else:                                        # interpolate this bone's
                t0, t1 = ts[idx - 1], ts[idx]            # own bracketing keyframes
                f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                loc = locs[idx - 1].lerp(locs[idx], f)
                q = quats[idx - 1].slerp(quats[idx], f)
            return Matrix.Translation(loc) @ q.to_matrix().to_4x4()

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
    stats["tracks_dropped"] = dropped_tracks
    if dropped_tracks and log:
        log.info("anim_apply: dropped %d mis-decoded track(s) across clips "
                 "(those bones hold rest; the clips still play)", dropped_tracks)

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
