"""
Reverse-engineered .i3d.anim parser.

Wire format (FS25, version 9):

  File header:
    [u32 version=9]
    [u32 character_count]                 (always 1 in samples seen)
    per character:
      [u32 name_length][bytes name]       (e.g. "cowCharacter")
      [u32 clip_count]
      per clip:
        [u32 name_length][bytes name]     (e.g. "chewSource")
        ALIGN to next 4-byte boundary
        Clip header (28 bytes):
          [f32 duration_ms]
          [u32 bone_count]                (= 49 in samples)
          [u32 unk1 = 33]
          [u32 frame_count]               (number of authored frames)
          [u32 unk2 = 0]
          [u32 unk3 = 3]
          [u32 unk4 = 0]
        Per-bone tracks (bone_count of them, in scene-DFS order):
          STANDARD layout:
            [u32 magic=3][f32 unk_a]      (8-byte preamble)
            [u32 magic=0][6 floats][f32 time=0]   (32-byte rest-pose KF)
            N times:
              [u32 magic=3][6 floats][f32 time_ms]   (32-byte standard KF)
          SHORT-INTRO layout (used for the root bone — cow_root_skin_jnt):
            [6 floats][f32 time = 33.333]  (24-byte intro KF, no magic)
            N times:
              [u32 magic=3][6 floats][f32 time_ms]
          The last KF's time always equals the clip's duration_ms.

  Each KF's 6 floats are: (vec3 ?, vec3 ?). Empirically:
    - For non-translating bones (e.g. jaw), the FIRST 3 are constant across
      all KFs and look like the bone's bind-pose offset/position.
    - The LAST 3 vary across KFs and look like Euler angles (radians) — for
      heavily-rotated bones the magnitudes can exceed unit-quat range, ruling
      out a quaternion-XYZ interpretation.
  This decoder treats the first 3 as a static "translation/offset" and the
  last 3 as Euler radians (XYZ order). The convention may need refinement
  once played back in Blender against a known animation.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Keyframe:
    time_ms: float
    location: tuple[float, float, float]    # bone-local translation (often constant per bone)
    rotation_euler: tuple[float, float, float]  # radians, applied in XYZ order


@dataclass
class BoneTrack:
    bone_node_id: int                # i3d XML node id of the bone
    keyframes: list[Keyframe] = field(default_factory=list)
    layout: str = "std"              # "std" or "short_intro"
    trailer_floats: tuple[float, ...] = ()


@dataclass
class Clip:
    name: str
    duration_ms: float
    frame_count: int
    bone_count: int
    bone_tracks: list[BoneTrack] = field(default_factory=list)


@dataclass
class AnimDocument:
    version: int
    character_name: str
    clips: list[Clip] = field(default_factory=list)


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def _f32(buf: bytes, off: int) -> float:
    return struct.unpack_from("<f", buf, off)[0]


def _read_string(buf: bytes, off: int) -> tuple[str, int]:
    n = _u32(buf, off)
    return buf[off + 4:off + 4 + n].decode("utf-8", errors="replace"), off + 4 + n


def _read_std_kf(buf: bytes, off: int) -> tuple[Keyframe, int]:
    """Read one 32-byte standard KF: [u32 magic][6 floats][f32 time].
    Returns (kf, next_off).
    """
    # magic = _u32(buf, off)  # usually 3, sometimes 0 — not needed for decode
    tx, ty, tz, rx, ry, rz, t = struct.unpack_from("<7f", buf, off + 4)
    return Keyframe(time_ms=t,
                    location=(tx, ty, tz),
                    rotation_euler=(rx, ry, rz)), off + 32


def _find_next_clip_name_offset(buf: bytes, search_from: int) -> int | None:
    """Find next length-prefixed ASCII clip name starting at >= search_from."""
    end = len(buf)
    j = search_from
    while j <= end - 8:
        n = _u32(buf, j)
        if 4 <= n <= 64 and j + 4 + n <= end:
            payload = buf[j + 4:j + 4 + n]
            if all(
                (65 <= b <= 90) or (97 <= b <= 122)
                or (48 <= b <= 57) or b == 0x5F
                for b in payload
            ) and (65 <= payload[0] <= 90 or 97 <= payload[0] <= 122):
                return j
        j += 1
    return None


def _read_first_track(buf: bytes, off: int, duration_ms: float) -> tuple[BoneTrack, int]:
    """First track per clip uses a short_intro layout: 24-byte intro (6 floats,
    last is the intro KF's time) + N std KFs ending when time==duration_ms +
    28-byte trailer.
    """
    f0, f1, f2, f3, f4, f5 = struct.unpack_from("<6f", buf, off)
    track = BoneTrack(bone_node_id=-1, layout="short_intro")
    track.keyframes.append(Keyframe(
        time_ms=f5, location=(0.0, 0.0, 0.0),
        rotation_euler=(f0, f1, f2),
    ))
    cur = off + 24
    while cur + 32 <= len(buf):
        kf, cur = _read_std_kf(buf, cur)
        track.keyframes.append(kf)
        if abs(kf.time_ms - duration_ms) < 1e-3:
            break
    if cur + 28 <= len(buf):
        track.trailer_floats = struct.unpack_from("<6f", buf, cur + 4)
        cur += 28
    return track, cur


def _looks_like_track_header(buf: bytes, off: int) -> bool:
    """True if the 12 bytes at `off` plausibly begin a std track header
    ``[u32 bone_node_id][u32 kf_count][u32 reserved=0]`` — as opposed to the
    next clip's length-prefixed name or garbage.

    SPARSE clips (e.g. the short 2-frame runFwd*) keyframe FEWER bones than the
    clip header's `bone_count`, so the track list ends early. Without this check
    the reader keeps going past the last real track and reads into the next
    clip's name (whose length word looks like a bone id but whose following
    words don't have reserved==0), desyncing the whole file.
    """
    if off + 12 > len(buf):
        return False
    if _u32(buf, off + 8) != 0:                 # reserved must be 0
        return False
    kf_count = _u32(buf, off + 4)
    if not (0 < kf_count < 100000):             # sane keyframe count
        return False
    bone_node_id = _u32(buf, off)
    return 0 < bone_node_id < 4096              # sane bone id


def _read_track(buf: bytes, off: int) -> tuple[BoneTrack, int]:
    """Tracks 1..N per clip: 12-byte header (bone_node_id, kf_count, reserved)
    + (kf_count-1) std KFs + 28-byte trailer.

    The kf_count field counts the trailer as if it were a KF — so the number
    of fully-formed std KFs is one less than what the header says.
    """
    bone_node_id = _u32(buf, off)
    kf_count = _u32(buf, off + 4)
    n_std = max(0, kf_count - 1)
    track = BoneTrack(bone_node_id=bone_node_id, layout="std")
    cur = off + 12
    for _ in range(n_std):
        kf, cur = _read_std_kf(buf, cur)
        track.keyframes.append(kf)
    if cur + 28 <= len(buf):
        track.trailer_floats = struct.unpack_from("<6f", buf, cur + 4)
        cur += 28
    return track, cur


def parse_anim(path: Path | str) -> AnimDocument:
    path = Path(path)
    buf = path.read_bytes()

    off = 0
    version = _u32(buf, off); off += 4
    char_count = _u32(buf, off); off += 4
    if char_count != 1:
        raise ValueError(f"unsupported character_count {char_count}; expected 1")
    char_name, off = _read_string(buf, off)
    # The character-name string is followed by 4-byte alignment padding before
    # clip_count. Without this align, clip_count reads as garbage (and the whole
    # clip loop derails). Verified against cattleCalfAnimations.i3d.anim: aligned
    # clip_count = 41.
    off = ((off + 3) // 4) * 4
    clip_count = _u32(buf, off); off += 4

    doc = AnimDocument(version=version, character_name=char_name)

    cur = off
    for _ in range(clip_count):
        if cur >= len(buf):
            break
        clip_name, after_name = _read_string(buf, cur)
        aligned = ((after_name + 3) // 4) * 4
        if aligned + 28 > len(buf):
            break
        duration_ms = _f32(buf, aligned)
        bone_count = _u32(buf, aligned + 4)
        frame_count = _u32(buf, aligned + 12)
        body_start = aligned + 28

        clip = Clip(
            name=clip_name, duration_ms=duration_ms,
            frame_count=frame_count, bone_count=bone_count,
        )

        try:
            track, off2 = _read_first_track(buf, body_start, duration_ms)
            clip.bone_tracks.append(track)
            for _ in range(bone_count - 1):
                # Stop at the real end of the track list — sparse clips have
                # fewer tracks than bone_count; the next bytes are the following
                # clip's name, not another track.
                if not _looks_like_track_header(buf, off2):
                    break
                track, off2 = _read_track(buf, off2)
                clip.bone_tracks.append(track)
        except struct.error:
            # End of buffer hit mid-track — keep what we got.
            pass

        doc.clips.append(clip)
        cur = off2

    return doc
