"""Dump full bytes of small per-bone tracks (cow_belly_skin_jnt = 72 bytes, etc.)
to figure out the per-bone keyframe layout."""
from __future__ import annotations

import struct
from pathlib import Path

ANIM = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.anim"
I3D = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d"


def dump_track(buf, start, end, label):
    print(f"\n=== {label}: bytes {start}..{end} ({end-start} bytes) ===")
    chunk = buf[start:end]
    # Print as hex grouped by 4 bytes (u32 / f32)
    for off in range(0, len(chunk), 16):
        row = chunk[off:off + 16]
        words = []
        for i in range(0, len(row), 4):
            if i + 4 <= len(row):
                w = row[i:i + 4]
                u = struct.unpack("<I", w)[0]
                f = struct.unpack("<f", w)[0]
                words.append(f"u32={u:11d} f32={f:12.6g}")
        hex_part = " ".join(f"{b:02x}" for b in row)
        print(f"  [+{off:4d}]  {hex_part:<48s}")
        for i, w in enumerate(words):
            print(f"           +{off + i*4:4d}: {w}")


def main():
    import xml.etree.ElementTree as ET
    root = ET.parse(I3D).getroot()
    scene = root.find("Scene")
    bones = []
    def walk(el):
        if el.tag == "TransformGroup" and "_skin_jnt" in el.attrib.get("name", ""):
            bones.append(el.attrib["name"])
        for c in el:
            walk(c)
    walk(scene)

    buf = ANIM.read_bytes()

    # Header & locate clip 0 body
    off = 0
    off += 4  # version
    off += 4  # char_count
    cnl = struct.unpack_from("<I", buf, off)[0]; off += 4 + cnl
    off += 4  # clip_count
    n = struct.unpack_from("<I", buf, off)[0]; cdata = off + 4 + n
    aligned = ((cdata + 3) // 4) * 4
    duration = struct.unpack_from("<f", buf, aligned)[0]
    body_start = aligned + 28

    # Find duration markers
    dur_bytes = struct.pack("<f", duration)
    markers = []
    i = body_start
    while i <= len(buf) - 4:
        if buf[i:i + 4] == dur_bytes:
            markers.append(i)
            i += 4
        else:
            i += 1
        if len(markers) > 60:
            break

    # Track 0: body_start..markers[0]+4
    # Track i (i>=1): markers[i-1]+4 .. markers[i]+4
    bounds = []
    prev_end = body_start
    for m in markers[:50]:
        bounds.append((prev_end, m + 4))
        prev_end = m + 4

    # Pick a few interesting bones to dump fully:
    interesting = [
        (0, "cow_root_skin_jnt"),
        (8, "cow_jaw_skin_jnt"),
        (13, "cow_ear_L_skin_jnt_02"),
        (31, "cow_belly_skin_jnt"),
        (48, "cow_udder_L_skin_jnt"),
    ]
    for idx, expected in interesting:
        if idx >= len(bounds) or idx >= len(bones):
            continue
        s, e = bounds[idx]
        actual_name = bones[idx] if idx < len(bones) else "<extra>"
        # Limit dump to first/last 96 bytes for big tracks.
        size = e - s
        if size > 200:
            print(f"\n=== bone {idx}: {actual_name} (size {size} bytes) — first 96 bytes ===")
            dump_track(buf, s, s + 96, f"{actual_name} HEAD")
            print(f"\n... middle skipped ...")
            print(f"\n=== {actual_name} — last 64 bytes ===")
            dump_track(buf, e - 64, e, f"{actual_name} TAIL")
        else:
            dump_track(buf, s, e, f"{actual_name} FULL")


if __name__ == "__main__":
    main()
