bl_info = {
    "name": "GIANTS i3d Importer",
    "author": "Conor Gannon",
    "version": (0, 2, 0),
    "blender": (4, 5, 0),
    "location": "File > Import > GIANTS i3d (.i3d)",
    "description": "Import GIANTS Engine .i3d scenes (Farming Simulator) including .i3d.shapes meshes and .i3d.anim animations",
    "category": "Import-Export",
    "doc_url": "",
    "tracker_url": "",
}

import bpy

from . import preferences, operator


_classes = (
    preferences.I3DImporterPreferences,
    operator.I3D_OT_import_i3d,
)


def _menu_func_import(self, context):
    self.layout.operator(operator.I3D_OT_import_i3d.bl_idname, text="GIANTS i3d (.i3d)")


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_func_import)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
