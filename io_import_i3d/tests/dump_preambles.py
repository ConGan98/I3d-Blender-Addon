"""For chewSource, dump the unk_a preamble float and KF[0]/KF[1] for every
track. We're looking for a pattern: maybe unk_a encodes a bone-bind scalar
(angle? scale?) that combines with the per-KF floats to recover the real
rotation matrix shown in GIANTS Editor.
"""
from __future__ import annotations

import sys
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location(
    "anim_reader", HERE.parents[1] / "importer" / "anim_reader.py"
)
ar = importlib.util.module_from_spec(spec)
sys.modules["anim_reader"] = ar
spec.loader.exec_module(ar)

ANIM = HERE.parents[2] / "cattleAdultAnimations.i3d.anim"


def main():
    doc = ar.parse_anim(ANIM)
    clip = next(c for c in doc.clips if c.name == "chewSource")

    print(f"clip={clip.name}  bones={clip.bone_count}")
    print(f"\n{'idx':>3} {'layout':<11} {'unk_a':>10}  KF[0]  /  KF[1]")
    for tr in clip.bone_tracks:
        unk = tr.preamble_floats[0] if tr.preamble_floats else float('nan')
        kf0 = tr.keyframes[0] if tr.keyframes else None
        kf1 = tr.keyframes[1] if len(tr.keyframes) > 1 else None
        kf0s = (f"loc=({kf0.location[0]:+.4f},{kf0.location[1]:+.4f},{kf0.location[2]:+.4f})"
                f" eul=({kf0.rotation_euler[0]:+.4f},{kf0.rotation_euler[1]:+.4f},{kf0.rotation_euler[2]:+.4f})"
                if kf0 else "<none>")
        kf1s = (f"loc=({kf1.location[0]:+.4f},{kf1.location[1]:+.4f},{kf1.location[2]:+.4f})"
                f" eul=({kf1.rotation_euler[0]:+.4f},{kf1.rotation_euler[1]:+.4f},{kf1.rotation_euler[2]:+.4f})"
                if kf1 else "<none>")
        print(f"{tr.bone_index:>3} {tr.layout:<11} {unk:>10.4f}")
        print(f"     KF0: {kf0s}")
        print(f"     KF1: {kf1s}")


if __name__ == "__main__":
    main()
