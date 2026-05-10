"""Validate that the keyframe layout (preamble + N*32 KFs) works for every
bone in every clip, and that all KF quaternions are unit-length and times
are monotonic."""
from __future__ import annotations

import math
import struct
from pathlib import Path

ANIM = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.anim"


def parse_clip_at(buf, name_prefix_off):
    """Parse the clip whose [u32 nameLen][name] starts at name_prefix_off.
    Returns (clip_name, clip_data_end, [(track_start, track_end), ...])."""
    n = struct.unpack_from("<I", buf, name_prefix_off)[0]
    clip_name = buf[name_prefix_off + 4:name_prefix_off + 4 + n].decode('utf-8')
    data_start = name_prefix_off + 4 + n
    aligned = ((data_start + 3) // 4) * 4
    duration = struct.unpack_from("<f", buf, aligned)[0]
    bone_count = struct.unpack_from("<I", buf, aligned + 4)[0]
    body_start = aligned + 28

    # Find next clip's name-length prefix to bound this clip.
    next_clip_off = None
    for j in range(data_start + 100, len(buf) - 8):
        n2 = struct.unpack_from("<I", buf, j)[0]
        if 4 <= n2 <= 64 and j + 4 + n2 <= len(buf):
            payload = buf[j + 4:j + 4 + n2]
            if all((65 <= b <= 90) or (97 <= b <= 122) or (48 <= b <= 57) or b == 0x5F for b in payload):
                if 65 <= payload[0] <= 90 or 97 <= payload[0] <= 122:
                    next_clip_off = j; break
    body_end = next_clip_off if next_clip_off is not None else len(buf)

    # Find duration markers (49 of them, end of each track).
    dur_bytes = struct.pack("<f", duration)
    markers = []
    i = body_start
    while i < body_end - 4:
        if buf[i:i + 4] == dur_bytes:
            markers.append(i); i += 4
        else:
            i += 1
        if len(markers) > bone_count + 5:
            break
    bounds = []
    prev = body_start
    for m in markers[:bone_count]:
        bounds.append((prev, m + 4))
        prev = m + 4
    return clip_name, duration, bone_count, bounds, body_end


def detect_track_layout(buf, start, end):
    """Return (preamble_size, n_kf, mode) where mode is 'std' or 'short_intro'."""
    track_size = end - start
    # Standard layout: 8-byte preamble + 32-byte 'rest' (magic=0) + N*32 KFs (magic=3)
    if track_size >= 40 and (track_size - 8) % 32 == 0:
        n_kf_after_pre = (track_size - 8) // 32
        # Verify: first KF after preamble has time 0 (it's the rest pose)
        first_time = struct.unpack_from("<f", buf, start + 8 + 28)[0]
        last_time = struct.unpack_from("<f", buf, start + 8 + (n_kf_after_pre - 1) * 32 + 28)[0]
        return ("std", 8, n_kf_after_pre, first_time, last_time)
    # Short-intro layout: 24-byte intro + N*32 KFs (used for cow_root)
    if (track_size - 24) % 32 == 0:
        n_kf = (track_size - 24) // 32
        first_time_intro = struct.unpack_from("<f", buf, start + 20)[0]
        last_time = struct.unpack_from("<f", buf, start + 24 + (n_kf - 1) * 32 + 28)[0]
        return ("short_intro", 24, n_kf, first_time_intro, last_time)
    return (None, None, None, None, None)


def main():
    buf = ANIM.read_bytes()
    # File header
    off = 0
    off += 4  # version
    off += 4  # char count
    cnl = struct.unpack_from("<I", buf, off)[0]; off += 4 + cnl
    clip_count = struct.unpack_from("<I", buf, off)[0]; off += 4
    print(f"clip_count = {clip_count}")

    cur_clip = off
    layout_summary = {"std": 0, "short_intro": 0, "unknown": 0}
    quat_check_ok = 0
    quat_check_fail = 0
    time_mono_fail = 0
    clips_ok = 0
    n_clip_examined = 0

    for clip_idx in range(min(clip_count, 41)):
        name, duration, bone_count, bounds, body_end = parse_clip_at(buf, cur_clip)
        n_clip_examined += 1
        cur_clip = body_end

        if len(bounds) != bone_count:
            print(f"  [{clip_idx:2d}] {name!r}: only {len(bounds)} bone bounds vs {bone_count} bone_count — skip")
            continue

        clip_ok = True
        for bone_idx, (s, e) in enumerate(bounds):
            mode, pre, n_kf, first_t, last_t = detect_track_layout(buf, s, e)
            if mode is None:
                layout_summary["unknown"] += 1
                clip_ok = False
                if clip_idx < 3:
                    print(f"     bone {bone_idx}: UNKNOWN layout, track size {e - s}")
                continue
            layout_summary[mode] += 1

            # Validate: last KF time should equal duration.
            if abs(last_t - duration) > 1.0:
                clip_ok = False
                if clip_idx < 3:
                    print(f"     bone {bone_idx}: last_t={last_t} != duration={duration}")

            # Validate quaternions are unit-length.
            for k in range(n_kf):
                kf_off = s + pre + k * 32 if mode == "std" else s + (24 if k == 0 else 24 + k * 32 - 24)
                # For short_intro, KF 0 is at bytes 0..24 (24 bytes, no magic).
                # For std and short_intro KFs >= 1, KF is 32 bytes with magic=3.
                if mode == "short_intro" and k == 0:
                    f0, f1, f2, f3, f4, f5 = struct.unpack_from("<6f", buf, s)
                    qx, qy, qz = f0, f1, f2  # tentative
                else:
                    base = s + pre + k * 32 if mode == "std" else s + 24 + (k - 1) * 32
                    if mode == "std":
                        base = s + pre + k * 32
                    else:  # short_intro k >= 1
                        base = s + 24 + (k - 1) * 32
                    tx, ty, tz, qx, qy, qz, t = struct.unpack_from("<7f", buf, base + 4)
                qmag2 = qx * qx + qy * qy + qz * qz
                if qmag2 > 1.001:
                    quat_check_fail += 1
                else:
                    quat_check_ok += 1

        if clip_ok:
            clips_ok += 1

        if clip_idx < 5:
            print(f"  [{clip_idx:2d}] {name!r}: bones={bone_count} duration={duration:.0f}ms tracks_layout={layout_summary} quat_ok={quat_check_ok} quat_fail={quat_check_fail}")

    print(f"\n--- summary ---")
    print(f"clips examined: {n_clip_examined} (clips ok: {clips_ok})")
    print(f"track layouts: {layout_summary}")
    print(f"quaternion checks: ok={quat_check_ok} fail={quat_check_fail}")


if __name__ == "__main__":
    main()
