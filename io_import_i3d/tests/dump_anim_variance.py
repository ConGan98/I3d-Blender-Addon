"""Dump per-bone-track value ranges for one clip.

Run from Blender's Text Editor (or any Python) — does not need bpy.
Tells us which track has the largest rotation variance. If chewSource's
loudest track is not the jaw, our DFS index ↔ bone mapping is wrong.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the addon importable when running outside Blender.
HERE = Path(__file__).resolve()
# Load anim_reader.py directly to avoid triggering the addon's bpy imports.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "anim_reader", HERE.parents[1] / "importer" / "anim_reader.py"
)
ar = importlib.util.module_from_spec(_spec)
sys.modules["anim_reader"] = ar
_spec.loader.exec_module(ar)

ANIM = HERE.parents[2] / "cattleAdultAnimations.i3d.anim"
CLIP = "chewSource"


def main():
    doc = ar.parse_anim(ANIM)
    clip = next((c for c in doc.clips if c.name == CLIP), None)
    if clip is None:
        print(f"clip {CLIP!r} not found; have:",
              [c.name for c in doc.clips[:5]], "...")
        return

    print(f"clip={clip.name}  bones={clip.bone_count}  "
          f"tracks={len(clip.bone_tracks)}  duration={clip.duration_ms:.1f}ms")

    rows = []
    for tr in clip.bone_tracks:
        if not tr.keyframes:
            continue
        locs = [kf.location for kf in tr.keyframes]
        rots = [kf.rotation_euler for kf in tr.keyframes]
        loc_range = tuple(max(v[i] for v in locs) - min(v[i] for v in locs) for i in range(3))
        rot_range = tuple(max(v[i] for v in rots) - min(v[i] for v in rots) for i in range(3))
        rot_total = sum(rot_range)
        loc_total = sum(loc_range)
        rows.append((tr.bone_node_id, len(tr.keyframes), tr.layout,
                     loc_range, rot_range, loc_total, rot_total))

    rows.sort(key=lambda r: r[6], reverse=True)
    print(f"\nTop 10 tracks by rotation variance ({CLIP}):")
    print(f"{'nid':>3}  {'nkf':>4}  layout       "
          f"{'loc_range':>30}  {'rot_range':>30}  rot_total")
    for r in rows[:10]:
        idx, nkf, layout, lr, rr, lt, rt = r
        print(f"{idx:>3}  {nkf:>4}  {layout:<11}  "
              f"({lr[0]:8.4f},{lr[1]:8.4f},{lr[2]:8.4f})  "
              f"({rr[0]:8.4f},{rr[1]:8.4f},{rr[2]:8.4f})  {rt:8.4f}")

    print(f"\nBottom 5 tracks by rotation variance:")
    for r in rows[-5:]:
        idx, nkf, layout, lr, rr, lt, rt = r
        print(f"{idx:>3}  {nkf:>4}  {layout:<11}  rot_total={rt:.5f}")


if __name__ == "__main__":
    main()
