"""
Modeling helpers for i3d work — exposed in the 3D viewport sidebar (N) under an
"i3d" tab.

- Skin to i3d Armature: bind a new/variant mesh (e.g. body_pedigree, a bulked-up
  bull frame, or a brand-new mesh) to the SAME skeleton as an already-skinned
  reference mesh, transferring vertex weights across by nearest surface. Lets you
  put several meshes (growth/quality variants) on one skeleton and one animation.

- i3d Visibility: set the exported i3d `visibility` (via hide_render, which is
  what i3dio exports) so your game-side code can toggle which variant shows.
"""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty


def _armature_of(mesh_obj):
    """The armature object driving mesh_obj via its Armature modifier, or None."""
    for m in mesh_obj.modifiers:
        if m.type == 'ARMATURE' and m.object is not None:
            return m.object
    return None


class I3D_OT_skin_to_armature(bpy.types.Operator):
    """Skin the selected mesh(es) to the same armature as the ACTIVE reference mesh, copying its weights across by nearest surface.

Select the target mesh(es), then shift-select the already-skinned reference LAST so it is active, and run"""
    bl_idname = "i3d.skin_to_armature"
    bl_label = "Skin to i3d Armature"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH'
                and sum(1 for o in context.selected_objects if o.type == 'MESH') >= 2)

    def execute(self, context):
        ref = context.active_object
        if ref is None or ref.type != 'MESH':
            self.report({'ERROR'}, "The ACTIVE object must be the reference mesh (select it last)")
            return {'CANCELLED'}
        armature = _armature_of(ref)
        if armature is None:
            self.report({'ERROR'}, f"Reference '{ref.name}' has no Armature modifier — "
                                   "pick an already-skinned mesh as the reference")
            return {'CANCELLED'}
        targets = [o for o in context.selected_objects if o is not ref and o.type == 'MESH']
        if not targets:
            self.report({'ERROR'}, "Select at least one target mesh plus the reference")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Remember selection/active to restore afterwards.
        view = context.view_layer
        prev_active = view.objects.active
        prev_selected = [o for o in context.selected_objects]

        done, failed = 0, 0
        for tgt in targets:
            if self._skin_one(context, ref, tgt, armature):
                done += 1
            else:
                failed += 1

        # Restore selection.
        for o in view.objects:
            o.select_set(False)
        for o in prev_selected:
            try:
                o.select_set(True)
            except Exception:
                pass
        view.objects.active = prev_active

        msg = f"Skinned {done} mesh(es) to '{armature.name}' from '{ref.name}'"
        if failed:
            msg += f" ({failed} failed — see console)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}

    def _skin_one(self, context, ref, tgt, armature) -> bool:
        # 1. Ensure the target carries the reference's bone vertex groups.
        for vg in ref.vertex_groups:
            if vg.name not in tgt.vertex_groups:
                tgt.vertex_groups.new(name=vg.name)

        # 2. Armature modifier -> the shared skeleton.
        mod = tgt.modifiers.get("Armature")
        if mod is None or mod.type != 'ARMATURE':
            mod = tgt.modifiers.new("Armature", 'ARMATURE')
        mod.object = armature
        mod.use_vertex_groups = True
        mod.use_bone_envelopes = False

        # 3. Transfer weights: data_transfer goes active(source) -> selected(dest).
        view = context.view_layer
        for o in view.objects:
            o.select_set(False)
        ref.select_set(True)
        tgt.select_set(True)
        view.objects.active = ref  # source
        try:
            bpy.ops.object.data_transfer(
                use_reverse_transfer=False,          # active -> selected
                data_type='VGROUP_WEIGHTS',
                use_create=True,
                vert_mapping='POLYINTERP_NEAREST',   # nearest face, interpolated
                layers_select_src='ALL',
                layers_select_dst='NAME',
                mix_mode='REPLACE',
            )
        except Exception as e:
            print(f"[i3d] weight transfer to '{tgt.name}' failed: {e}")
            return False
        return True


class I3D_OT_set_i3d_visibility(bpy.types.Operator):
    """Set the exported i3d visibility of the selected objects.

i3dio exports `visibility` from Blender's hide_render, so this sets that. Use for growth/quality variant meshes your game code toggles (like the horns trick)"""
    bl_idname = "i3d.set_visibility"
    bl_label = "Set i3d Visibility"
    bl_options = {'REGISTER', 'UNDO'}

    visible: BoolProperty(name="Visible", default=True)

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        n = 0
        for o in context.selected_objects:
            # i3dio writes visibility = NOT hide_render (tracked). Setting
            # hide_render is what actually exports. Leave hide_viewport alone so
            # you can still see both meshes while modelling.
            o.hide_render = not self.visible
            n += 1
        state = "visible" if self.visible else "hidden"
        self.report({'INFO'}, f"Set {n} object(s) i3d visibility -> {state}")
        return {'FINISHED'}


class VIEW3D_PT_i3d_tools(bpy.types.Panel):
    bl_label = "i3d Tools"
    bl_idname = "VIEW3D_PT_i3d_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "i3d"

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Skinning", icon='MOD_ARMATURE')
        box.label(text="Select target(s), then the")
        box.label(text="skinned reference LAST.")
        box.operator(I3D_OT_skin_to_armature.bl_idname, text="Skin to i3d Armature")

        box = layout.box()
        box.label(text="i3d Visibility", icon='HIDE_OFF')
        row = box.row(align=True)
        row.operator(I3D_OT_set_i3d_visibility.bl_idname, text="Visible").visible = True
        row.operator(I3D_OT_set_i3d_visibility.bl_idname, text="Hidden").visible = False


classes = (
    I3D_OT_skin_to_armature,
    I3D_OT_set_i3d_visibility,
    VIEW3D_PT_i3d_tools,
)
