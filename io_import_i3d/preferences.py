import bpy
from bpy.props import StringProperty, EnumProperty


class I3DImporterPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    data_path: StringProperty(
        name="$data path",
        description="Root for resolving $data/... texture paths (typically the FS install's data folder)",
        default="",
        subtype='DIR_PATH',
    )
    data_s_path: StringProperty(
        name="$dataS path",
        description="Root for resolving $dataS/... shared-asset texture paths (typically the FS install's dataS folder)",
        default="",
        subtype='DIR_PATH',
    )
    log_level: EnumProperty(
        name="Log level",
        items=[
            ('DEBUG', "Debug", "Verbose"),
            ('INFO', "Info", "Normal"),
            ('WARNING', "Warning", "Warnings + errors only"),
            ('ERROR', "Error", "Errors only"),
        ],
        default='INFO',
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "data_path")
        layout.prop(self, "data_s_path")
        layout.prop(self, "log_level")
