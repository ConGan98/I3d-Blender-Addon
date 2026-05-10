"""Thorough brute force: try every encoding × every axis permutation ×
every sign flip × every conjugation transform. If the .anim is using a
standard rotation parameterisation in some non-canonical frame, this finds
the conversion.
"""
from __future__ import annotations

import math
import sys
import importlib.util
import itertools
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location(
    "anim_reader", HERE.parents[1] / "importer" / "anim_reader.py"
)
ar = importlib.util.module_from_spec(spec)
sys.modules["anim_reader"] = ar
spec.loader.exec_module(ar)


def rot_x(a): c, s = math.cos(a), math.sin(a); return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
def rot_y(a): c, s = math.cos(a), math.sin(a); return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
def rot_z(a): c, s = math.cos(a), math.sin(a); return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def euler_intrinsic(a, b, c, order):
    R = {'X': rot_x, 'Y': rot_y, 'Z': rot_z}
    angles = {'X': a, 'Y': b, 'Z': c}
    M = np.eye(3)
    for ax in order:
        M = M @ R[ax](angles[ax])
    return M


def axis_angle_to_mat(v):
    a = float(np.linalg.norm(v))
    if a < 1e-9:
        return np.eye(3)
    ax = v / a
    K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    return np.eye(3) + math.sin(a)*K + (1-math.cos(a))*(K @ K)


# All 6 axis permutations as 3x3 matrices
PERMS = []
for perm in itertools.permutations([0, 1, 2]):
    P = np.zeros((3, 3))
    for i, j in enumerate(perm):
        P[i, j] = 1
    PERMS.append((perm, P))

# All 8 sign-flip diagonal matrices (must have det=1 to be a proper rotation)
SIGNS = []
for s in itertools.product([1, -1], repeat=3):
    if s[0]*s[1]*s[2] == 1:
        SIGNS.append(np.diag(s))


def all_base_encodings(raw):
    """Generate base candidate matrices from 6 raw floats."""
    last3 = np.array(raw[3:])
    out = []
    for order in ('XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'):
        out.append((f"euler {order}", euler_intrinsic(last3[0], last3[1], last3[2], order)))
    out.append(("axis*angle", axis_angle_to_mat(last3)))
    out.append(("axis*angle (neg)", axis_angle_to_mat(-last3)))
    return out


def conjugate(M, P, S):
    """Apply axis permutation and sign flip via similarity transform."""
    T = P @ S
    return T @ M @ np.linalg.inv(T)


def main():
    doc = ar.parse_anim(HERE.parents[2] / "cattleAdultAnimations.i3d.anim")
    clip = next(c for c in doc.clips if c.name == "chewSource")
    jaw = next(t for t in clip.bone_tracks if t.bone_node_id == 22)

    targets = [
        (58, math.radians(92.906), math.radians(-29.124), math.radians(-93.309)),
        (3999, math.radians(92.498), math.radians(-23.856), math.radians(-92.04)),
        (8960, math.radians(93.202), math.radians(-32.234), math.radians(-92.854)),
    ]

    test_data = []
    for tms, ax, ay, az in targets:
        target_mat = euler_intrinsic(ax, ay, az, 'ZYX')
        kf = min(jaw.keyframes, key=lambda k: abs(k.time_ms - tms))
        raw = (kf.location[0], kf.location[1], kf.location[2],
               kf.rotation_euler[0], kf.rotation_euler[1], kf.rotation_euler[2])
        test_data.append((tms, raw, target_mat))

    best = []
    for enc_name, _ in all_base_encodings(test_data[0][1]):
        for perm, P in PERMS:
            for S in SIGNS:
                # Score across all test points
                total = 0.0
                for tms, raw, target in test_data:
                    encs = dict(all_base_encodings(raw))
                    M = encs[enc_name]
                    M_pred = conjugate(M, P, S)
                    total += float(np.linalg.norm(M_pred - target))
                desc = f"{enc_name}  perm={perm}  signs=({int(S[0,0])},{int(S[1,1])},{int(S[2,2])})"
                best.append((total, desc))

    best.sort()
    print(f"Top 5 (encoding × permutation × sign-flip), summed over 3 time points:")
    for score, desc in best[:5]:
        print(f"  {score:7.3f}  {desc}")

    # For the best candidate, show per-target breakdown
    print(f"\n--- Best: {best[0][1]} ---  total {best[0][0]:.3f}")
    best_desc = best[0][1]
    enc_name = best_desc.split("  ")[0]
    perm_str = best_desc.split("perm=")[1].split(")")[0] + ")"
    perm = eval(perm_str)
    P = np.zeros((3, 3))
    for i, j in enumerate(perm):
        P[i, j] = 1
    sign_str = best_desc.split("signs=")[1]
    signs = eval(sign_str)
    S = np.diag(signs)

    print(f"\nPer-target diagnostic:")
    for tms, raw, target in test_data:
        encs = dict(all_base_encodings(raw))
        M = encs[enc_name]
        M_pred = conjugate(M, P, S)
        d = float(np.linalg.norm(M_pred - target))
        print(f"\n  t={tms}ms  distance={d:.3f}")
        print(f"    raw last3 = {tuple(round(x,4) for x in raw[3:])}")
        print(f"    M_pred:")
        for row in M_pred:
            print(f"      {tuple(round(x,3) for x in row)}")
        print(f"    target:")
        for row in target:
            print(f"      {tuple(round(x,3) for x in row)}")


if __name__ == "__main__":
    main()
