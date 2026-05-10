"""
Partition chewSource body by the 49 occurrences of the duration float
(0x460ca000 = 9000.0) and analyse each per-bone track.

Hypothesis: each bone has its own track within the clip body. The track
either starts or ends with the clip-duration float as a sentinel.
"""
from __future__ import annotations

import struct
from pathlib import Path

ANIM = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.anim"
I3D = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d"

BONE_NAMES_DFS = []  # populated below

def main():
    # Pull bone names in DFS order from the i3d XML.
    import xml.etree.ElementTree as ET
    root = ET.parse(I3D).getroot()
    scene = root.find("Scene")

    def walk(el):
        if el.tag == "TransformGroup" and "_skin_jnt" in el.attrib.get("name", ""):
            BONE_NAMES_DFS.append(el.attrib["name"])
        for c in el:
            walk(c)
    walk(scene)
    print(f"Bone count from i3d XML: {len(BONE_NAMES_DFS)}")

    buf = ANIM.read_bytes()

    # Header parse
    off = 0
    version = struct.unpack_from("<I", buf, off)[0]; off += 4
    char_count = struct.unpack_from("<I", buf, off)[0]; off += 4
    char_name_len = struct.unpack_from("<I", buf, off)[0]; off += 4
    off += char_name_len
    clip_count = struct.unpack_from("<I", buf, off)[0]; off += 4

    # First clip
    n = struct.unpack_from("<I", buf, off)[0]
    name = buf[off + 4:off + 4 + n].decode()
    clip0_data_start = off + 4 + n
    aligned = ((clip0_data_start + 3) // 4) * 4
    duration = struct.unpack_from("<f", buf, aligned)[0]
    bone_count = struct.unpack_from("<I", buf, aligned + 4)[0]
    frame_count = struct.unpack_from("<I", buf, aligned + 12)[0]
    body_start = aligned + 28

    # Find clip 1 to bound clip 0.
    next_clip_off = None
    for j in range(clip0_data_start + 100, len(buf) - 8):
        n2 = struct.unpack_from("<I", buf, j)[0]
        if 4 <= n2 <= 64 and j + 4 + n2 <= len(buf):
            payload = buf[j + 4:j + 4 + n2]
            if all((65 <= b <= 90) or (97 <= b <= 122) or (48 <= b <= 57) or b == 0x5F for b in payload):
                if 65 <= payload[0] <= 90 or 97 <= payload[0] <= 122:
                    next_clip_off = j; break
    body_end = next_clip_off

    print(f"Clip {name!r}: duration={duration} bones={bone_count} frames={frame_count}")
    print(f"Body: {body_start}..{body_end} = {body_end - body_start} bytes")

    # Find duration markers
    duration_bytes = struct.pack("<f", duration)
    markers = []
    i = body_start
    while i <= body_end - 4:
        if buf[i:i + 4] == duration_bytes:
            markers.append(i)
        i += 1
    print(f"Duration markers: {len(markers)}")

    # Two hypotheses for track boundaries:
    # A: track i = [body_start ... markers[0]] for i=0, then [markers[i-1]+4 ... markers[i]] for i>=1.
    #    (markers are at END of each track, last = end of body marker, but here last marker at 227836 with 32 bytes after)
    # B: tracks start at markers, so track i = [markers[i] ... markers[i+1]] (last extends to body_end).

    # Check: track 0 in A would be body_start..markers[0] = 6964 bytes. Then markers spacing gives subsequent.
    # Check: track 0 in B starts AT markers[0], skipping body_start..markers[0] (= 6964 bytes pre-data).
    # Likely A — let's print both and let pattern speak.

    print("\n--- Hypothesis A: markers are END of each track ---")
    print("  bone | track_size | first16 | last16 | bf0f_count")
    n_tracks_A = len(markers)
    # Track i bounds: from prev_end (body_start or markers[i-1]+4) to markers[i]+4 (include marker).
    bounds = []
    prev_end = body_start
    for m in markers:
        bounds.append((prev_end, m + 4))
        prev_end = m + 4
    # Also include trailing bytes after last marker (if any).
    if prev_end < body_end:
        bounds.append((prev_end, body_end))
    for bi, (s, e) in enumerate(bounds[:60]):
        chunk = buf[s:e]
        # Count bf0f markers in this chunk
        bf0f = sum(1 for j in range(len(chunk) - 1) if chunk[j] == 0x0f and chunk[j + 1] == 0xbf)
        first16 = chunk[:16].hex()
        last16 = chunk[-16:].hex()
        bone_name = BONE_NAMES_DFS[bi] if bi < len(BONE_NAMES_DFS) else "<extra>"
        print(f"  [{bi:3d}] {bone_name:<32s}  size={len(chunk):6d}  bf0f={bf0f:4d}  first16={first16}  last16={last16}")

    print(f"\nTotal tracks under hypothesis A: {len(bounds)}")
    print(f"Total bones: {len(BONE_NAMES_DFS)}")


if __name__ == "__main__":
    main()
