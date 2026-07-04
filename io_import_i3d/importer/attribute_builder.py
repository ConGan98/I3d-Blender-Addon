"""
Populate StjerneIdioten i3dio's `i3d_attributes` from the source i3d node
attributes, so imported nodes carry their GIANTS attributes in the STANDARD
I3D panels (Object Properties + Data Properties) and round-trip on re-export.

How it works
------------
Each i3dio property group defines an `i3d_map`:
    field_key -> {'name': <xml attribute>, 'default': ..., 'type': 'HEX'?, ...}
which i3dio uses to WRITE attributes on export. We invert it
(`xml attribute -> field_key`) and, for every attribute on the imported node,
set the matching field — converting the XML string to the field's real type via
its RNA. Because we set i3dio's own property groups (not raw custom props), the
values appear in the normal panels and export correctly.

No-op if i3dio isn't installed (the objects simply have no `i3d_attributes`).
Covers Object (`obj.i3d_attributes`) and Data (`obj.data.i3d_attributes`, e.g.
mesh/light) groups; materials/shaders are a later pass.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import bpy

from . import xml_parser as xp


def _invert_i3d_map(pg) -> dict:
    """xml attribute name -> (field_key, entry) for Node-placed, non-tracking
    fields. Tracking fields derive from a native Blender property (e.g.
    visibility <-> hide_render) and are set elsewhere, so we skip them."""
    out: dict = {}
    i3d_map = getattr(pg, "i3d_map", None)
    if not isinstance(i3d_map, dict):
        return out
    for field_key, entry in i3d_map.items():
        if not isinstance(entry, dict):
            continue
        xml_name = entry.get("name")
        if not xml_name:
            continue
        if entry.get("placement", "Node") != "Node":
            continue
        if entry.get("tracking"):
            continue
        out[xml_name] = (field_key, entry)
    return out


def _coerce(pg, field_key: str, entry: dict, raw: str):
    """Convert an XML attribute string to the property's Python value, using the
    field's RNA type (falls back to the raw string)."""
    if entry.get("type") == "HEX":
        v = raw.strip()
        return v[2:] if v[:2].lower() == "0x" else v

    rna = pg.bl_rna.properties.get(field_key)
    if rna is None:
        return raw

    t = rna.type
    if t == "BOOLEAN":
        return raw.strip().lower() in ("true", "1", "yes")
    if t == "INT":
        return int(float(raw))
    if t == "FLOAT":
        parts = [p for p in raw.replace(",", " ").split() if p]
        arr = getattr(rna, "array_length", 0) or 0
        if arr > 1:
            vals = [float(x) for x in parts]
            vals = (vals + [0.0] * arr)[:arr]
            return tuple(vals)
        return float(parts[0]) if parts else 0.0
    # ENUM / STRING / anything else: pass the string (enum ids are strings).
    return raw.strip() if t == "ENUM" else raw


# GIANTS writes the rigid-body kind as separate boolean flags, but i3dio models
# it as a single enum (`rigid_body_type`) with NO 'name' in its i3d_map — so the
# generic inverter can't reach it. Map the flags to the enum, first match wins.
_RIGID_BODY_FLAGS = (
    ("static", "static"),
    ("dynamic", "dynamic"),
    ("kinematic", "kinematic"),
    ("compoundChild", "compoundChild"),
)


def _apply_rigid_body(pg, raw_attribs: dict, stats: dict) -> None:
    if pg is None or not hasattr(pg, "rigid_body_type"):
        return
    for xml_flag, enum_val in _RIGID_BODY_FLAGS:
        v = raw_attribs.get(xml_flag)
        if v is not None and v.strip().lower() in ("true", "1", "yes"):
            try:
                pg.rigid_body_type = enum_val
                stats["set"] += 1
            except Exception:
                stats["failed"] += 1
            return  # a node is only one rigid-body kind


def _apply_to_group(pg, raw_attribs: dict, stats: dict) -> None:
    if pg is None:
        return
    inv = _invert_i3d_map(pg)
    if not inv:
        return
    for xml_name, raw_value in raw_attribs.items():
        target = inv.get(xml_name)
        if target is None:
            continue
        field_key, entry = target
        try:
            setattr(pg, field_key, _coerce(pg, field_key, entry, raw_value))
            stats["set"] += 1
        except Exception:
            stats["failed"] += 1


def apply_node_attributes(doc: xp.I3DDocument, nodes_by_id: dict, *, log=None) -> dict:
    """Copy each node's i3d attributes into i3dio's `i3d_attributes` on the
    object and its data block. Returns a stats dict; no-op without i3dio."""
    stats = {"objects": 0, "set": 0, "failed": 0}
    seen_i3dio = False

    for node in doc.all_nodes():
        obj = nodes_by_id.get(node.node_id)
        if obj is None:
            continue
        raw = getattr(node, "raw_attribs", None)
        if not raw:
            continue

        obj_pg = getattr(obj, "i3d_attributes", None)
        data = getattr(obj, "data", None)
        data_pg = getattr(data, "i3d_attributes", None)
        if obj_pg is None and data_pg is None:
            continue  # i3dio not registered, or this type carries no attributes
        seen_i3dio = True

        before = stats["set"]
        _apply_to_group(obj_pg, raw, stats)
        _apply_to_group(data_pg, raw, stats)
        _apply_rigid_body(obj_pg, raw, stats)  # enum with no i3d_map 'name'
        if stats["set"] > before:
            stats["objects"] += 1

    if log:
        if not seen_i3dio:
            log.info("i3d attributes: skipped (StjerneIdioten i3dio not installed "
                     "— no i3d_attributes to populate)")
        else:
            log.info("i3d attributes: set %d field(s) across %d object(s) "
                     "(%d unmapped/failed)",
                     stats["set"], stats["objects"], stats["failed"])
    return stats
