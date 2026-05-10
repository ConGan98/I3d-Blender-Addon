"""Find some keyframes whose 'last 3' floats fail the unit-quat check
and dump them to see if a different interpretation works."""
from __future__ import annotations

import math
import struct
from pathlib import Path

ANIM = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.anim"


def main():
    buf = ANIM.read_bytes()
    off = 0
    off += 4
    off += 4
    cnl = struct.unpack_from("<I", buf, off)[0]; off += 4 + cnl
    off += 4
    n = struct.unpack_from("<I", buf, off)[0]
    cdata = off + 4 + n
    aligned = ((cdata + 3) // 4) * 4
    duration = struct.unpack_from("<f", buf, aligned)[0]
    body_start = aligned + 28

    dur_bytes = struct.pack("<f", duration)
    markers = []
    i = body_start
    while i < len(buf) - 4:
        if buf[i:i + 4] == dur_bytes:
            markers.append(i); i += 4
        else:
            i += 1
        if len(markers) > 50:
            break

    bounds = []
    prev = body_start
    for m in markers[:49]:
        bounds.append((prev, m + 4))
        prev = m + 4

    # Iterate per-bone tracks and find failing KFs.
    failing_examples = []
    for bone_idx, (s, e) in enumerate(bounds):
        track_size = e - s
        if (track_size - 8) % 32 == 0:
            preamble = 8
        elif track_size % 32 == 24:
            # Cow_root style: 24-byte intro then KFs at 24+.
            # Skip for this analysis.
            continue
        else:
            continue
        n_kf = (track_size - preamble) // 32
        for k in range(n_kf):
            base = s + preamble + k * 32
            magic = struct.unpack_from("<I", buf, base)[0]
            tx, ty, tz, qx, qy, qz, t = struct.unpack_from("<7f", buf, base + 4)
            qmag2_last = qx * qx + qy * qy + qz * qz
            qmag2_first = tx * tx + ty * ty + tz * tz
            # Check if last 3 fail
            if qmag2_last > 1.001:
                failing_examples.append((bone_idx, k, magic, (tx, ty, tz), (qx, qy, qz), t, qmag2_last, qmag2_first))
                if len(failing_examples) >= 10:
                    break
        if len(failing_examples) >= 10:
            break

    # Also collect a few PASSING examples for comparison.
    passing_examples = []
    for bone_idx, (s, e) in enumerate(bounds):
        track_size = e - s
        if (track_size - 8) % 32 != 0:
            continue
        n_kf = (track_size - 8) // 32
        for k in range(n_kf):
            base = s + 8 + k * 32
            magic = struct.unpack_from("<I", buf, base)[0]
            tx, ty, tz, qx, qy, qz, t = struct.unpack_from("<7f", buf, base + 4)
            qmag2_last = qx * qx + qy * qy + qz * qz
            if qmag2_last < 1.001 and qmag2_last > 0.0001:
                passing_examples.append((bone_idx, k, magic, (tx, ty, tz), (qx, qy, qz), t, qmag2_last))
                if len(passing_examples) >= 5:
                    break
        if len(passing_examples) >= 5:
            break

    print("=== FAILING examples (|last 3 floats|^2 > 1.001) ===")
    for x in failing_examples:
        bone_idx, k, magic, trans, quat_xyz, t, mag2_last, mag2_first = x
        print(f"  bone={bone_idx} kf={k:3d} magic={magic} time={t:.1f}")
        print(f"    first 3: {trans}  |·|²={mag2_first:.4f}")
        print(f"    last 3:  {quat_xyz}  |·|²={mag2_last:.4f}")

    print("\n=== PASSING examples (last 3 looks like unit quat XYZ) ===")
    for x in passing_examples:
        bone_idx, k, magic, trans, quat_xyz, t, mag2_last = x
        print(f"  bone={bone_idx} kf={k:3d} magic={magic} time={t:.1f}")
        print(f"    first 3: {trans}")
        print(f"    last 3:  {quat_xyz}  |·|²={mag2_last:.4f}")


if __name__ == "__main__":
    main()
