"""
Parse a GIANTS .i3d XML file into typed dataclasses.

Output preserves the original tree order so depth-first walks reproduce the
GIANTS hierarchy exactly. Node IDs (the canonical key) are kept as ints, and
parent linkage is captured by tree position rather than a separate map.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _parse_vec3(s: Optional[str], default=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    if not s:
        return default
    parts = s.split()
    if len(parts) != 3:
        return default
    return (float(parts[0]), float(parts[1]), float(parts[2]))


def _parse_int_list(s: Optional[str]) -> list[int]:
    if not s:
        return []
    return [int(p) for p in s.split() if p]


def _parse_bool(s: Optional[str], default: bool = False) -> bool:
    if s is None:
        return default
    return s.strip().lower() in ("true", "1", "yes")


@dataclass
class FileRef:
    file_id: int
    filename: str
    relative_path: bool = False


@dataclass
class MaterialDef:
    material_id: int
    name: str
    diffuse_color: tuple[float, float, float, float] | None = None
    diffuse_file_id: int | None = None
    normal_file_id: int | None = None
    gloss_file_id: int | None = None
    custom_shader_id: int | None = None
    custom_shader_variation: str | None = None
    custom_parameters: dict[str, str] = field(default_factory=dict)
    custom_maps: dict[str, int] = field(default_factory=dict)


@dataclass
class SceneNode:
    """One scene-graph node. Kind is the XML tag (TransformGroup, Shape, Light, Camera)."""
    kind: str
    name: str
    node_id: int
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)  # Euler ZY'X'' degrees
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    visibility: bool = True
    children: list["SceneNode"] = field(default_factory=list)

    # Shape-only:
    shape_id: int | None = None
    material_ids: list[int] = field(default_factory=list)
    skin_bind_node_ids: list[int] = field(default_factory=list)
    casts_shadows: bool = True
    receive_shadows: bool = True
    non_renderable: bool = False
    clip_distance: float | None = None

    # Misc:
    extras: dict[str, str] = field(default_factory=dict)


@dataclass
class AnimationRef:
    """Either inline (clips populated) or an external file reference."""
    external_file: str | None = None  # path relative to .i3d


@dataclass
class I3DDocument:
    name: str
    version: str
    files: dict[int, FileRef]
    materials: dict[int, MaterialDef]
    external_shapes_file: str | None
    scene_roots: list[SceneNode]
    animation: AnimationRef | None
    source_path: Path

    def all_nodes(self) -> list[SceneNode]:
        out: list[SceneNode] = []
        stack = list(reversed(self.scene_roots))
        while stack:
            n = stack.pop()
            out.append(n)
            stack.extend(reversed(n.children))
        return out

    def by_node_id(self) -> dict[int, SceneNode]:
        return {n.node_id: n for n in self.all_nodes()}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_i3d(path: Path | str) -> I3DDocument:
    path = Path(path)
    tree = ET.parse(path)
    root = tree.getroot()
    if root.tag != "i3D":
        raise ValueError(f"Expected <i3D> root, got <{root.tag}>")

    doc_name = root.attrib.get("name", path.stem)
    version = root.attrib.get("version", "")

    files: dict[int, FileRef] = {}
    files_el = root.find("Files")
    if files_el is not None:
        for f in files_el.findall("File"):
            try:
                fid = int(f.attrib["fileId"])
            except (KeyError, ValueError):
                continue
            files[fid] = FileRef(
                file_id=fid,
                filename=f.attrib.get("filename", ""),
                relative_path=_parse_bool(f.attrib.get("relativePath")),
            )

    materials: dict[int, MaterialDef] = {}
    mats_el = root.find("Materials")
    if mats_el is not None:
        for m in mats_el.findall("Material"):
            try:
                mid = int(m.attrib["materialId"])
            except (KeyError, ValueError):
                continue
            md = MaterialDef(material_id=mid, name=m.attrib.get("name", f"mat_{mid}"))
            dc = m.attrib.get("diffuseColor")
            if dc:
                parts = dc.split()
                if len(parts) == 4:
                    md.diffuse_color = tuple(float(p) for p in parts)  # type: ignore[assignment]
                elif len(parts) == 3:
                    md.diffuse_color = (*[float(p) for p in parts], 1.0)  # type: ignore[assignment]
            csid = m.attrib.get("customShaderId")
            if csid is not None:
                try:
                    md.custom_shader_id = int(csid)
                except ValueError:
                    pass
            md.custom_shader_variation = m.attrib.get("customShaderVariation")
            for tex in m.findall("Texture"):
                fid = tex.attrib.get("fileId")
                if fid:
                    md.diffuse_file_id = int(fid)
            for nrm in m.findall("Normalmap"):
                fid = nrm.attrib.get("fileId")
                if fid:
                    md.normal_file_id = int(fid)
            for gls in m.findall("Glossmap"):
                fid = gls.attrib.get("fileId")
                if fid:
                    md.gloss_file_id = int(fid)
            for cp in m.findall("CustomParameter"):
                n = cp.attrib.get("name")
                v = cp.attrib.get("value")
                if n and v is not None:
                    md.custom_parameters[n] = v
            for cm in m.findall("Custommap"):
                n = cm.attrib.get("name")
                fid = cm.attrib.get("fileId")
                if n and fid:
                    try:
                        md.custom_maps[n] = int(fid)
                    except ValueError:
                        pass
            materials[mid] = md

    external_shapes_file: str | None = None
    shapes_el = root.find("Shapes")
    if shapes_el is not None:
        external_shapes_file = shapes_el.attrib.get("externalShapesFile")

    scene_roots: list[SceneNode] = []
    scene_el = root.find("Scene")
    if scene_el is not None:
        for child in scene_el:
            node = _parse_scene_node(child)
            if node is not None:
                scene_roots.append(node)

    animation: AnimationRef | None = None
    anim_el = root.find("Animation")
    if anim_el is not None:
        animation = AnimationRef(external_file=anim_el.attrib.get("externalAnimFile"))

    return I3DDocument(
        name=doc_name,
        version=version,
        files=files,
        materials=materials,
        external_shapes_file=external_shapes_file,
        scene_roots=scene_roots,
        animation=animation,
        source_path=path,
    )


_NODE_KINDS = {"TransformGroup", "Shape", "Light", "Camera"}


def _parse_scene_node(el: ET.Element) -> SceneNode | None:
    if el.tag not in _NODE_KINDS:
        return None
    a = el.attrib
    try:
        node_id = int(a["nodeId"])
    except (KeyError, ValueError):
        return None
    node = SceneNode(
        kind=el.tag,
        name=a.get("name", f"{el.tag}_{node_id}"),
        node_id=node_id,
        translation=_parse_vec3(a.get("translation"), (0.0, 0.0, 0.0)),
        rotation=_parse_vec3(a.get("rotation"), (0.0, 0.0, 0.0)),
        scale=_parse_vec3(a.get("scale"), (1.0, 1.0, 1.0)),
        visibility=_parse_bool(a.get("visibility"), True),
    )
    if el.tag == "Shape":
        try:
            node.shape_id = int(a["shapeId"])
        except (KeyError, ValueError):
            node.shape_id = None
        node.material_ids = _parse_int_list(a.get("materialIds"))
        node.skin_bind_node_ids = _parse_int_list(a.get("skinBindNodeIds"))
        node.casts_shadows = _parse_bool(a.get("castsShadows"), True)
        node.receive_shadows = _parse_bool(a.get("receiveShadows"), True)
        node.non_renderable = _parse_bool(a.get("nonRenderable"), False)
        cd = a.get("clipDistance")
        if cd:
            try:
                node.clip_distance = float(cd)
            except ValueError:
                pass

    # capture remaining attributes as opaque extras (cheap to keep, useful for debug)
    handled = {
        "name", "nodeId", "translation", "rotation", "scale", "visibility",
        "shapeId", "materialIds", "skinBindNodeIds", "castsShadows",
        "receiveShadows", "nonRenderable", "clipDistance",
    }
    node.extras = {k: v for k, v in a.items() if k not in handled}

    for child in el:
        sub = _parse_scene_node(child)
        if sub is not None:
            node.children.append(sub)
    return node
