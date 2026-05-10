"""Dump raw bytes around track boundaries for chewSource. Looking for the
real per-track structure — our parser has tracks shifted relative to the
actual data layout, evidenced by track N+1's unk_a always equaling track N's
KF[1].loc.x.
"""
from __future__ import annotations

import struct
from pathlib import Path

ANIM = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.anim"


def main():
    buf = ANIM.read_bytes()

    # Skip file header to find chewSource clip body.
    off = 0
    off += 4  # version
    off += 4  # char_count
    nl = struct.unpack_from("<I", buf, off)[0]
    off += 4 + nl  # char name
    clip_count = struct.unpack_from("<I", buf, off)[0]
    off += 4
    print(f"clip_count={clip_count}")

    # Read first clip name (chewSource)
    cnl = struct.unpack_from("<I", buf, off)[0]
    name = buf[off + 4:off + 4 + cnl].decode("ascii", errors="replace")
    off += 4 + cnl
    aligned = ((off + 3) // 4) * 4
    print(f"clip[0] name={name!r}, header at offset 0x{aligned:x}")

    duration = struct.unpack_from("<f", buf, aligned)[0]
    bone_count = struct.unpack_from("<I", buf, aligned + 4)[0]
    unk1 = struct.unpack_from("<I", buf, aligned + 8)[0]
    frame_count = struct.unpack_from("<I", buf, aligned + 12)[0]
    print(f"  duration={duration}, bone_count={bone_count}, "
          f"unk1={unk1}, frame_count={frame_count}")

    body_start = aligned + 28
    print(f"  body_start = 0x{body_start:x}")

    # Find all 9000.0 markers between body_start and the start of clip[1]
    dur_bytes = struct.pack("<f", duration)
    markers = []
    i = body_start
    end_search = min(len(buf), body_start + 1_000_000)  # bound the search
    while i < end_search - 4:
        if buf[i:i + 4] == dur_bytes:
            markers.append(i)
            i += 4
        else:
            i += 1

    print(f"\nFound {len(markers)} occurrences of duration marker {duration} "
          f"in first 1MB after body_start")

    # Walk first 5 markers — print bytes in 4-float chunks around each boundary.
    print(f"\n=== Bytes around first 5 markers ===")
    for k, m in enumerate(markers[:5]):
        print(f"\n-- marker {k} at file offset 0x{m:x} --")
        # Print 16 floats before and 16 floats after the marker
        start = max(0, m - 64)
        end = min(len(buf), m + 64)
        for j in range(start, end, 4):
            f = struct.unpack_from("<f", buf, j)[0]
            u = struct.unpack_from("<I", buf, j)[0]
            tag = ""
            if j == m:
                tag = "  <<< MARKER (9000.0)"
            elif j == m - 4:
                tag = "  (just before marker)"
            elif j == m + 4:
                tag = "  (just after marker)"
            print(f"  0x{j:06x}  f32={f:>14.6f}   u32={u:>10d}{tag}")


if __name__ == "__main__":
    main()
