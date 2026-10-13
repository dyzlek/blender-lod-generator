"""
Viewport Inspection and Interactive Distance Simulation Operators.
Provides side-by-side Exploded View and real-time Camera Distance simulation modal loop.
"""

import math
import bpy
from ..utils.hierarchy import get_base_name, apply_exploded_view, reset_exploded_view
from ..utils.math_helpers import calculate_bounding_sphere, calculate_screen_size


class LOD_OT_toggle_exploded_view(bpy.types.Operator):
    """Bascule entre la vue éclatée côte-à-côte (comparaison visuelle) et la vue empilée (export)"""
    bl_idname = "lod.toggle_exploded_view"
    bl_label = "Basculer Vue Éclatée"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        settings = context.scene.lod_generator_settings
        active_obj = context.active_object
        base_name = get_base_name(active_obj.name)

        # Collect all LODs
        lod_objects = []
        for i in range(10):
            obj = bpy.data.objects.get(f"{base_name}_LOD{i}")
            if obj:
                lod_objects.append(obj)

        if len(lod_objects) <= 1:
            self.report({'WARNING'}, "Aucun LOD secondaire trouvé pour la vue éclatée.")
            return {'CANCELLED'}

        if not settings.is_exploded:
            apply_exploded_view(lod_objects, settings.explode_spacing)
            settings.is_exploded = True
            self.report({'INFO'}, f"Vue éclatée activée (espacement x{settings.explode_spacing:.1f}).")
        else:
            reset_exploded_view(lod_objects)
            settings.is_exploded = False
            self.report({'INFO'}, "Vue réalignée à l'origine (0, 0, 0).")

        return {'FINISHED'}


class LOD_OT_camera_distance_preview(bpy.types.Operator):
    """Simule en direct dans la 3D View le changement dynamique de LOD selon la distance de la caméra"""
    bl_idname = "lod.camera_distance_preview"
    bl_label = "Simuler Distance Caméra (Modal)"
    bl_options = {'REGISTER'}

    _timer = None
    _active_instance = None

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def modal(self, context, event):
        settings = context.scene.lod_generator_settings

        # Check for exit condition
        if event.type in {'ESC', 'RIGHTMOUSE'} or not settings.is_distance_sim_active:
            self.cancel(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            self.update_lod_visibility(context)

        return {'PASS_THROUGH'}

    def update_lod_visibility(self, context):
        settings = context.scene.lod_generator_settings
        base_name = settings.base_asset_name or get_base_name(context.active_object.name)

        lod_objects = []
        for i in range(10):
            obj = bpy.data.objects.get(f"{base_name}_LOD{i}")
            if obj:
                lod_objects.append(obj)

        if not lod_objects:
            return

        lod0 = lod_objects[0]
        _, radius = calculate_bounding_sphere(lod0)

        # Get 3D Viewport camera position
        r3d = context.space_data.region_3d if context.space_data and hasattr(context.space_data, 'region_3d') else None
        if not r3d:
            return

        view_mat = r3d.view_matrix
        cam_pos = view_mat.inverted().translation
        obj_pos = lod0.matrix_world.translation

        distance = (cam_pos - obj_pos).length
        screen_size = calculate_screen_size(radius, distance)

        # Determine which LOD should be active based on screen size thresholds
        thresholds = [item.screen_threshold for item in settings.stats_items]
        if not thresholds:
            thresholds = [0.5, 0.25, 0.12, 0.05]

        active_idx = 0
        for idx, thresh in enumerate(thresholds):
            if screen_size < thresh and idx < len(lod_objects):
                active_idx = idx

        active_idx = min(active_idx, len(lod_objects) - 1)

        # Apply viewport visibility
        for idx, obj in enumerate(lod_objects):
            obj.hide_viewport = (idx != active_idx)

        # Report in viewport header status
        context.workspace.status_text_set(
            f"[LOD Preview] Actif: {lod_objects[active_idx].name} | Dist: {distance:.2f}m | ScreenSize: {screen_size*100:.1f}% | [ESC pour Quitter]"
        )

    def execute(self, context):
        settings = context.scene.lod_generator_settings

        if settings.is_distance_sim_active:
            # Toggle off
            settings.is_distance_sim_active = False
            return {'FINISHED'}

        settings.is_distance_sim_active = True
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        self.report({'INFO'}, "Simulation de distance caméra démarrée. Déplacez la vue 3D. [ESC pour quitter]")
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        settings = context.scene.lod_generator_settings
        settings.is_distance_sim_active = False
        context.workspace.status_text_set(None)

        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

        # Restore visibility of all LODs
        base_name = settings.base_asset_name or (get_base_name(context.active_object.name) if context.active_object else "")
        for i in range(10):
            obj = bpy.data.objects.get(f"{base_name}_LOD{i}")
            if obj:
                obj.hide_viewport = False

        self.report({'INFO'}, "Simulation de distance arrêtée. Visibilité rétablie.")
