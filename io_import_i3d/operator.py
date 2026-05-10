import bpy
from bpy.props import StringProperty, BoolProperty, FloatProperty, EnumProperty
from bpy_extras.io_utils import ImportHelper

from .importer import import_i3d


class I3D_OT_import_i3d(bpy.types.Operator, ImportHelper):
    """Import a GIANTS Engine .i3d scene"""
    bl_idname = "import_scene.giants_i3d"
    bl_label = "Import GIANTS i3d"
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = ".i3d"
    filter_glob: StringProperty(default="*.i3d", options={'HIDDEN'})

    import_animations: BoolProperty(
        name="Import animations (experimental)",
        description=(
            "Parse the sibling .i3d.anim file and create Actions on the armature. "
            "The track structure is decoded but the rotation encoding for the "
            "per-keyframe values is not yet fully reverse-engineered, so motion "
            "may look wrong. Off by default until cracked"
        ),
        default=False,
    )
    import_materials: BoolProperty(
        name="Import materials",
        description="Create Principled BSDF materials and link textures (where resolvable)",
        default=True,
    )
    bone_display_size: FloatProperty(
        name="Bone display size (m)",
        description="Fallback bone length for leaf bones; small enough to avoid visual clutter",
        default=0.05,
        min=0.001,
        max=10.0,
    )
    axis_convention: EnumProperty(
        name="Axis convention",
        items=[
            ('AUTO', "Auto (Y-up → Z-up)", "Apply +X 90° at the import root"),
            ('NONE', "None", "Keep GIANTS coordinates (debug)"),
        ],
        default='AUTO',
    )
    forward_axis: EnumProperty(
        name="Forward axis",
        description="Which Blender axis the model's forward direction (+Z in GIANTS) should face after import",
        items=[
            ('+Y', "+Y (face Front view)", "Standard rig: face visible in Numpad 1 Front view"),
            ('-Y', "-Y (face Back view)", "Raw GIANTS orientation, no extra Z rotation"),
            ('+X', "+X (face Right view)", "Face visible in Numpad 3 Right view"),
            ('-X', "-X (face Left view)", "Face visible in Numpad Ctrl+3 Left view"),
        ],
        default='-Y',
    )
    wrap_in_container: BoolProperty(
        name="Wrap in container empty",
        description=(
            "If enabled, all imported objects sit under a top-level Empty named after "
            "the .i3d file. Convenient for organisation but adds an extra TransformGroup "
            "that shifts every nodeId on re-export — disable for round-trip with "
            "GIANTS animations or other nodeId-sensitive features"
        ),
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(heading="Import")
        col.prop(self, "import_materials")
        col.prop(self, "import_animations")

        col = layout.column(heading="Orientation")
        col.prop(self, "axis_convention")
        col.prop(self, "forward_axis")

        col = layout.column(heading="Hierarchy")
        col.prop(self, "wrap_in_container")

        col = layout.column(heading="Armature")
        col.prop(self, "bone_display_size")

    def execute(self, context):
        try:
            stats = import_i3d.run(
                context=context,
                filepath=self.filepath,
                options={
                    'import_animations': self.import_animations,
                    'import_materials': self.import_materials,
                    'bone_display_size': self.bone_display_size,
                    'axis_convention': self.axis_convention,
                    'forward_axis': self.forward_axis,
                    'wrap_in_container': self.wrap_in_container,
                    'operator': self,
                },
            )
            self.report(
                {'INFO'},
                "i3d import OK — {n_objects} objs, {n_bones} bones, "
                "{n_meshes_bound} meshes bound ({n_vertex_groups} vgroups), "
                "{n_actions} animations".format(**stats),
            )
            return {'FINISHED'}
        except import_i3d.I3DImportError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
