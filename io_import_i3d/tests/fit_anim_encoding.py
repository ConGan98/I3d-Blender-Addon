"""Brute-force fit the .anim KF encoding to a known GIANTS-displayed pose.

GIANTS Editor showed cow_jaw_skin_jnt at t=745ms with:
  Translate (X, Y, Z) = (0, -0.012, 0.178)        [bind translation, static]
  Rotate    (X, Y, Z) = (93.016, -39, -92.94)°    [animated, ZYX intrinsic]

Our anim_reader gives jaw KF[1] @ t=733ms:
  6 floats = (0.118, 0.061, 0.209, 0.49, -0.47, -0.44)

Try every plausible interpretation; report which (if any) produces a rotation
matrix close to GIANTS' displayed matrix.
"""
from __future__ import annotations

import math
import numpy as np


def rot_x(a): c,s=math.cos(a),math.sin(a); return np.array([[1,0,0],[0,c,-s],[0,s,c]])
def rot_y(a): c,s=math.cos(a),math.sin(a); return np.array([[c,0,s],[0,1,0],[-s,0,c]])
def rot_z(a): c,s=math.cos(a),math.sin(a); return np.array([[c,-s,0],[s,c,0],[0,0,1]])


def euler_to_mat(a, b, c, order):
    """Intrinsic euler. order='ZYX' means Rz * Ry * Rx applied to a column vector."""
    R = {'X': rot_x, 'Y': rot_y, 'Z': rot_z}
    angles = {'X': a if 'X' in order else 0,
              'Y': b if 'Y' in order else 0,
              'Z': c if 'Z' in order else 0}
    # Map order's positions to angles in (a,b,c) by axis name
    axis_to_angle = {}
    for ax, val in zip(order, (a, b, c)):
        axis_to_angle[ax] = val
    M = np.eye(3)
    # intrinsic = leftmost-axis rotation outermost
    for ax in order:
        M = M @ R[ax](axis_to_angle[ax])
    return M


def axis_angle_to_mat(v):
    """Treat v as axis*angle; |v| = angle."""
    a = float(np.linalg.norm(v))
    if a < 1e-9:
        return np.eye(3)
    ax = v / a
    K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    return np.eye(3) + math.sin(a)*K + (1-math.cos(a))*(K@K)


def quat_xyz_to_mat(v):
    """Treat v as quat XYZ; W = sqrt(1 - |v|^2) (must be ≤ 1)."""
    n2 = float(np.dot(v, v))
    if n2 > 1.0:
        return None
    w = math.sqrt(1 - n2)
    x, y, z = v
    return np.array([
        [1 - 2*(y*y+z*z), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x*x+z*z), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x*x+y*y)],
    ])


def gibbs_to_mat(v):
    """Gibbs vector: v = axis*tan(angle/2). Equivalent to standard Rodrigues."""
    n2 = float(np.dot(v, v))
    w = 1.0 / math.sqrt(1 + n2)
    s = w  # since v = (xyz)/cos(θ/2) form gives quat (w, w*v[0], w*v[1], w*v[2])
    x, y, z = v[0]*s, v[1]*s, v[2]*s
    return np.array([
        [1 - 2*(y*y+z*z), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x*x+z*z), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x*x+y*y)],
    ])


def mrp_to_mat(v):
    """Modified Rodrigues Parameter: v = axis*tan(angle/4)."""
    n2 = float(np.dot(v, v))
    w = (1 - n2) / (1 + n2)
    s = 2.0 / (1 + n2)
    x, y, z = v[0]*s, v[1]*s, v[2]*s
    return np.array([
        [1 - 2*(y*y+z*z), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x*x+z*z), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x*x+y*y)],
    ])


def diff_score(M_a, M_b):
    """Frobenius norm of difference. 0 = perfect match."""
    return float(np.linalg.norm(M_a - M_b))


