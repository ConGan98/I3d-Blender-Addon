"""
GIANTS uses Y-up, right-handed; rotations are intrinsic Euler ZY'X'' in degrees.

The corresponding matrix is M = R_z(z) @ R_y(y) @ R_x(x). In Blender, the Euler
order naming reads RIGHT-TO-LEFT in the matrix product, so 'ZYX' would build
Rx @ Ry @ Rz (wrong for GIANTS); 'XYZ' builds Rz @ Ry @ Rx (correct). We use
rotation_mode='XYZ' on objects/pose-bones so the Euler triplet from XML maps
directly to the same matrix GIANTS Editor produces. (Single-axis rotations like
cow's spine (0, -90, 0) work in either mode, which is why the convention bug
went unnoticed until pigs/sheep with multi-component joint rotations.)

The Y-up→Z-up conversion is applied as a rotation at the top-level import root;
all children inherit. The base conversion is +X 90° (maps GIANTS +Y → Blender
+Z, GIANTS +Z → Blender -Y). The user can then add an extra Z rotation to pick
which Blender axis the model's "forward" should face.
"""
from __future__ import annotations

import math


_FORWARD_TO_Z_DEG = {
    # In GIANTS the cow's head is at +Z (forward=+Z). After +X 90° that
    # becomes Blender -Y. Adding the Z rotations below flips that to taste:
    "-Y": 0.0,    # cow head at Blender -Y (Front view shows the back)
    "+Y": 180.0,  # cow head at Blender +Y (Front view shows the face)
    "+X": 90.0,   # cow head at Blender +X (Right view shows the face)
    "-X": -90.0,  # cow head at Blender -X (Left view shows the face)
}


def import_root_matrix(forward: str = "-Y"):
    """Return the 4x4 world matrix for the import root.

    forward: which Blender axis the GIANTS forward (+Z) should map to.
    """
    from mathutils import Matrix
    base = Matrix.Rotation(math.radians(90.0), 4, 'X')
    extra_z = _FORWARD_TO_Z_DEG.get(forward, 0.0)
    if extra_z != 0.0:
        return Matrix.Rotation(math.radians(extra_z), 4, 'Z') @ base
    return base


def degrees_to_radians_zyx(rotation_deg: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        math.radians(rotation_deg[0]),
        math.radians(rotation_deg[1]),
        math.radians(rotation_deg[2]),
    )
