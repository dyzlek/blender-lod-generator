"""
Operators for LOD Generation and Topological Mask Visualization.
Coordinates mesh audit, parallel/cascade generation passes, collider setup,
and stats collection.
"""

import time
import bpy
from ..core.analyzer import audit_mesh
from ..core.decimator import iter_generate_lod_mesh, iter_find_ratio_for_error, run_steps, _scaled
from ..core.error_metrics import measure_deviation, level_error_budget
from ..core.baker import bake_lod_maps
from ..core.impostor import generate_impostor
from ..utils.settings_map import pipeline_kwargs
from ..core.weight_mapper import apply_protection_mask_to_object, PROTECTION_VG_NAME
from ..core.collider_gen import generate_convex_hull_collider, generate_box_collider
from ..utils.hierarchy import get_base_name, setup_lod_group_empty, get_or_create_collection, purge_orphan_lod_data
from ..utils.math_helpers import (
    calculate_ratios,
    get_triangulated_face_count,
    default_screen_thresholds,
)


class LOD_OT_generate(bpy.types.Operator):
    """Génère la chaîne complète de LODs procéduraux pour l'objet sélectionné (Échap pour annuler)"""
    bl_idname = "lod.generate"
    bl_label = "Générer les LODs"
    bl_options = {'REGISTER', 'UNDO'}

    # Time slice given to the generation on each modal timer tick (seconds)
    TIME_BUDGET = 0.08

    _timer = None
    _steps = None

    @classmethod
    def poll(cls, context):
        settings = context.scene.lod_generator_settings
        return (
            context.active_object is not None
            and context.active_object.type == 'MESH'
            and not settings.is_generating
        )

    # ------------------------------------------------------------------ setup
    def _prepare(self, context) -> bool:
        """Audits the source and computes everything the generation needs."""
        settings = context.scene.lod_generator_settings
        active_obj = context.active_object

        # Step 1: Audit and Sanity Check
        audit = audit_mesh(active_obj)
        if not audit.is_valid:
            for err in audit.errors:
                self.report({'ERROR'}, f"Audit: {err}")
            return False

        for warn in audit.warnings:
            self.report({'WARNING'}, f"Audit: {warn}")

        # The live preview would recompute on every pass: switch it off
        settings.preview_enabled = False

        self.base_name = get_base_name(active_obj.name)
        active_obj.name = f"{self.base_name}_LOD0"
        self.lod0_obj = active_obj

        # Calculate polycounts
        self.original_triangles = get_triangulated_face_count(self.lod0_obj)
        settings.lod0_initial_triangles = self.original_triangles
        settings.base_asset_name = self.base_name

        # Calculate reduction ratios
        custom_ratios = [
            settings.custom_lod1_ratio,
            settings.custom_lod2_ratio,
            settings.custom_lod3_ratio,
            settings.custom_lod4_ratio,
            settings.custom_lod5_ratio,
            settings.custom_lod6_ratio,
        ]
        self.ratios = calculate_ratios(settings.reduction_preset, settings.lod_count, custom_ratios)
        self.screen_thresholds = default_screen_thresholds(settings.lod_count + 1)

        # Clear stats
        settings.stats_items.clear()

        # Add LOD0 to stats
        item0 = settings.stats_items.add()
        item0.lod_name = self.lod0_obj.name
        item0.lod_level = 0
        item0.triangles = self.original_triangles
        item0.reduction_pct = 0.0
        item0.screen_threshold = self.screen_thresholds[0]
        item0.culled_islands = 0

        self.created_lods = [self.lod0_obj]
        self.last_stats = None
        self.step_times = {}
        self.start_time = time.perf_counter()
        return True

    # ------------------------------------------------------------- generation
    def _iter_generation(self, context):
        """Yields (label, global fraction) for every pass of every LOD level."""
        settings = context.scene.lod_generator_settings
        lod0_obj = self.lod0_obj
        lod_count = settings.lod_count
        prev_obj = lod0_obj

        baked_levels = [k for k in range(1, lod_count + 1)
                        if settings.bake_enabled and k >= settings.bake_from_level]
        # Progress units: one per LOD, one per baked LOD, one for the impostor
        total = lod_count + len(baked_levels) + (1 if settings.impostor_enabled else 0)
        unit = 0
        kwargs = pipeline_kwargs(settings)
        prev_ratio = 1.0

        # Generation loop
        for k in range(1, lod_count + 1):
            lod_name = f"{self.base_name}_LOD{k}"
            prefix = f"LOD{k}/{lod_count}"

            # A previous LOD with this name is only replaced once the new one is complete,
            # so cancelling leaves the levels not yet regenerated untouched
            old_obj = bpy.data.objects.get(lod_name)
            if old_obj == lod0_obj:
                old_obj = None

            source_for_pass = lod0_obj if settings.generation_mode == 'PARALLEL' else prev_obj

            # Error budget / screen size: search the smallest ratio within the budget
            budget = 0.0
            start = 0.0
            if settings.reduction_mode == 'RATIO':
                target_ratio = self.ratios[k - 1]
            else:
                budget = level_error_budget(settings, k, lod0_obj, self.screen_thresholds)
                search = iter_find_ratio_for_error(
                    context, source_for_pass, lod0_obj, budget, k, self.original_triangles,
                    max_ratio=prev_ratio, **kwargs
                )
                target_ratio, _ = yield from self._timed(
                    _scaled(search, "Recherche du ratio (budget d'erreur)", 0.0, 0.5), prefix, unit, total)
                start = 0.5
            prev_ratio = target_ratio

            lod_steps = iter_generate_lod_mesh(
                context=context,
                source_obj=source_for_pass,
                lod0_obj=lod0_obj,
                lod_name=lod_name,
                target_ratio=target_ratio,
                lod_level=k,
                original_lod0_triangles=self.original_triangles,
                **kwargs
            )
            new_lod_obj, stats = yield from self._timed(_remap(lod_steps, start, 1.0), prefix, unit, total)
            unit += 1

            if old_obj is not None:
                _remove_object(old_obj)
                new_lod_obj.name = lod_name
                new_lod_obj.data.name = f"{lod_name}_Mesh"

            self.created_lods.append(new_lod_obj)
            self.last_stats = stats
            prev_obj = new_lod_obj

            stat_entry = settings.stats_items.add()
            stat_entry.lod_name = lod_name
            stat_entry.lod_level = k
            stat_entry.triangles = stats["triangles"]
            stat_entry.reduction_pct = stats["reduction_pct"]
            stat_entry.screen_threshold = self.screen_thresholds[k] if k < len(self.screen_thresholds) else 0.05
            stat_entry.culled_islands = stats["culled_islands"]
            stat_entry.repaired_uv_faces = stats.get("repaired_uvs", 0)
            stat_entry.deviation = measure_deviation(new_lod_obj, lod0_obj)
            stat_entry.error_budget = budget

        # Normal & AO bake on distant LODs
        for k in baked_levels:
            yield f"LOD{k} : bake normales & AO", unit / total
            started = time.perf_counter()
            bake_lod_maps(
                context, self.created_lods[k], lod0_obj,
                resolution=int(settings.bake_resolution),
                bake_ao=settings.bake_ao,
                dedicated_uv=settings.bake_dedicated_uv,
                ao_samples=settings.bake_ao_samples,
            )
            self._add_time("Bake normales & AO", started)
            unit += 1

        # Billboard impostor as the last level
        if settings.impostor_enabled:
            level = lod_count + 1
            name = f"{self.base_name}_LOD{level}"
            yield f"LOD{level} : imposteur ({settings.impostor_views} vues)", unit / total
            started = time.perf_counter()
            old_obj = bpy.data.objects.get(name)
            impostor = generate_impostor(
                context, lod0_obj, name,
                views=int(settings.impostor_views),
                resolution=settings.impostor_resolution,
                engine=settings.impostor_engine,
            )
            if old_obj is not None and old_obj != lod0_obj:
                _remove_object(old_obj)
                impostor.name = name
                impostor.data.name = f"{name}_Mesh"
            self._add_time("Imposteur", started)

            triangles = get_triangulated_face_count(impostor)
            reduction = round((1.0 - triangles / max(1, self.original_triangles)) * 100.0, 1)
            self.created_lods.append(impostor)
            self.last_stats = {"triangles": triangles, "reduction_pct": reduction}
            stat_entry = settings.stats_items.add()
            stat_entry.lod_name = name
            stat_entry.lod_level = level
            stat_entry.triangles = triangles
            stat_entry.reduction_pct = reduction
            stat_entry.screen_threshold = self.screen_thresholds[-1] * 0.5
            stat_entry.is_impostor = True
            unit += 1

        yield "Collider & hiérarchie LodGroup", 1.0
        self._package(context)

    def _add_time(self, label: str, started: float):
        self.step_times[label] = self.step_times.get(label, 0.0) + time.perf_counter() - started

    def _timed(self, steps, prefix: str, unit: int, total: int):
        """Maps one LOD's (label, fraction) onto the whole run and accumulates time per step."""
        label, mark = None, time.perf_counter()
        while True:
            try:
                next_label, fraction = next(steps)
            except StopIteration as stop:
                if label:
                    self._add_time(label, mark)
                return stop.value
            if label:
                self._add_time(label, mark)
            label, mark = next_label, time.perf_counter()
            try:
                yield f"{prefix} : {next_label}", (unit + fraction) / total
            except GeneratorExit:
                steps.close()
                raise

    def _package(self, context):
        """Collider and LodGroup hierarchy for the LODs created so far."""
        settings = context.scene.lod_generator_settings

        # Optional Collider Generation
        collider_obj = None
        if settings.generate_collider:
            if settings.collider_type == 'UCX':
                collider_obj = generate_convex_hull_collider(context, self.lod0_obj, self.base_name)
            else:
                collider_obj = generate_box_collider(context, self.lod0_obj, self.base_name)

        # Setup Engine LodGroup Empty Hierarchy
        if settings.setup_lod_group and len(self.created_lods) > 1:
            setup_lod_group_empty(context, self.base_name, self.created_lods, collider_obj)

    def _timing_summary(self) -> str:
        total = time.perf_counter() - self.start_time
        if not self.step_times:
            return f"{total:.1f} s"
        slowest, seconds = max(self.step_times.items(), key=lambda item: item[1])
        return f"{total:.1f} s, étape la plus longue : {slowest} ({seconds:.1f} s)"

    def _finish(self, context):
        # Materials and maps of replaced LODs are no longer used
        purge_orphan_lod_data(self.base_name)

        # Set active back to LOD0
        context.view_layer.objects.active = self.lod0_obj
        self.lod0_obj.select_set(True)

        stats = self.last_stats
        self.report(
            {'INFO'},
            f"LODs générés avec succès pour '{self.base_name}' ({len(self.created_lods) - 1} niveaux). "
            f"LOD0: {self.original_triangles} tris -> {self.created_lods[-1].name}: {stats['triangles']} tris "
            f"(-{stats['reduction_pct']}%) en {self._timing_summary()}"
        )

    # --------------------------------------------------------------- entries
    def execute(self, context):
        """Synchronous path (scripts, tests, background mode)."""
        if not self._prepare(context):
            return {'CANCELLED'}
        run_steps(self._iter_generation(context))
        self._finish(context)
        return {'FINISHED'}

    def invoke(self, context, event):
        """Interactive path: modal generation with progress bar and cancellation."""
        if context.window is None:
            return self.execute(context)
        if not self._prepare(context):
            return {'CANCELLED'}

        settings = context.scene.lod_generator_settings
        settings.is_generating = True
        settings.cancel_requested = False
        settings.generation_progress = 0.0
        settings.generation_label = "Démarrage..."

        self._steps = self._iter_generation(context)
        wm = context.window_manager
        wm.progress_begin(0, 100)
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        settings = context.scene.lod_generator_settings

        if event.type == 'ESC' or settings.cancel_requested:
            return self._cancel(context)

        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        deadline = time.perf_counter() + self.TIME_BUDGET
        try:
            while True:
                label, fraction = next(self._steps)
                if time.perf_counter() >= deadline:
                    break
        except StopIteration:
            self._end_modal(context)
            self._finish(context)
            return {'FINISHED'}
        except Exception as err:
            self._end_modal(context)
            self._remove_created_lods(context)
            self.report({'ERROR'}, f"Échec de la génération : {err}")
            return {'CANCELLED'}

        settings.generation_label = label
        settings.generation_progress = fraction
        context.window_manager.progress_update(int(fraction * 100))
        _redraw_ui(context)
        return {'RUNNING_MODAL'}

    # ------------------------------------------------------------- teardown
    def _cancel(self, context):
        settings = context.scene.lod_generator_settings
        # Closing the generator removes the half-built LOD (see iter_generate_lod_mesh)
        self._steps.close()
        self._end_modal(context)

        done = len(self.created_lods) - 1
        if settings.cancel_keep_completed and done > 0:
            self._package(context)
            self.report({'WARNING'}, f"Génération annulée : {done} LOD(s) terminé(s) conservé(s).")
        else:
            self._remove_created_lods(context)
            self.report({'WARNING'}, "Génération annulée : aucun LOD conservé.")

        if self.lod0_obj.name in bpy.data.objects:
            context.view_layer.objects.active = self.lod0_obj
        return {'CANCELLED'}

    def _remove_created_lods(self, context):
        settings = context.scene.lod_generator_settings
        for obj in self.created_lods[1:]:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        self.created_lods = self.created_lods[:1]
        purge_orphan_lod_data(self.base_name)
        while len(settings.stats_items) > 1:
            settings.stats_items.remove(len(settings.stats_items) - 1)

    def _end_modal(self, context):
        settings = context.scene.lod_generator_settings
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
            self._timer = None
        wm.progress_end()
        settings.is_generating = False
        settings.cancel_requested = False
        settings.generation_progress = 0.0
        settings.generation_label = ""
        _redraw_ui(context)


class LOD_OT_cancel_generation(bpy.types.Operator):
    """Interrompt la génération de LODs en cours"""
    bl_idname = "lod.cancel_generation"
    bl_label = "Annuler la génération"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.scene.lod_generator_settings.is_generating

    def execute(self, context):
        context.scene.lod_generator_settings.cancel_requested = True
        return {'FINISHED'}


def _remap(steps, start: float, end: float):
    """Re-yields (label, fraction) pairs with the fraction mapped into [start, end]."""
    while True:
        try:
            label, fraction = next(steps)
        except StopIteration as stop:
            return stop.value
        try:
            yield label, start + (end - start) * fraction
        except GeneratorExit:
            steps.close()
            raise


def _remove_object(obj):
    """Removes an object and its mesh when no longer used."""
    mesh = obj.data if obj.type == 'MESH' else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _redraw_ui(context):
    """Redraws the 3D views so the panel's progress bar follows the generation."""
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


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
            old_mesh = obj.data if obj.type == 'MESH' else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if old_mesh and old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)

        settings.stats_items.clear()
        settings.is_exploded = False

        purge_orphan_lod_data(base_name)

        self.report({'INFO'}, f"LODs supprimés pour '{base_name}'.")
        return {'FINISHED'}
