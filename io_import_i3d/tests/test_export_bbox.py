"""Check the coordinate range of vertex data in the exported .i3d file
to figure out which coordinate system the mesh is in."""
import re
import sys
from pathlib import Path

EXPORTED = Path(__file__).resolve().parents[2] / "Untitled.i3d"


def main():
    if not EXPORTED.exists():
        print(f"missing: {EXPORTED}")
        return 1
    text = EXPORTED.read_text(encoding="iso-8859-1")
    # Find each <IndexedTriangleSet name="..."> block and gather its <v p="x y z" .../> points.
    set_re = re.compile(r'<IndexedTriangleSet name="([^"]+)"[^>]*>(.*?)</IndexedTriangleSet>', re.DOTALL)
    v_re = re.compile(r'<v p="([-0-9.eE+ ]+)"')
    for m in set_re.finditer(text):
        name = m.group(1)
        body = m.group(2)
        coords = []
        for vm in v_re.finditer(body):
            try:
                x, y, z = (float(p) for p in vm.group(1).split())
            except ValueError:
                continue
            coords.append((x, y, z))
        if not coords:
            print(f"{name}: no vertices found")
            continue
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        zs = [c[2] for c in coords]
        print(f"{name}: {len(coords)} verts")
        print(f"   X: {min(xs):.3f} .. {max(xs):.3f}   (range {max(xs)-min(xs):.3f})")
        print(f"   Y: {min(ys):.3f} .. {max(ys):.3f}   (range {max(ys)-min(ys):.3f})")
        print(f"   Z: {min(zs):.3f} .. {max(zs):.3f}   (range {max(zs)-min(zs):.3f})")


if __name__ == "__main__":
    sys.exit(main() or 0)
