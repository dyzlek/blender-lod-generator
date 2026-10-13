"""
Export Pipeline Operator for Game Engines (FBX / glTF).
Prepares hierarchy, resets exploded view offsets, selects LOD group,
and invokes native exporters with engine-optimized settings.
"""

import os
import bpy
from ..utils.hierarchy import get_base_name, reset_exploded_view


class LOD_OT_export(bpy.types.Operator):
    """Exporte l'ensemble du groupe de LODs au format FBX ou glTF pour votre moteur de jeu"""
    bl_idname = "lod.export"
    bl_label = "Exporter pour Moteur de Jeu"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        settings = context.scene.lod_generator_settings
        active_obj = context.active_object
        base_name = settings.base_asset_name or get_base_name(active_obj.name)

        # 1. Reset exploded offsets if currently active
        lod_objects = []
        for i in range(10):
            obj = bpy.data.objects.get(f"{base_name}_LOD{i}")
            if obj:
                lod_objects.append(obj)

        if settings.is_exploded:
            reset_exploded_view(lod_objects)
            settings.is_exploded = False

        # 2. Find Empty group and collider
        empty_obj = bpy.data.objects.get(f"LODGroup_{base_name}")
        collider_obj = bpy.data.objects.get(f"UCX_{base_name}_01") or bpy.data.objects.get(f"UBX_{base_name}_01")

        # 3. Resolve export directory
        export_dir = bpy.path.abspath(settings.export_path) if settings.export_path else bpy.path.abspath("//")
        if not export_dir or export_dir.strip() == "":
            export_dir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else os.path.expanduser("~")

        if not os.path.exists(export_dir):
            try:
                os.makedirs(export_dir, exist_ok=True)
            except Exception as err:
                self.report({'ERROR'}, f"Impossible de créer le dossier d'export: {err}")
                return {'CANCELLED'}

        # 4. Select objects to export
        bpy.ops.object.select_all(action='DESELECT')
        export_objs = list(lod_objects)
        if empty_obj:
            export_objs.append(empty_obj)
        if collider_obj:
            export_objs.append(collider_obj)

        for obj in export_objs:
            obj.select_set(True)

        if empty_obj:
            context.view_layer.objects.active = empty_obj
        elif lod_objects:
            context.view_layer.objects.active = lod_objects[0]

        # 5. Export according to selected format
        fmt = settings.export_format
        if fmt == 'FBX':
            filename = f"{base_name}_LODs.fbx"
            filepath = os.path.join(export_dir, filename)

            try:
                bpy.ops.export_scene.fbx(
                    filepath=filepath,
                    use_selection=True,
                    global_scale=1.0,
                    apply_unit_scale=True,
                    apply_scale_options='FBX_SCALE_NONE',
                    bake_space_transform=False,
                    object_types={'EMPTY', 'MESH'},
                    mesh_smooth_type='FACE',
                    add_leaf_bones=False,
                    axis_forward='-Z',
                    axis_up='Y'
                )
            except Exception as err:
                self.report({'ERROR'}, f"Erreur export FBX: {err}")
                return {'CANCELLED'}

        else: # glTF
            filename = f"{base_name}_LODs.glb"
            filepath = os.path.join(export_dir, filename)

            try:
                bpy.ops.export_scene.gltf(
                    filepath=filepath,
                    use_selection=True,
                    export_format='GLB',
                    export_apply=True,
                    export_yup=True
                )
            except Exception as err:
                self.report({'ERROR'}, f"Erreur export glTF: {err}")
                return {'CANCELLED'}

        self.report({'INFO'}, f"Export réussi ({fmt}) : {filepath}")
        return {'FINISHED'}
