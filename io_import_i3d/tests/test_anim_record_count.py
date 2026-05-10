"""
Count 0xbf0f markers across the chewSource clip and compute spacings, so
we can figure out whether records are uniform or variable-length, and
whether they group into per-bone tracks.
"""
from __future__ import annotations

import struct
from pathlib import Path

ANIM = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.anim"


def main():
    buf = ANIM.read_bytes()
    print(f"File size: {len(buf):,}")

    # Header
    off = 0
    version = struct.unpack_from("<I", buf, off)[0]; off += 4
    char_count = struct.unpack_from("<I", buf, off)[0]; off += 4
    char_name_len = struct.unpack_from("<I", buf, off)[0]; off += 4
    char_name = buf[off:off + char_name_len].decode('utf-8'); off += char_name_len
    clip_count = struct.unpack_from("<I", buf, off)[0]; off += 4
    print(f"version={version} char={char_name!r} clip_count={clip_count}")

    # Find clip 0: chewSource
    clip0_name_off = off  # 28
    n = struct.unpack_from("<I", buf, off)[0]
    print(f"clip 0 name length: {n}")
    name = buf[off + 4:off + 4 + n].decode('utf-8')
    print(f"clip 0 name: {name!r}")
    clip0_data_start = off + 4 + n  # 42 for chewSource
    print(f"clip 0 data start: {clip0_data_start}")

    # Align to 4-byte boundary from file start
    aligned_start = ((clip0_data_start + 3) // 4) * 4
    pad = aligned_start - clip0_data_start
    print(f"alignment pad: {pad} bytes -> aligned start = {aligned_start}")

    # Find clip 1's name length offset to bound clip 0
    # Just scan forward looking for next clip name (length-prefixed ASCII).
    next_clip_off = None
    for j in range(clip0_data_start + 100, len(buf) - 8):
        n2 = struct.unpack_from("<I", buf, j)[0]
        if 4 <= n2 <= 64 and j + 4 + n2 <= len(buf):
            payload = buf[j + 4:j + 4 + n2]
            if all((65 <= b <= 90) or (97 <= b <= 122) or (48 <= b <= 57) or b == 0x5F for b in payload):
                if 65 <= payload[0] <= 90 or 97 <= payload[0] <= 122:
                    next_clip_off = j
                    break
    print(f"next clip name length-prefix at: {next_clip_off}")
    clip0_data_end = next_clip_off  # exclusive
    print(f"clip 0 data: {clip0_data_start}..{clip0_data_end} ({clip0_data_end - clip0_data_start} bytes)")

    # Parse the 28-byte header (after alignment).
    h_off = aligned_start
    h_duration = struct.unpack_from("<f", buf, h_off)[0]
    h_bone_count = struct.unpack_from("<I", buf, h_off + 4)[0]
    h_unk1 = struct.unpack_from("<I", buf, h_off + 8)[0]
    h_frame_count = struct.unpack_from("<I", buf, h_off + 12)[0]
    h_unk2 = struct.unpack_from("<I", buf, h_off + 16)[0]
    h_unk3 = struct.unpack_from("<I", buf, h_off + 20)[0]
    h_unk4 = struct.unpack_from("<I", buf, h_off + 24)[0]
    print(f"\nClip header (28 bytes from {h_off}):")
    print(f"  duration_f32 = {h_duration}")
    print(f"  bone_count u32 = {h_bone_count}")
    print(f"  unk1 u32 = {h_unk1}  (= 0x{h_unk1:x})")
    print(f"  frame_count u32 = {h_frame_count}")
    print(f"  unk2 u32 = {h_unk2}")
    print(f"  unk3 u32 = {h_unk3}  (= 0x{h_unk3:x})")
    print(f"  unk4 u32 = {h_unk4}")

    body_start = h_off + 28
    body_end = clip0_data_end
    body_size = body_end - body_start
    print(f"\nBody: {body_start}..{body_end} = {body_size} bytes")
    print(f"  body_size / 32 = {body_size / 32}")
    print(f"  body_size / bones = {body_size / h_bone_count:.2f} bytes/bone")
    print(f"  body_size / (bones*frames) = {body_size / (h_bone_count * h_frame_count):.4f}")

    # Find all 0xbf0f markers in the body.
    bf0f_positions = []
    i = body_start
    while i < body_end - 1:
        if buf[i] == 0x0f and buf[i + 1] == 0xbf:
            bf0f_positions.append(i)
        i += 1
    print(f"\n0xbf0f marker count in body: {len(bf0f_positions)}")
    if len(bf0f_positions) >= 2:
        spacings = [bf0f_positions[i + 1] - bf0f_positions[i] for i in range(len(bf0f_positions) - 1)]
        from collections import Counter
        c = Counter(spacings)
        print(f"  spacing distribution (top 8): {c.most_common(8)}")
        print(f"  first 5 marker positions: {bf0f_positions[:5]}")
        print(f"  last 5 marker positions: {bf0f_positions[-5:]}")

    # Find all repeat occurrences of the duration constant float (8995.0 = 0x460ca000)
    # to see if it appears as a per-bone track marker.
    duration_bytes = struct.pack("<f", h_duration)
    print(f"\nSearching for the duration float ({h_duration}) as a marker (bytes={duration_bytes.hex()}):")
    dur_positions = []
    i = body_start
    while i <= body_end - 4:
        if buf[i:i + 4] == duration_bytes:
            dur_positions.append(i)
        i += 1
    print(f"  occurrences: {len(dur_positions)}")
    if len(dur_positions) >= 2:
        spacings = [dur_positions[i + 1] - dur_positions[i] for i in range(len(dur_positions) - 1)]
        print(f"  first spacings: {spacings[:10]}")
        print(f"  last 5 positions: {dur_positions[-5:]}")
    if len(dur_positions) > 0:
        print(f"  first occurrence relative to body_start: {dur_positions[0] - body_start}")


if __name__ == "__main__":
    main()
