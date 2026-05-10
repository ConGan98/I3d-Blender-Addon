"""
Decode keyframes for cow_jaw_skin_jnt in chewSource and verify the layout:
  per-bone track = preamble + N keyframes
  per-keyframe = [u32=3][f32 tx][f32 ty][f32 tz][f32 qx][f32 qy][f32 qz][f32 time_ms]

Print: time_ms (should be monotonically increasing, ending at duration=9000)
       quat (xyz, derive w via sqrt(1-x²-y²-z²))
       trans (should be ~constant for jaw which doesn't translate)
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

ANIM = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.anim"


def main():
    buf = ANIM.read_bytes()

    # Header & locate clip 0 body
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

    # Find duration markers (49 of them, end of each bone track).
    dur_bytes = struct.pack("<f", duration)
    markers = []
    i = body_start
    while i <= len(buf) - 4:
        if buf[i:i + 4] == dur_bytes:
            markers.append(i)
            i += 4
        else:
            i += 1
        if len(markers) > 50:
            break
    print(f"Found {len(markers)} duration markers")

    # Bounds: track i = [prev_end .. markers[i] + 4]
    bounds = []
    prev_end = body_start
    for m in markers[:50]:
        bounds.append((prev_end, m + 4))
        prev_end = m + 4

    # Bone 8 = cow_jaw_skin_jnt (DFS index)
    JAW = 8
    s, e = bounds[JAW]
    print(f"\nJaw track: bytes {s}..{e}, size = {e - s}")

    # Try to detect preamble size: scan for first u32=3 magic (start of first KF
    # with magic). For most bones it's at byte 0; preamble extends to byte 8.
    # KFs are 32 bytes each.
    # Validate by checking the LAST KF's time matches duration.
    for preamble_size in (0, 8, 16, 24, 32, 40):
        body_size = (e - s) - preamble_size
        if body_size <= 0 or body_size % 32 != 0:
            continue
        n_kf = body_size // 32
        # Check last KF time:
        last_kf_off = e - 32
        last_time = struct.unpack_from("<f", buf, last_kf_off + 28)[0]
        first_kf_off = s + preamble_size
        first_magic = struct.unpack_from("<I", buf, first_kf_off)[0]
        first_time = struct.unpack_from("<f", buf, first_kf_off + 28)[0]
        print(f"  preamble={preamble_size:3d}  n_kf={n_kf:4d}  first_magic={first_magic:3d}  first_time={first_time:>9.4f}  last_time={last_time:>9.4f}")

    # Now parse with the assumed best layout: preamble=8, KFs=32 bytes.
    print("\n=== Parsing with preamble=8, KF=32 bytes ===")
    preamble_size = 8
    body_size = (e - s) - preamble_size
    n_kf = body_size // 32
    # Show preamble
    pre = struct.unpack_from("<8B", buf, s)
    pre_u32_a = struct.unpack_from("<I", buf, s)[0]
    pre_u32_b = struct.unpack_from("<I", buf, s + 4)[0]
    pre_f_b = struct.unpack_from("<f", buf, s + 4)[0]
    print(f"  preamble: u32_a={pre_u32_a}  u32_b={pre_u32_b} (f32={pre_f_b:.6g})")

    # Parse keyframes
    print(f"\n  {'idx':>4s}  {'time_ms':>10s}  {'tx':>10s}  {'ty':>10s}  {'tz':>10s}  {'qx':>10s}  {'qy':>10s}  {'qz':>10s}  {'qw':>10s}  |q|")
    print("  " + "-" * 110)
    last_ts = []
    for k in range(n_kf):
        kf_off = s + preamble_size + k * 32
        magic = struct.unpack_from("<I", buf, kf_off)[0]
        tx, ty, tz, qx, qy, qz, t = struct.unpack_from("<7f", buf, kf_off + 4)
        # Derive qw
        sum_sq = qx * qx + qy * qy + qz * qz
        qw_sq = max(0.0, 1.0 - sum_sq)
        qw = math.sqrt(qw_sq)
        qmag = math.sqrt(sum_sq + qw * qw)
        if magic != 3:
            print(f"  [{k:4d}] !! magic={magic} (expected 3)")
        if k < 5 or k >= n_kf - 3:
            print(f"  [{k:4d}]  {t:10.4f}  {tx:10.4f}  {ty:10.4f}  {tz:10.4f}  {qx:10.4f}  {qy:10.4f}  {qz:10.4f}  {qw:10.4f}  {qmag:.4f}")
        last_ts.append(t)

    # Time check
    print("\n  Time monotonically increasing? ", all(last_ts[i + 1] >= last_ts[i] for i in range(len(last_ts) - 1)))
    print(f"  Total KFs: {n_kf}, time range: {last_ts[0]:.2f} to {last_ts[-1]:.2f} ms")
    if len(last_ts) > 1:
        diffs = [last_ts[i + 1] - last_ts[i] for i in range(len(last_ts) - 1)]
        from collections import Counter
        c = Counter(round(d, 1) for d in diffs)
        print(f"  Time diff distribution (top 8): {c.most_common(8)}")


if __name__ == "__main__":
    main()
