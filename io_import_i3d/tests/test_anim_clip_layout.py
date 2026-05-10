"""
Inspect the start of each clip's data block + the bytes around clip
boundaries, looking for a consistent clip-header pattern.
"""
from __future__ import annotations

import struct
from pathlib import Path

ANIM = Path(__file__).resolve().parents[2] / "cattleAdultAnimations.i3d.anim"


def main():
    buf = ANIM.read_bytes()
    # Walk the known header.
    # version, char_count, char_name, clip_count, then 41 clips.
    off = 0
    version = struct.unpack_from("<I", buf, off)[0]; off += 4
    char_count = struct.unpack_from("<I", buf, off)[0]; off += 4
    char_name_len = struct.unpack_from("<I", buf, off)[0]; off += 4
    char_name = buf[off:off + char_name_len].decode('utf-8'); off += char_name_len
    clip_count = struct.unpack_from("<I", buf, off)[0]; off += 4
    print(f"version={version}, char={char_name!r}, clip_count={clip_count}, post-header offset={off}")

    # Find all clip name positions (length-prefix offset). We re-detect them
    # the same way as the explorer but only keep length>=5 clip-name-looking ones.
    clips = []
    while True:
        # Read name length + name
        if off + 4 > len(buf):
            break
        n = struct.unpack_from("<I", buf, off)[0]
        if not (4 <= n <= 64) or off + 4 + n > len(buf):
            break
        payload = buf[off + 4:off + 4 + n]
        if not all((65 <= b <= 90) or (97 <= b <= 122) or (48 <= b <= 57) or b == 0x5F for b in payload):
            break
        if not (65 <= payload[0] <= 90 or 97 <= payload[0] <= 122):
            break
        clips.append((off, payload.decode('ascii')))
        # Skip to NEXT clip name. We don't know clip-data size yet, so we
        # scan forward looking for the next length-prefixed ASCII string.
        scan_from = off + 4 + n
        next_off = None
        for j in range(scan_from, len(buf) - 8):
            n2 = struct.unpack_from("<I", buf, j)[0]
            if 4 <= n2 <= 64 and j + 4 + n2 <= len(buf):
                p2 = buf[j + 4:j + 4 + n2]
                if all((65 <= b <= 90) or (97 <= b <= 122) or (48 <= b <= 57) or b == 0x5F for b in p2):
                    if 65 <= p2[0] <= 90 or 97 <= p2[0] <= 122:
                        next_off = j
                        break
        if next_off is None:
            # Last clip — extends to end of file
            break
        off = next_off

    print(f"\n  found {len(clips)} clips")
    for i, (o, nm) in enumerate(clips[:5]):
        # Data starts after the [u32 len][name] block.
        name_len = len(nm)
        data_start = o + 4 + name_len
        # Data ends just before the next clip's length prefix (or EOF for last).
        if i + 1 < len(clips):
            data_end = clips[i + 1][0]
        else:
            data_end = len(buf)
        data_len = data_end - data_start
        print(f"\n=== clip {i}: {nm!r} (data {data_start}..{data_end}, {data_len} bytes) ===")
        print("  -- first 64 bytes of data:")
        chunk = buf[data_start:data_start + 64]
        for k in range(0, len(chunk), 16):
            row = chunk[k:k + 16]
            hex_part = " ".join(f"{b:02x}" for b in row)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
            print(f"    [{data_start + k:8d}] {hex_part:<48s}  {ascii_part}")
        # Annotate as u32 + f32
        print("  -- first 16 u32/f32:")
        for k in range(0, min(64, len(chunk)), 4):
            u = struct.unpack_from("<I", chunk, k)[0]
            f = struct.unpack_from("<f", chunk, k)[0]
            print(f"    [+{k:3d}] hex={chunk[k:k+4].hex()}  u32={u:11d}  f32={f:.6g}")

    # Also dump the LAST 32 bytes of clip 0 (right before clip 1 length-prefix)
    if len(clips) >= 2:
        clip1_start = clips[1][0]
        end_chunk = buf[clip1_start - 32:clip1_start]
        print(f"\n--- last 32 bytes of clip 0 (before clip 1 length-prefix at {clip1_start}) ---")
        for k in range(0, len(end_chunk), 16):
            row = end_chunk[k:k + 16]
            hex_part = " ".join(f"{b:02x}" for b in row)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
            print(f"  [{clip1_start - 32 + k:8d}] {hex_part:<48s}  {ascii_part}")


if __name__ == "__main__":
    main()
