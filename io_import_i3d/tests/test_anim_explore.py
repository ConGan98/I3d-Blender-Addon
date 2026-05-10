"""
First-pass explorer of cattleAdultAnimations.i3d.anim.

Strategy: read what we already know (header + 1 character + clip count +
1 clip name), then dump the next ~512 bytes in multiple representations so
we can spot the per-clip layout.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ANIM = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.anim"
I3D = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d"


def read_u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0], off + 4


def read_string(buf, off):
    """Length-prefixed UTF-8 string: [u32 len][bytes]."""
    n, off = read_u32(buf, off)
    if n > 256:
        raise ValueError(f"String length {n} unreasonably large at offset {off-4}")
    s = buf[off:off + n].decode("utf-8", errors="replace")
    return s, off + n


def hexdump(buf, start, length, width=16):
    end = min(start + length, len(buf))
    out = []
    for i in range(start, end, width):
        row = buf[i:i + width]
        hex_part = " ".join(f"{b:02x}" for b in row)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        out.append(f"  [{i:6d}]  {hex_part:<48s}  {ascii_part}")
    return "\n".join(out)


def annotated_view(buf, start, count, label=""):
    """Show 'count' u32 LE values starting at 'start', annotated as int / float / printable string."""
    print(f"\n--- {label} (annotated u32 view from {start}) ---")
    for i in range(count):
        off = start + i * 4
        if off + 4 > len(buf):
            break
        chunk = buf[off:off + 4]
        u = struct.unpack("<I", chunk)[0]
        f = struct.unpack("<f", chunk)[0]
        # Try as 4 ASCII chars
        s = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        # Annotate small ints, plausible floats
        notes = []
        if u < 1000:
            notes.append(f"smallint")
        if 0.0001 < abs(f) < 10000.0 and not (f != f):
            notes.append(f"float~{f:.4g}")
        n = " ".join(notes)
        print(f"  [{off:6d}]  hex={chunk.hex()}  u32={u:11d}  ascii={s!r}  {n}")


def main():
    if not ANIM.exists():
        print(f"SKIP: {ANIM} not found")
        return 0
    buf = ANIM.read_bytes()
    print(f"file: {ANIM.name} ({len(buf):,} bytes)")

    off = 0
    version, off = read_u32(buf, off)
    char_count, off = read_u32(buf, off)
    print(f"version={version}, character_count={char_count}")

    if char_count == 0 or char_count > 32:
        print(f"unexpected char_count {char_count}")
        return 1

    # Parse character header
    char_name, off = read_string(buf, off)
    print(f"character: {char_name!r}, name ends at offset {off}")

    clip_count, off = read_u32(buf, off)
    print(f"clip_count={clip_count}, after read offset = {off}")

    clip_name, off = read_string(buf, off)
    print(f"first clip name: {clip_name!r}, ends at offset {off}")

    # Now we don't know the layout. Print the next 256 bytes as
    # hex + ascii + annotated u32 to see what fields follow.
    print("\n--- raw hex from offset {} ---".format(off))
    print(hexdump(buf, off, 256))

    annotated_view(buf, off, 64, label=f"u32 view from {off}")

    # Scan the WHOLE file for length-prefixed ASCII strings — those are
    # almost certainly the 41 clip names. We expect 41 of them, spaced
    # at the start of each clip's data block.
    print("\n--- length-prefixed ASCII strings in the file (likely clip names) ---")
    found = []
    i = 0
    while i < len(buf) - 8:
        n = struct.unpack_from("<I", buf, i)[0]
        # Real clip names are 5+ chars (e.g. "chewSource", "idle", "walk").
        if 4 <= n <= 64 and i + 4 + n <= len(buf):
            payload = buf[i + 4:i + 4 + n]
            if all((65 <= b <= 90) or (97 <= b <= 122) or (48 <= b <= 57) or b == 0x5F for b in payload):
                s = payload.decode('ascii')
                # First char must be a letter (not a digit) to filter random noise
                if 65 <= payload[0] <= 90 or 97 <= payload[0] <= 122:
                    found.append((i, s))
                    i += 4 + n
                    continue
        i += 1
    print(f"  found {len(found)} candidate strings")
    for ofs, s in found[:60]:
        print(f"  [{ofs:8d}]  len={len(s):3d}  {s!r}")
    if len(found) > 60:
        print(f"  ... +{len(found) - 60} more")
    # Spacing between consecutive found strings (clip sizes)
    if len(found) >= 2:
        print("\n--- spacing between consecutive candidate strings (clip sizes) ---")
        for i in range(1, min(len(found), 12)):
            prev = found[i - 1]
            cur = found[i]
            print(f"  clip {i-1} {prev[1]!r}: starts at {prev[0]}, "
                  f"next at {cur[0]}, spacing={cur[0]-prev[0]} bytes")

    # Verify: the 49 bone names from the i3d XML should appear in the file.
    print("\n--- check: do all 49 i3d bone names appear in the .anim file? ---")
    if I3D.exists():
        import xml.etree.ElementTree as ET
        root = ET.parse(I3D).getroot()
        bone_names = []
        scene = root.find("Scene")
        def collect(el):
            if el.tag == "TransformGroup" and "_skin_jnt" in el.attrib.get("name", ""):
                bone_names.append(el.attrib["name"])
            for c in el:
                collect(c)
        collect(scene)
        present = sum(1 for n in bone_names if n.encode("utf-8") in buf)
        print(f"  bones in i3d XML: {len(bone_names)}, present in .anim: {present}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
