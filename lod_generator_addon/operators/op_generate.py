"""
Operators for LOD Generation and Topological Mask Visualization.
Coordinates mesh audit, parallel/cascade generation passes, collider setup,
and stats collection.
"""

import bpy
from ..core.analyzer import audit_mesh
from ..core.decimator import generate_lod_mesh
from ..core.weight_mapper import apply_protection_mask_to_object, PROTECTION_VG_NAME
from ..core.collider_gen import generate_convex_hull_collider, generate_box_collider
from ..utils.hierarchy import get_base_name, setup_lod_group_empty, get_or_create_collection
from ..utils.math_helpers import (
    calculate_ratios,
    get_triangulated_face_count,
    default_screen_thresholds,
)


class LOD_OT_generate(bpy.types.Operator):
    """Génère la chaîne complète de LODs procéduraux pour l'objet sélectionné"""
    bl_idname = "lod.generate"
    bl_label = "Générer les LODs"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def execute(self, context):
        settings = context.scene.lod_generator_settings
        active_obj = context.active_object

        # Step 1: Audit and Sanity Check
        audit = audit_mesh(active_obj)
        if not audit.is_valid:
            for err in audit.errors:
                self.report({'ERROR'}, f"Audit: {err}")
            return {'CANCELLED'}

        for warn in audit.warnings:
            self.report({'WARNING'}, f"Audit: {warn}")

        base_name = get_base_name(active_obj.name)
        active_obj.name = f"{base_name}_LOD0"
        lod0_obj = active_obj

        # Calculate polycounts
        original_triangles = get_triangulated_face_count(lod0_obj)
        settings.lod0_initial_triangles = original_triangles
        settings.base_asset_name = base_name

        # Calculate reduction ratios
        custom_ratios = [
            settings.custom_lod1_ratio,
            settings.custom_lod2_ratio,
            settings.custom_lod3_ratio,
            settings.custom_lod4_ratio,
            settings.custom_lod5_ratio,
            settings.custom_lod6_ratio,
        ]
        ratios = calculate_ratios(settings.reduction_preset, settings.lod_count, custom_ratios)
        screen_thresholds = default_screen_thresholds(settings.lod_count + 1)

        # Clear stats
        settings.stats_items.clear()

        # Add LOD0 to stats
        item0 = settings.stats_items.add()
        item0.lod_name = lod0_obj.name
        item0.lod_level = 0
        item0.triangles = original_triangles
        item0.reduction_pct = 0.0
        item0.screen_threshold = screen_thresholds[0]
        item0.culled_islands = 0

        created_lods = [lod0_obj]
        prev_obj = lod0_obj

        # Generation loop
        for k in range(1, settings.lod_count + 1):
            lod_name = f"{base_name}_LOD{k}"

            # Remove previous object with this name if exists
            old_obj = bpy.data.objects.get(lod_name)
            if old_obj and old_obj != lod0_obj:
                bpy.data.objects.remove(old_obj, do_unlink=True)

            source_for_pass = lod0_obj if settings.generation_mode == 'PARALLEL' else prev_obj
            target_ratio = ratios[k - 1]

            new_lod_obj, stats = generate_lod_mesh(
                context=context,
                source_obj=source_for_pass,
                lod0_obj=lod0_obj,
                lod_name=lod_name,
                target_ratio=target_ratio,
                lod_level=k,
                original_lod0_triangles=original_triangles,
                pre_triangulate=settings.pre_triangulate,
                use_delimit_uv=settings.use_delimit_uv,
                uv_border_curvature=settings.uv_border_curvature,
                bvh_repair_uv=settings.bvh_repair_uv,
                split_weld_isolation=settings.split_weld_isolation,
                preserve_uv=settings.preserve_uv_seams,
                preserve_silhouette=settings.preserve_silhouette,
                silhouette_angle_rad=settings.silhouette_angle,
                preserve_boundary=settings.preserve_open_boundaries,
                preserve_material=settings.preserve_material_borders,
                preserve_skinning=settings.preserve_skinning,
                relax_weights=settings.relax_weights_per_lod,
                pre_planar_dissolve=settings.pre_planar_dissolve,
                planar_angle_limit_rad=settings.planar_angle_limit,
                cull_small_parts=settings.cull_small_parts,
                cull_threshold_pct=settings.cull_threshold,
                normal_strategy=settings.normal_strategy,
                keep_protection_mask=settings.keep_protection_mask
            )

            created_lods.append(new_lod_obj)
            prev_obj = new_lod_obj

            stat_entry = settings.stats_items.add()
            stat_entry.lod_name = lod_name
            stat_entry.lod_level = k
            stat_entry.triangles = stats["triangles"]
            stat_entry.reduction_pct = stats["reduction_pct"]
            stat_entry.screen_threshold = screen_thresholds[k] if k < len(screen_thresholds) else 0.05
            stat_entry.culled_islands = stats["culled_islands"]
            stat_entry.repaired_uv_faces = stats.get("repaired_uvs", 0)

        # Optional Collider Generation
        collider_obj = None
        if settings.generate_collider:
            if settings.collider_type == 'UCX':
                collider_obj = generate_convex_hull_collider(context, lod0_obj, base_name)
            else:
                collider_obj = generate_box_collider(context, lod0_obj, base_name)

        # Setup Engine LodGroup Empty Hierarchy
        empty_group = None
        if settings.setup_lod_group:
            empty_group = setup_lod_group_empty(context, base_name, created_lods, collider_obj)

        # Set active back to LOD0
        context.view_layer.objects.active = lod0_obj
        lod0_obj.select_set(True)

        final_tris = created_lods[-1].data.polygons
        self.report(
            {'INFO'},
            f"LODs générés avec succès pour '{base_name}' ({len(created_lods) - 1} niveaux). "
            f"LOD0: {original_triangles} tris -> {created_lods[-1].name}: {stats['triangles']} tris (-{stats['reduction_pct']}%)"
        )
        return {'FINISHED'}


