"""Multi-target encoding fit. We have GIANTS-displayed jaw rotation matrices
at three time points; the .anim has jaw KFs at corresponding times. Try every
plausible 6-float encoding and score it against all three matrices at once.
"""
from __future__ import annotations

import math
import sys
import importlib.util
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


def euler_intrinsic(angles, order):
    """angles is dict like {'X': 1.0, 'Y': 0.5, 'Z': -0.3}; order applies left-to-right."""
    R = {'X': rot_x, 'Y': rot_y, 'Z': rot_z}
    M = np.eye(3)
    for ax in order:
        M = M @ R[ax](angles[ax])
    return M


def quat_to_mat(w, x, y, z):
    n = math.sqrt(w*w + x*x + y*y + z*z)
    if n < 1e-9:
        return None
    w, x, y, z = w/n, x/n, y/n, z/n
    return np.array([
        [1 - 2*(y*y+z*z), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x*x+z*z), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x*x+y*y)],
    ])


def quat_xyz_implicit_w(v):
    n2 = float(v @ v)
    if n2 > 1.0:
        return None
    return quat_to_mat(math.sqrt(1 - n2), v[0], v[1], v[2])


def axis_angle_to_mat(v):
    a = float(np.linalg.norm(v))
    if a < 1e-9:
        return np.eye(3)
    ax = v / a
    K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    return np.eye(3) + math.sin(a)*K + (1-math.cos(a))*(K @ K)


def gibbs_to_mat(v):
    """v = axis * tan(θ/2). θ < π."""
    n2 = float(v @ v)
    cos_half = 1.0 / math.sqrt(1 + n2)
    return quat_to_mat(cos_half, v[0]*cos_half, v[1]*cos_half, v[2]*cos_half)


def mrp_to_mat(v):
    """v = axis * tan(θ/4). θ < 2π."""
    n2 = float(v @ v)
    w = (1 - n2) / (1 + n2)
    s = 2.0 / (1 + n2)
    return quat_to_mat(w, v[0]*s, v[1]*s, v[2]*s)


def all_encodings(raw):
    """Generate (name, matrix-or-None) candidates from 6 raw floats."""
    last3 = np.array(raw[3:])
    first3 = np.array(raw[:3])
    out = []
    for name, v in (("last3", last3), ("first3", first3)):
        for order in ('XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'):
            angles = {'X': float(v[0]), 'Y': float(v[1]), 'Z': float(v[2])}
            out.append((f"{name} euler {order} (rad)", euler_intrinsic(angles, order)))
            angles_rev = {'X': float(v[2]), 'Y': float(v[1]), 'Z': float(v[0])}
            out.append((f"{name} euler {order} (rev rad)", euler_intrinsic(angles_rev, order)))
        out.append((f"{name} axis*angle", axis_angle_to_mat(v)))
        out.append((f"{name} axis*angle (neg)", axis_angle_to_mat(-v)))
        m = quat_xyz_implicit_w(v)
        if m is not None: out.append((f"{name} quat-xyz +W", m))
        out.append((f"{name} Gibbs", gibbs_to_mat(v)))
        out.append((f"{name} MRP", mrp_to_mat(v)))
    # Quaternion variants from 4 of 6 floats
    for label, w_idx, x_idx, y_idx, z_idx in [
        ("first4 wxyz", 0, 1, 2, 3),
        ("first4 xyzw", 3, 0, 1, 2),
        ("last4 wxyz", 2, 3, 4, 5),
        ("last4 xyzw", 5, 2, 3, 4),
    ]:
        m = quat_to_mat(raw[w_idx], raw[x_idx], raw[y_idx], raw[z_idx])
        if m is not None: out.append((f"quat {label}", m))
    return out


def main():
    doc = ar.parse_anim(HERE.parents[2] / "cattleAdultAnimations.i3d.anim")
    clip = next(c for c in doc.clips if c.name == "chewSource")
    jaw = next(t for t in clip.bone_tracks if t.bone_node_id == 22)

    targets = [
        (58, math.radians(92.906), math.radians(-29.124), math.radians(-93.309)),
        (3999, math.radians(92.498), math.radians(-23.856), math.radians(-92.04)),
        (8960, math.radians(93.202), math.radians(-32.234), math.radians(-92.854)),
    ]

    test_points = []
    for tms, ax, ay, az in targets:
        target_mat = euler_intrinsic({'X': ax, 'Y': ay, 'Z': az}, 'ZYX')
        # Find KF closest in time
        kf = min(jaw.keyframes, key=lambda k: abs(k.time_ms - tms))
        raw = (kf.location[0], kf.location[1], kf.location[2],
               kf.rotation_euler[0], kf.rotation_euler[1], kf.rotation_euler[2])
        test_points.append((tms, kf.time_ms, raw, target_mat))
        print(f"t={tms}ms (closest KF @ {kf.time_ms:.1f}ms): raw={raw}")

    # Score every encoding by sum of Frobenius distances across all 3 points
    print(f"\nScoring encodings across all 3 time points (lower is better):")
    encoding_names = [name for name, _ in all_encodings(test_points[0][2])]
    scores = {name: 0.0 for name in encoding_names}
    for tms, kf_t, raw, target in test_points:
        for name, M in all_encodings(raw):
            if M is None:
                scores[name] += 99.0
            else:
                scores[name] += float(np.linalg.norm(M - target))

    ranked = sorted(scores.items(), key=lambda kv: kv[1])
    for name, score in ranked[:15]:
        print(f"  {score:8.3f}  {name}")

    print(f"\n--- Best encoding: {ranked[0][0]} ---")
    print(f"Per-target distance:")
    best_name = ranked[0][0]
    for tms, kf_t, raw, target in test_points:
        for name, M in all_encodings(raw):
            if name == best_name:
                d = float(np.linalg.norm(M - target)) if M is not None else float('nan')
                print(f"  t={tms}ms: distance {d:.3f}")
                break


if __name__ == "__main__":
    main()
