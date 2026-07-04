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
        name="Import animations",
        description=(
            "Parse the .i3d.anim clips and create Actions on the armature. Point "
            "'Animation i3d' at the animations file (e.g. cattleCalfAnimations.i3d) "
            "and enable 'Exact bone orientation' for correct playback"
        ),
        default=False,
    )
    animation_i3d_path: StringProperty(
        name="Animation i3d",
        description=(
            "The separate skeleton-only animation file (e.g. cattleCalfAnimations.i3d) "
            "whose sibling .i3d.anim holds the clips. It provides the bone id->name "
            "table the tracks reference. Leave blank to use a .anim referenced by the "
            "imported model itself"
        ),
        subtype='FILE_PATH',
        default="",
    )
    exact_bone_orientation: BoolProperty(
        name="Exact bone orientation",
        description=(
            "Build bones in each joint's exact rest orientation (they point sideways) "
            "instead of down the chain. No longer required for animation — the importer "
            "now deforms via forward kinematics, so normal point-at-child bones animate "
            "correctly. Leave OFF unless you specifically want the raw joint frames"
        ),
        default=False,
    )
    import_materials: BoolProperty(
        name="Import materials",
        description="Create Principled BSDF materials and link textures (where resolvable)",
        default=True,
    )
    import_attributes: BoolProperty(
        name="Import i3d attributes",
        description=(
            "Copy each node's GIANTS attributes (collision, density, shadows, LOD, "
            "masks, …) into StjerneIdioten i3dio's I3D panels in Object/Data "
            "Properties, so they show up and round-trip on re-export. No effect if "
            "the i3dio exporter isn't installed"
        ),
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
    order_prefix: BoolProperty(
        name="Preserve order (01_ prefix)",
        description=(
            "Rename imported objects with a zero-padded order prefix (01_, 02_, …) "
            "per parent, so Blender's alphabetical outliner keeps the original i3d "
            "scene order instead of scrambling it. Bones aren't renamed, and the "
            "round-trip tools strip the prefix, so export is unaffected"
        ),
        default=True,
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
        col.prop(self, "import_attributes")
        col.prop(self, "import_animations")

        col = layout.column(heading="Animation")
        sub = col.column()
        sub.enabled = self.import_animations
        sub.prop(self, "animation_i3d_path")
        sub.prop(self, "exact_bone_orientation")

        col = layout.column(heading="Orientation")
        col.prop(self, "axis_convention")
        col.prop(self, "forward_axis")

        col = layout.column(heading="Hierarchy")
        col.prop(self, "order_prefix")
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
                    'animation_i3d_path': self.animation_i3d_path,
                    'exact_bone_orientation': self.exact_bone_orientation,
                    'import_materials': self.import_materials,
                    'import_attributes': self.import_attributes,
                    'order_prefix': self.order_prefix,
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