class LOD_OT_visualize_mask(bpy.types.Operator):
    """Calcule et affiche le masque de protection en mode Weight Paint (Rouge = Protégé, Bleu = Sacrifiable)"""
    bl_idname = "lod.visualize_mask"
    bl_label = "Visualiser le Masque de Protection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def execute(self, context):
        settings = context.scene.lod_generator_settings
        active_obj = context.active_object

        vg = apply_protection_mask_to_object(
            obj=active_obj,
            preserve_uv=settings.preserve_uv_seams,
            preserve_silhouette=settings.preserve_silhouette,
            silhouette_angle_rad=settings.silhouette_angle,
            preserve_boundary=settings.preserve_open_boundaries,
            preserve_material=settings.preserve_material_borders,
            preserve_skinning=settings.preserve_skinning,
            lod_level=1,
            relax_weights=False
        )

        # Set vertex group active
        active_obj.vertex_groups.active_index = vg.index

        # Switch to WEIGHT_PAINT mode
        try:
            bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
        except Exception:
            pass

        self.report(
            {'INFO'},
            f"Masque '{PROTECTION_VG_NAME}' généré. Rouge = Fortement Protégé (1.0), Bleu = Sacrifiable (0.0)."
        )
        return {'FINISHED'}


class LOD_OT_clear_lods(bpy.types.Operator):
    """Supprime les niveaux LOD générés et réinitialise l'objet source"""
    bl_idname = "lod.clear_lods"
    bl_label = "Nettoyer les LODs"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        settings = context.scene.lod_generator_settings
        active_obj = context.active_object
        base_name = get_base_name(active_obj.name)

        # Find and delete LOD1 to LOD6 and colliders
        to_delete = []
        for i in range(1, 10):
            obj = bpy.data.objects.get(f"{base_name}_LOD{i}")
            if obj:
                to_delete.append(obj)
        
        collider = bpy.data.objects.get(f"UCX_{base_name}_01") or bpy.data.objects.get(f"UBX_{base_name}_01")
        if collider:
            to_delete.append(collider)

        empty_obj = bpy.data.objects.get(f"LODGroup_{base_name}")
        if empty_obj:
            to_delete.append(empty_obj)

        for obj in to_delete:
            bpy.data.objects.remove(obj, do_unlink=True)

        settings.stats_items.clear()
        settings.is_exploded = False

        self.report({'INFO'}, f"LODs supprimés pour '{base_name}'.")
        return {'FINISHED'}