def main():
    # Ground truth from GIANTS Editor
    target = euler_to_mat(math.radians(93.016), math.radians(-39),
                          math.radians(-92.94), 'ZYX')
    print("Target matrix (GIANTS at t=745ms):")
    print(target.round(3))

    # Pull the actual jaw track (bone_node_id=22) from chewSource via the
    # corrected parser, and use its first KF as the test case.
    import importlib.util, sys
    from pathlib import Path
    HERE = Path(__file__).resolve()
    spec = importlib.util.spec_from_file_location(
        "anim_reader", HERE.parents[1] / "importer" / "anim_reader.py"
    )
    ar = importlib.util.module_from_spec(spec)
    sys.modules["anim_reader"] = ar
    spec.loader.exec_module(ar)
    doc = ar.parse_anim(HERE.parents[2] / "cattleAdultAnimations.i3d.anim")
    clip = next(c for c in doc.clips if c.name == "chewSource")
    jaw = next(t for t in clip.bone_tracks if t.bone_node_id == 22)
    kf = jaw.keyframes[0]
    raw = (kf.location[0], kf.location[1], kf.location[2],
           kf.rotation_euler[0], kf.rotation_euler[1], kf.rotation_euler[2])
    print(f"\nJaw KF[0] @ t={kf.time_ms:.1f}ms: {raw}")
    print(f"  trailer = {jaw.trailer_floats}")

    # Each candidate interpretation gives a 3x3 rotation matrix
    candidates = []

    # Group A: last 3 floats are the rotation
    last3 = np.array(raw[3:])
    for order in ['XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX']:
        M = euler_to_mat(last3[0], last3[1], last3[2], order)
        candidates.append((f"last3 euler {order} (rad)", M))
        Md = euler_to_mat(math.radians(last3[0]), math.radians(last3[1]),
                          math.radians(last3[2]), order)
        candidates.append((f"last3 euler {order} (deg interp)", Md))
    candidates.append(("last3 axis*angle", axis_angle_to_mat(last3)))
    q = quat_xyz_to_mat(last3)
    if q is not None:
        candidates.append(("last3 quat-xyz (unit, +W)", q))
    candidates.append(("last3 MRP", mrp_to_mat(last3)))
    candidates.append(("last3 Gibbs (tan θ/2)", gibbs_to_mat(last3)))
    candidates.append(("last3 axis*angle (negated)", axis_angle_to_mat(-last3)))
    candidates.append(("last3 MRP (negated)", mrp_to_mat(-last3)))
    candidates.append(("last3 Gibbs (negated)", gibbs_to_mat(-last3)))

    # Group B: first 3 floats are the rotation
    first3 = np.array(raw[:3])
    for order in ['XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX']:
        M = euler_to_mat(first3[0], first3[1], first3[2], order)
        candidates.append((f"first3 euler {order} (rad)", M))
    candidates.append(("first3 axis*angle", axis_angle_to_mat(first3)))
    q = quat_xyz_to_mat(first3)
    if q is not None:
        candidates.append(("first3 quat-xyz (unit, +W)", q))
    candidates.append(("first3 MRP", mrp_to_mat(first3)))

    # Group C: first 4 floats are quaternion (wxyz / xyzw)
    candidates.append(("first4 quat wxyz",
        _quat_to_mat(raw[0], raw[1], raw[2], raw[3])))
    candidates.append(("first4 quat xyzw",
        _quat_to_mat(raw[3], raw[0], raw[1], raw[2])))
    candidates.append(("last4 quat wxyz",
        _quat_to_mat(raw[2], raw[3], raw[4], raw[5])))
    candidates.append(("last4 quat xyzw",
        _quat_to_mat(raw[5], raw[2], raw[3], raw[4])))

    # Sort by closeness
    scored = sorted(((diff_score(M, target), name, M) for name, M in candidates),
                    key=lambda r: r[0])
    print("\nTop 10 closest matches (Frobenius distance from target):")
    for score, name, M in scored[:10]:
        print(f"  {score:6.3f}  {name}")

    print("\nBest match:")
    score, name, M = scored[0]
    print(f"  {name}  (distance {score:.3f})")
    print(M.round(3))


def _quat_to_mat(w, x, y, z):
    """Normalize and convert to matrix (returns None if degenerate)."""
    n = math.sqrt(w*w + x*x + y*y + z*z)
    if n < 1e-9:
        return None
    w, x, y, z = w/n, x/n, y/n, z/n
    return np.array([
        [1 - 2*(y*y+z*z), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x*x+z*z), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x*x+y*y)],
    ])


if __name__ == "__main__":
    main()
