"""
User Interface Panels for Procedural LOD Generator.
Displays audit warnings, configuration controls, UV preservation suite,
weight settings, stats breakdown table, and game engine export controls in 3D Viewport N-Panel.
"""

import bpy
from ..utils.hierarchy import get_base_name
from ..utils.math_helpers import get_triangulated_face_count
from ..utils.version import check_version_state, format_version
from . import hud_overlay


class VIEW3D_PT_lod_generator(bpy.types.Panel):
    """Main N-Panel for Procedural LOD Generator"""
    bl_label = "Procedural LOD Generator"
    bl_idname = "VIEW3D_PT_lod_generator"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'LOD Generator'

    def draw_header(self, context):
        self.layout.label(text=f"v{format_version(check_version_state()['loaded'])}")

    def draw(self, context):
        layout = self.layout
        settings = context.scene.lod_generator_settings
        active_obj = context.active_object

        # --- Old version still loaded in memory ---
        version_state = check_version_state()
        if version_state["mismatch"]:
            box_ver = layout.box()
            box_ver.alert = True
            col_ver = box_ver.column(align=True)
            disk = format_version(version_state["disk"])
            loaded = format_version(version_state["loaded"])
            if disk != loaded:
                col_ver.label(text=f"Ancienne version ({loaded}) encore chargée,", icon='ERROR')
                col_ver.label(text=f"la version {disk} est installée.")
            else:
                col_ver.label(text="Des fichiers de l'addon ont changé", icon='ERROR')
                col_ver.label(text="depuis leur chargement en mémoire.")
            col_ver.label(text="Redémarrez Blender ou rechargez l'addon :")
            box_ver.operator("lod.reload_addon", text="Recharger l'addon", icon='FILE_REFRESH')

        # --- 1. Audit & Objet Actif ---
        box_audit = layout.box()
        box_audit.label(text="1. Audit & Objet Source", icon='MESH_DATA')
        if not active_obj or active_obj.type != 'MESH':
            box_audit.label(text="Sélectionnez un objet MESH", icon='INFO')
            return

        row = box_audit.row()
        row.label(text=f"Objet: {active_obj.name}")
        row.label(text=f"{get_triangulated_face_count(active_obj):,} tris", icon='MOD_TRIANGULATE')

        # Scale check
        s = active_obj.scale
        is_scale_uniform = (abs(s.x - 1.0) < 1e-3 and abs(s.y - 1.0) < 1e-3 and abs(s.z - 1.0) < 1e-3)
        if not is_scale_uniform:
            row_warn = box_audit.row()
            row_warn.alert = True
            row_warn.label(text=f"Échelle non appliquée ({s.x:.2f}, {s.y:.2f}, {s.z:.2f})", icon='ERROR')

        # UV check
        if len(active_obj.data.uv_layers) == 0:
            row_uv = box_audit.row()
            row_uv.alert = True
            row_uv.label(text="Aucune carte UV détectée", icon='UV')

        # --- 2. Configuration des LODs ---
        box_cfg = layout.box()
        box_cfg.label(text="2. Paramètres des LODs", icon='MOD_DECIM')
        box_cfg.prop(settings, "lod_count")
        box_cfg.prop(settings, "reduction_preset")

        if settings.reduction_preset == 'CUSTOM':
            col_cust = box_cfg.column(align=True)
            if settings.lod_count >= 1: col_cust.prop(settings, "custom_lod1_ratio")
            if settings.lod_count >= 2: col_cust.prop(settings, "custom_lod2_ratio")
            if settings.lod_count >= 3: col_cust.prop(settings, "custom_lod3_ratio")
            if settings.lod_count >= 4: col_cust.prop(settings, "custom_lod4_ratio")
            if settings.lod_count >= 5: col_cust.prop(settings, "custom_lod5_ratio")
            if settings.lod_count >= 6: col_cust.prop(settings, "custom_lod6_ratio")

        box_cfg.prop(settings, "generation_mode")

        box_cfg.prop(settings, "reduction_mode")
        if settings.reduction_mode == 'ERROR':
            col_err = box_cfg.column(align=True)
            col_err.prop(settings, "max_error")
            col_err.prop(settings, "error_growth")
        elif settings.reduction_mode == 'SCREEN':
            col_err = box_cfg.column(align=True)
            col_err.prop(settings, "pixel_error")
            col_err.prop(settings, "screen_height_px")

        # --- 3. Préservation des UVs & Éradication des Artefacts ---
        box_uv = layout.box()
        box_uv.label(text="3. Protection UVs & Anti-Artefacts", icon='UV')
        col_uv = box_uv.column(align=True)
        col_uv.prop(settings, "pre_triangulate")
        col_uv.prop(settings, "use_delimit_uv")
        col_uv.prop(settings, "uv_border_curvature")
        col_uv.prop(settings, "bvh_repair_uv")
        col_uv.prop(settings, "split_weld_isolation")

        # --- 4. Préservation Topologique & Pondération ---
        box_weights = layout.box()
        box_weights.label(text="4. Carte de Protection (Pondération)", icon='MOD_VERTEX_WEIGHT')
        col_w = box_weights.column(align=True)
        col_w.prop(settings, "preserve_uv_seams", slider=True)
        col_w.prop(settings, "preserve_silhouette", slider=True)
        col_w.prop(settings, "silhouette_angle")
        col_w.prop(settings, "preserve_open_boundaries", slider=True)
        col_w.prop(settings, "preserve_material_borders", slider=True)
        col_w.prop(settings, "preserve_skinning", slider=True)
        box_weights.prop(settings, "relax_weights_per_lod")

        row_vis = box_weights.row()
        row_vis.operator("lod.visualize_mask", text="Visualiser Masque (Weight Paint)", icon='WPAINT_HLT')
        box_weights.prop(settings, "keep_protection_mask")

        # --- 5. Optimisation Avancée ---
        box_opt = layout.box()
        box_opt.label(text="5. Optimisation Avancée", icon='MOD_SIMPLIFY')
        box_opt.prop(settings, "pre_planar_dissolve")
        if settings.pre_planar_dissolve:
            box_opt.prop(settings, "planar_angle_limit")
        box_opt.prop(settings, "cull_small_parts")
        if settings.cull_small_parts:
            box_opt.prop(settings, "cull_threshold")
        box_opt.prop(settings, "remesh_mode")
        if settings.remesh_mode != 'NONE':
            col_rm = box_opt.column(align=True)
            if settings.remesh_mode == 'VOXEL':
                col_rm.prop(settings, "remesh_voxel_pct")
            else:
                col_rm.prop(settings, "remesh_quadriflow_ratio", slider=True)
                col_rm.prop(settings, "remesh_voxel_pct", text="Voxel (repli) %")
            col_rm.prop(settings, "remesh_from_level")

        # --- 6. Ombrage & Sortie ---
        box_out = layout.box()
        box_out.label(text="6. Ombrage & Conditionnement", icon='SHADING_RENDERED')
        box_out.prop(settings, "normal_strategy")
        box_out.prop(settings, "setup_lod_group")
        box_out.prop(settings, "generate_collider")
        if settings.generate_collider:
            box_out.prop(settings, "collider_type")

        box_out.prop(settings, "bake_enabled")
        if settings.bake_enabled:
            col_bk = box_out.column(align=True)
            col_bk.prop(settings, "bake_from_level")
            col_bk.prop(settings, "bake_resolution")
            col_bk.prop(settings, "bake_ao")
            if settings.bake_ao:
                col_bk.prop(settings, "bake_ao_samples")
            col_bk.prop(settings, "bake_dedicated_uv")

        box_out.prop(settings, "impostor_enabled")
        if settings.impostor_enabled:
            col_imp = box_out.column(align=True)
            col_imp.prop(settings, "impostor_views")
            col_imp.prop(settings, "impostor_resolution")
            col_imp.prop(settings, "impostor_engine")

        # --- Action Buttons ---
        if settings.is_generating:
            box_prog = layout.box()
            label = settings.generation_label or "Génération..."
            if hasattr(box_prog, "progress"):
                box_prog.progress(factor=settings.generation_progress, type='BAR', text=label)
            else:
                box_prog.label(text=f"{label} ({settings.generation_progress * 100:.0f}%)", icon='TIME')
            row_cancel = box_prog.row()
            row_cancel.scale_y = 1.2
            row_cancel.operator("lod.cancel_generation", text="Annuler (Échap)", icon='CANCEL')
        else:
            col_action = layout.column(align=True)
            col_action.scale_y = 1.4
            col_action.operator("lod.generate", text="Générer les LODs", icon='PLAY')
        layout.prop(settings, "cancel_keep_completed")

        row_clear = layout.row()
        row_clear.operator("lod.clear_lods", text="Réinitialiser LODs", icon='TRASH')

        # --- 7. Inspection & Viewport ---
        box_view = layout.box()
        box_view.label(text="7. Outils Viewport", icon='VIEW3D')
        row_exp = box_view.row(align=True)
        exp_text = "Vue Empilée (Réaligner)" if settings.is_exploded else "Vue Éclatée (Côte à côte)"
        exp_icon = 'SNAP_GRID' if settings.is_exploded else 'ARROW_LEFTRIGHT'
        row_exp.operator("lod.toggle_exploded_view", text=exp_text, icon=exp_icon)
        row_exp.prop(settings, "explode_spacing", text="Espacement")

        sim_text = "Arrêter Simulation" if settings.is_distance_sim_active else "Simuler Distance Caméra"
        sim_icon = 'PAUSE' if settings.is_distance_sim_active else 'CAMERA_DATA'
        box_view.operator("lod.camera_distance_preview", text=sim_text, icon=sim_icon)

        col_prev = box_view.column(align=True)
        row_prev = col_prev.row(align=True)
        row_prev.prop(settings, "preview_enabled", text="Aperçu en direct", icon='MOD_WIREFRAME', toggle=True)
        row_prev.prop(settings, "preview_level", text="LOD")
        if settings.preview_enabled:
            col_prev.prop(settings, "preview_color", text="Couleur")
            info = hud_overlay.get_preview_info()
            if info["error"]:
                row_err = col_prev.row()
                row_err.alert = True
                row_err.label(text=f"Erreur : {info['error']}", icon='ERROR')
            elif info["computing"] or not info["source_triangles"]:
                col_prev.label(text="Calcul de l'aperçu...", icon='TIME')
            else:
                gain = (1.0 - info["triangles"] / max(1, info["source_triangles"])) * 100.0
                col_prev.label(text=f"LOD{info['level']} : {info['triangles']:,} tris (-{gain:.1f}%)", icon='INFO')

        # --- 8. Tableau Récapitulatif Statistique ---
        if len(settings.stats_items) > 0:
            box_stats = layout.box()
            box_stats.label(text="Statistiques de Réduction & UVs", icon='COMMUNITY')
            
            # Header
            header_row = box_stats.row()
            header_row.label(text="LOD")
            header_row.label(text="Triangles")
            header_row.label(text="Gain %")
            header_row.label(text="Seuil Écran")
            header_row.label(text="Écart")

            for item in settings.stats_items:
                r = box_stats.row()
                r.label(text=item.lod_name)
                r.label(text=f"{item.triangles:,}")
                r.label(text=f"-{item.reduction_pct:.1f}%")
                r.label(text=f"{item.screen_threshold*100:.1f}%")
                if item.lod_level == 0:
                    r.label(text="-")
                elif item.is_impostor:
                    r.label(text="billboard")
                else:
                    sub = r.row()
                    sub.alert = item.error_budget > 0.0 and item.deviation > item.error_budget * 1.001
                    sub.label(text=f"{item.deviation * 1000:.2f} mm")

            # Show UV repair count if any occurred
            total_repaired = sum(item.repaired_uv_faces for item in settings.stats_items)
            if total_repaired > 0:
                row_rep = box_stats.row()
                row_rep.label(text=f"✨ {total_repaired} faces UV réparées par projection BVH", icon='CHECKMARK')

        # --- 9. Export Moteur de Jeu ---
        box_exp = layout.box()
        box_exp.label(text="8. Export Moteur (FBX / glTF)", icon='EXPORT')
        box_exp.prop(settings, "export_format")
        box_exp.prop(settings, "export_path")
        box_exp.scale_y = 1.2
        box_exp.operator("lod.export", text=f"Exporter {settings.export_format}", icon='FILE_TICK')
