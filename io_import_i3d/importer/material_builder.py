"""
Build basic Principled BSDF materials from i3d <Material> elements.

For each material:
  * Diffuse texture        -> Image Texture -> Principled.Base Color
  * Normal map texture     -> Image Texture (Non-Color) -> Normal Map -> Principled.Normal
  * Gloss texture          -> Image Texture (Non-Color) -> Principled.Specular IOR Level
  * diffuseColor (no tex)  -> Principled.Base Color
  * customShaderId/Variation/CustomParameter — preserved as material custom
    properties for future fidelity work; not interpreted in v1.

Texture resolution (in priority order):
  1. Path is absolute and exists -> use as-is.
  2. Path starts with $data/  -> prepend prefs.data_path.
  3. Path starts with $dataS/ -> prepend prefs.data_s_path.
  4. Otherwise relative to .i3d location.
If unresolved, the Image Texture node is created with a missing-image
placeholder; import still succeeds.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import bpy

from . import xml_parser as xp


def build_materials(
    doc: xp.I3DDocument,
    nodes_by_id: dict,
    *,
    data_path: str = "",
    data_s_path: str = "",
    log=None,
):
    """Create one bpy.types.Material per i3d <Material> and assign to all
    Shape meshes that reference it. Returns count of materials created."""
    import bpy

    if not doc.materials:
        return 0

    base_dir = doc.source_path.parent
    mats_by_id: dict[int, bpy.types.Material] = {}

    for mid, md in doc.materials.items():
        mat = bpy.data.materials.new(name=md.name or f"i3dMat_{mid}")
        mat.use_nodes = True
        nt = mat.node_tree
        # Reset default nodes
        for n in list(nt.nodes):
            nt.nodes.remove(n)

        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (350, 0)
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

        if md.diffuse_color:
            try:
                bsdf.inputs["Base Color"].default_value = md.diffuse_color
            except KeyError:
                pass

        if md.diffuse_file_id is not None:
            img = _load_image_for_file_id(
                doc, md.diffuse_file_id, base_dir, data_path, data_s_path, log,
            )
            if img is not None:
                tex = nt.nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.location = (-300, 200)
                nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

        if md.normal_file_id is not None:
            img = _load_image_for_file_id(
                doc, md.normal_file_id, base_dir, data_path, data_s_path, log,
            )
            if img is not None:
                tex = nt.nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.location = (-600, -100)
                tex.image.colorspace_settings.name = 'Non-Color'
                nm = nt.nodes.new("ShaderNodeNormalMap")
                nm.location = (-300, -100)
                nt.links.new(tex.outputs["Color"], nm.inputs["Color"])
                if "Normal" in bsdf.inputs:
                    nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])

        if md.gloss_file_id is not None:
            img = _load_image_for_file_id(
                doc, md.gloss_file_id, base_dir, data_path, data_s_path, log,
            )
            if img is not None:
                tex = nt.nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.location = (-300, -350)
                tex.image.colorspace_settings.name = 'Non-Color'
                # Blender 4.x renamed; try a few candidates.
                spec_input = None
                for cand in ("Specular IOR Level", "Specular", "Roughness"):
                    if cand in bsdf.inputs:
                        spec_input = bsdf.inputs[cand]
                        break
                if spec_input is not None:
                    nt.links.new(tex.outputs["Color"], spec_input)

        # Stash custom shader info for later round-trip
        if md.custom_shader_id is not None:
            mat["_i3d_custom_shader_id"] = int(md.custom_shader_id)
        if md.custom_shader_variation:
            mat["_i3d_custom_shader_variation"] = md.custom_shader_variation
        if md.custom_parameters:
            mat["_i3d_custom_parameters"] = dict(md.custom_parameters)
        if md.custom_maps:
            mat["_i3d_custom_maps"] = {k: int(v) for k, v in md.custom_maps.items()}

        mats_by_id[mid] = mat

    # Assign materials to Shape meshes by materialIds.
    for n in doc.all_nodes():
        if n.kind != "Shape" or not n.material_ids:
            continue
        obj = nodes_by_id.get(n.node_id)
        if obj is None or obj.type != 'MESH':
            continue
        for slot_mid in n.material_ids:
            mat = mats_by_id.get(slot_mid)
            if mat is None:
                continue
            if mat.name not in obj.data.materials:
                obj.data.materials.append(mat)

    return len(mats_by_id)


def _load_image_for_file_id(doc, fid, base_dir, data_path, data_s_path, log):
    import bpy
    fref = doc.files.get(fid)
    if fref is None or not fref.filename:
        return None
    resolved = _resolve_path(fref.filename, base_dir, data_path, data_s_path)
    try:
        if resolved is not None and resolved.exists():
            return bpy.data.images.load(str(resolved), check_existing=True)
    except Exception as e:
        if log is not None:
            log.warning("Could not load image %s: %s", resolved, e)
    # Create a placeholder so the texture node still has an image slot.
    img = bpy.data.images.new(Path(fref.filename).name, width=8, height=8)
    img["_i3d_unresolved_path"] = fref.filename
    return img


def _resolve_path(filename: str, base_dir: Path, data_path: str, data_s_path: str) -> Path | None:
    if not filename:
        return None
    p = Path(filename)
    if p.is_absolute() and p.exists():
        return p
    s = filename.replace("\\", "/")
    if s.startswith("$data/"):
        if data_path:
            return Path(data_path) / s[len("$data/"):]
        return None
    if s.startswith("$dataS/"):
        if data_s_path:
            return Path(data_s_path) / s[len("$dataS/"):]
        return None
    cand = base_dir / s
    return cand if cand.exists() else cand
