"""
Decimator Pipeline Module for Procedural LOD Generator.
"""

import math
import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform
from .analyzer import find_connected_islands
from .weight_mapper import (
    apply_protection_mask_to_object,
    PROTECTION_VG_NAME,
    mark_uv_discontinuities_as_seams
)
from ..utils.math_helpers import (
    clamp,
    get_triangulated_face_count,
    calculate_coords_bounding_sphere,
    calculate_uv_signed_area,
    calculate_triangle_3d_area,
)


def cull_small_islands_from_object(obj: bpy.types.Object, threshold_pct: float) -> int:
    """
    Identifies and removes detached sub-elements whose relative bounding radius
    is smaller than threshold_pct % of the overall mesh.
    """
    if not obj or obj.type != 'MESH' or threshold_pct <= 0.0:
        return 0

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    if len(bm.verts) == 0:
        bm.free()
        return 0

    coords = [v.co for v in bm.verts]
    _, global_radius = calculate_coords_bounding_sphere(coords)
    if global_radius <= 1e-5:
        bm.free()
        return 0

    islands = find_connected_islands(bm)
    if len(islands) <= 1:
        bm.free()
        return 0

    culled_count = 0
    verts_to_delete = []
    ratio_threshold = threshold_pct / 100.0

    for island in islands:
        island_coords = [v.co for v in island]
        _, island_radius = calculate_coords_bounding_sphere(island_coords)
        relative_size = island_radius / global_radius

        if relative_size < ratio_threshold:
            culled_count += 1
            verts_to_delete.extend(island)

    if verts_to_delete:
        bmesh.ops.delete(bm, geom=verts_to_delete, context='VERTS')
        bm.to_mesh(obj.data)
        obj.data.update()

    bm.free()
    return culled_count


def find_nearest_same_side(bvh: BVHTree, co: Vector, ref_normal: Vector, max_radius: float):
    """
    BVH nearest-point query restricted to faces oriented like ref_normal.
    On thin double-walled meshes (balloons, bottles, solidified shells) the plain
    nearest face is often the opposite wall, whose normal points the other way:
    sampling it projects vertices onto the wrong wall and inverts shading.
    Returns (location, normal, index, distance), or Nones when nothing matches.
    """
    hit = bvh.find_nearest(co)
    if hit[0] is None or ref_normal.length == 0.0 or hit[1].dot(ref_normal) > 0.0:
        return hit

    radius = max(hit[3] * 2.0, 1e-4)
    while radius <= max_radius:
        candidates = [h for h in bvh.find_nearest_range(co, radius) if h[1].dot(ref_normal) > 0.0]
        if candidates:
            return min(candidates, key=lambda h: h[3])
        radius *= 2.0
    return None, None, None, None


# Number of faces processed between two progress yields in long Python loops
PROGRESS_BATCH = 2000


def run_steps(steps):
    """Runs a step generator to completion and returns its final value."""
    while True:
        try:
            next(steps)
        except StopIteration as stop:
            return stop.value


def _search_radius(obj: bpy.types.Object) -> float:
    """Maximum search distance for same-side queries (a quarter of the bounding radius)."""
    _, radius = calculate_coords_bounding_sphere([v.co for v in obj.data.vertices])
    return max(radius * 0.25, 1e-4)


def unfold_and_harmonize_shading(target_obj: bpy.types.Object, lod0_obj: bpy.types.Object) -> int:
    """
    Unfolds geometrically inverted triangles (foldovers) by relaxing their vertices
    and re-projecting them onto the same-side LOD0 surface, then harmonizes Smooth
    Shading with LOD0. Winding is never recalculated: decimation preserves it, and a
    global recalculation flips intentionally inward-facing shells (inner walls).
    """
    if not target_obj or not lod0_obj:
        return 0

    lod0_is_smooth = any(p.use_smooth for p in lod0_obj.data.polygons)

    bm0 = bmesh.new()
    bm0.from_mesh(lod0_obj.data)
    bm0.faces.ensure_lookup_table()
    bvh0 = BVHTree.FromBMesh(bm0)
    max_radius = _search_radius(lod0_obj)

    bm = bmesh.new()
    bm.from_mesh(target_obj.data)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()

    # 1. Remove zero-area degenerate faces
    degenerate_faces = [f for f in bm.faces if f.calc_area() <= 1e-8]
    if degenerate_faces:
        bmesh.ops.delete(bm, geom=degenerate_faces, context='FACES')
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
    bm.normal_update()

    # 2. Detect folded faces topologically: a foldover points against its edge neighbours.
    # (Comparing with the nearest LOD0 face is unreliable on double-walled meshes,
    # where the nearest face may belong to the opposite wall.)
    folded_faces = set()
    for f in bm.faces:
        f.smooth = lod0_is_smooth
        dots = [f.normal.dot(other.normal) for e in f.edges for other in e.link_faces if other is not f]
        if dots and sum(dots) / len(dots) < -0.2:
            folded_faces.add(f)

    folded_verts = {v for f in folded_faces for v in f.verts}

    if folded_verts:
        for _ in range(2):
            new_positions = {}
            for v in folded_verts:
                neighbors = [e.other_vert(v).co for e in v.link_edges]
                if not neighbors:
                    continue
                avg_co = sum(neighbors, Vector((0.0, 0.0, 0.0))) / len(neighbors)
                relaxed = v.co.lerp(avg_co, 0.5)
                ref_normal = sum((f.normal for f in v.link_faces if f not in folded_faces),
                                 Vector((0.0, 0.0, 0.0)))
                loc, _, _, _ = find_nearest_same_side(bvh0, relaxed, ref_normal, max_radius)
                new_positions[v] = loc if loc is not None else relaxed
            for v, pos in new_positions.items():
                v.co = pos
            bm.normal_update()

    bm.to_mesh(target_obj.data)
    target_obj.data.update()

    bm0.free()
    bm.free()
    return len(folded_verts)


def transfer_lod0_normals(target_obj: bpy.types.Object, lod0_obj: bpy.types.Object) -> int:
    """Synchronous wrapper of iter_transfer_lod0_normals."""
    return run_steps(iter_transfer_lod0_normals(target_obj, lod0_obj))


def iter_transfer_lod0_normals(target_obj: bpy.types.Object, lod0_obj: bpy.types.Object):
    """
    Transfers LOD0 corner normals onto the LOD as custom split normals, sampling only
    LOD0 faces oriented like each target face (barycentric interpolation on the hit
    triangle). Replaces the Data Transfer modifier, whose nearest-face mapping picks
    the opposite wall on thin double-walled meshes and paints dark inverted patches.
    Yields progress fractions (0..1) every PROGRESS_BATCH faces and returns the
    number of corners that fell back to their own face normal.
    """
    if not target_obj or not lod0_obj:
        return 0

    me0 = lod0_obj.data
    corner_normals0 = [cn.vector.copy() for cn in me0.corner_normals]

    bm0 = bmesh.new()
    bm0.from_mesh(me0)
    bm0.faces.ensure_lookup_table()
    nor_layer = bm0.loops.layers.float_vector.new("_lod_corner_normal")
    for f, poly in zip(bm0.faces, me0.polygons):
        for loop, loop_index in zip(f.loops, poly.loop_indices):
            loop[nor_layer] = corner_normals0[loop_index]
    bmesh.ops.triangulate(bm0, faces=bm0.faces)
    bm0.faces.ensure_lookup_table()
    bm0.normal_update()
    bvh0 = BVHTree.FromBMesh(bm0)
    max_radius = _search_radius(lod0_obj)

    me = target_obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bm.normal_update()

    normals = [Vector((0.0, 0.0, 1.0))] * len(me.loops)
    fallbacks = 0
    total_faces = max(1, len(bm.faces))
    try:
        for face_index, (f, poly) in enumerate(zip(bm.faces, me.polygons)):
            if face_index % PROGRESS_BATCH == 0:
                yield face_index / total_faces
            center = f.calc_center_median()
            for loop, loop_index in zip(f.loops, poly.loop_indices):
                co = loop.vert.co
                # Sample slightly inside the face so hard edges pick the face on the right side
                _, _, idx, _ = find_nearest_same_side(bvh0, co.lerp(center, 0.1), f.normal, max_radius)
                if idx is None:
                    normals[loop_index] = f.normal.copy()
                    fallbacks += 1
                    continue
                tri = bm0.faces[idx]
                n = barycentric_transform(
                    co,
                    tri.verts[0].co, tri.verts[1].co, tri.verts[2].co,
                    tri.loops[0][nor_layer], tri.loops[1][nor_layer], tri.loops[2][nor_layer],
                )
                if n.length < 1e-6 or n.dot(f.normal) <= 0.0:
                    n = f.normal.copy()
                    fallbacks += 1
                normals[loop_index] = n.normalized()
    finally:
        bm0.free()
        bm.free()

    me.normals_split_custom_set(normals)
    me.update()
    return fallbacks


def repair_corrupted_uvs_via_bvh(target_obj: bpy.types.Object, lod0_obj: bpy.types.Object) -> int:
    """Synchronous wrapper of iter_repair_corrupted_uvs_via_bvh."""
    return run_steps(iter_repair_corrupted_uvs_via_bvh(target_obj, lod0_obj))


def iter_repair_corrupted_uvs_via_bvh(target_obj: bpy.types.Object, lod0_obj: bpy.types.Object):
    """
    Scans the decimated mesh for strictly degenerate UV faces (zero UV area)
    and re-projects barycentric UVs using the exact hit face on a triangulated LOD0 BVHTree.
    Yields progress fractions (0..1) and returns the number of repaired faces.
    """
    if not target_obj or not lod0_obj or not target_obj.data.uv_layers:
        return 0

    bm0 = bmesh.new()
    bm0.from_mesh(lod0_obj.data)
    bmesh.ops.triangulate(bm0, faces=bm0.faces)
    bm0.faces.ensure_lookup_table()
    bm0.verts.ensure_lookup_table()

    uv_layer0 = bm0.loops.layers.uv.active
    if not uv_layer0:
        bm0.free()
        return 0

    bvh0 = BVHTree.FromBMesh(bm0)

    max_radius = _search_radius(lod0_obj)

    bm_target = bmesh.new()
    bm_target.from_mesh(target_obj.data)
    bm_target.faces.ensure_lookup_table()
    bm_target.verts.ensure_lookup_table()
    bm_target.normal_update()

    uv_layer_target = bm_target.loops.layers.uv.active
    if not uv_layer_target:
        bm0.free()
        bm_target.free()
        return 0

    repaired_count = 0
    total_faces = max(1, len(bm_target.faces))
    try:
        for face_index, f in enumerate(bm_target.faces):
            if face_index % PROGRESS_BATCH == 0:
                yield face_index / total_faces
            if len(f.verts) != 3:
                continue

            v0, v1, v2 = f.verts[0].co, f.verts[1].co, f.verts[2].co
            lp0, lp1, lp2 = f.loops[0], f.loops[1], f.loops[2]
            uv0, uv1, uv2 = lp0[uv_layer_target].uv, lp1[uv_layer_target].uv, lp2[uv_layer_target].uv

            area_uv = abs(calculate_uv_signed_area(uv0, uv1, uv2))
            area_3d = calculate_triangle_3d_area(v0, v1, v2)

            max_uv_edge = max((uv1 - uv0).length, (uv2 - uv1).length, (uv0 - uv2).length)
            is_barcode = (max_uv_edge > 1e-3 and (2.0 * area_uv / (max_uv_edge ** 2)) < 1e-6)
            is_zero_uv = (area_uv < 1e-11 and area_3d > 1e-5)

            if is_barcode or is_zero_uv:
                face_center = (v0 + v1 + v2) / 3.0
                for loop in f.loops:
                    # Sample slightly inward toward face center and use the EXACT hit face (hit_face_idx)
                    sample_pt = loop.vert.co.lerp(face_center, 0.15)
                    loc, _, hit_face_idx, _ = find_nearest_same_side(bvh0, sample_pt, f.normal, max_radius)
                    if loc is not None and hit_face_idx is not None and hit_face_idx < len(bm0.faces):
                        f_hit = bm0.faces[hit_face_idx]
                        pA, pB, pC = f_hit.verts[0].co, f_hit.verts[1].co, f_hit.verts[2].co
                        uvA = Vector((f_hit.loops[0][uv_layer0].uv.x, f_hit.loops[0][uv_layer0].uv.y, 0.0))
                        uvB = Vector((f_hit.loops[1][uv_layer0].uv.x, f_hit.loops[1][uv_layer0].uv.y, 0.0))
                        uvC = Vector((f_hit.loops[2][uv_layer0].uv.x, f_hit.loops[2][uv_layer0].uv.y, 0.0))

                        uv_repaired = barycentric_transform(loc, pA, pB, pC, uvA, uvB, uvC)
                        loop[uv_layer_target].uv = uv_repaired.xy

                repaired_count += 1

        if repaired_count > 0:
            bm_target.to_mesh(target_obj.data)
            target_obj.data.update()
    finally:
        bm0.free()
        bm_target.free()
    return repaired_count


def clear_custom_normals_safely(context, obj: bpy.types.Object):
    """Clears any pre-existing custom split normals before topological decimation."""
    if not obj or obj.type != 'MESH' or not obj.data.has_custom_normals:
        return
    prev_active = context.view_layer.objects.active
    context.view_layer.objects.active = obj
    obj.select_set(True)
    try:
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    except Exception:
        pass
    context.view_layer.objects.active = prev_active


def apply_modifier_safely(context, obj: bpy.types.Object, mod_name: str) -> bool:
    """Safely applies a modifier on an object in the current scene view layer."""
    if not obj or mod_name not in obj.modifiers:
        return False

    prev_active = context.view_layer.objects.active
    context.view_layer.objects.active = obj
    obj.select_set(True)

    if obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    success = True
    try:
        with context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_apply(modifier=mod_name)
    except Exception as err:
        print(f"[LOD Generator] Failed to apply modifier '{mod_name}' on '{obj.name}': {err}")
        success = False

    context.view_layer.objects.active = prev_active
    return success


def _scaled(steps, label: str, start: float, end: float):
    """Re-yields a sub-generator's 0..1 fractions as (label, fraction) mapped into [start, end]."""
    while True:
        try:
            fraction = next(steps)
        except StopIteration as stop:
            return stop.value
        try:
            yield label, start + (end - start) * fraction
        except GeneratorExit:
            steps.close()
            raise


def generate_lod_mesh(*args, **kwargs) -> tuple[bpy.types.Object, dict]:
    """Synchronous wrapper of iter_generate_lod_mesh (scripting, tests, Redo panel)."""
    return run_steps(iter_generate_lod_mesh(*args, **kwargs))


def iter_generate_lod_mesh(
    context,
    source_obj: bpy.types.Object,
    lod0_obj: bpy.types.Object,
    lod_name: str,
    target_ratio: float,
    lod_level: int,
    original_lod0_triangles: int,
    pre_triangulate: bool = True,
    use_delimit_uv: bool = True,
    uv_border_curvature: bool = True,
    bvh_repair_uv: bool = True,
    split_weld_isolation: bool = False,
    preserve_uv: float = 0.85,
    preserve_silhouette: float = 0.75,
    silhouette_angle_rad: float = math.radians(35.0),
    preserve_boundary: float = 1.0,
    preserve_material: float = 0.90,
    preserve_skinning: float = 0.70,
    relax_weights: bool = True,
    pre_planar_dissolve: bool = False,
    planar_angle_limit_rad: float = math.radians(0.5),
    cull_small_parts: bool = True,
    cull_threshold_pct: float = 2.5,
    normal_strategy: str = 'TRANSFER',
    keep_protection_mask: bool = False
):
    """
    Executes the complete generation of a single LOD level from a source mesh.
    Generator: yields (step label, fraction 0..1 of this LOD) between passes so a
    modal operator can show progress and cancel; returns (lod_object, stats).
    If the generator is closed (cancel) or a pass raises, the partial LOD object
    and its mesh are removed so nothing is left behind.
    """
    # 1. Clean duplication (keep identical local space with lod0_obj)
    new_mesh = source_obj.data.copy()
    new_mesh.name = f"{lod_name}_Mesh"
    new_obj = source_obj.copy()
    new_obj.data = new_mesh
    new_obj.name = lod_name
    new_obj.modifiers.clear()

    context.collection.objects.link(new_obj)
    try:
        yield "Préparation", 0.02
        # Clear pre-existing custom normals so BMesh operations don't corrupt tangent loop normals
        clear_custom_normals_safely(context, new_obj)

        yield "Pré-triangulation", 0.05
        # 2. Pass 0 — Clean Pre-Triangulation
        if pre_triangulate:
            mod_tri = new_obj.modifiers.new(name="LOD_Pre_Triangulate", type='TRIANGULATE')
            mod_tri.quad_method = 'BEAUTY'
            mod_tri.ngon_method = 'CLIP'
            mod_tri.keep_custom_normals = False
            apply_modifier_safely(context, new_obj, mod_tri.name)

        yield "Culling des petits fragments", 0.1
        # 3. Geometry Culling for small detached components on distant LODs
        culled_islands = 0
        if cull_small_parts and lod_level >= 2:
            effective_cull_pct = cull_threshold_pct
            if lod_level >= 3:
                effective_cull_pct = max(effective_cull_pct, 5.0)
            culled_islands = cull_small_islands_from_object(new_obj, effective_cull_pct)

        yield "Dissolution planaire", 0.12
        # 4. Pass 1 — Coplanar Face Dissolve (Planar Pre-Decimate)
        if pre_planar_dissolve:
            mod_planar = new_obj.modifiers.new(name="LOD_Planar_Dissolve", type='DECIMATE')
            mod_planar.decimate_type = 'DISSOLVE'
            mod_planar.angle_limit = planar_angle_limit_rad
            mod_planar.delimit = {'UV', 'MATERIAL', 'SEAM', 'SHARP'}
            apply_modifier_safely(context, new_obj, mod_planar.name)

        yield "Marquage des coutures UV", 0.15
        # 5. Pre-mark UV discontinuities as seams & weld sawtooth pole UVs
        if use_delimit_uv and new_obj.data.uv_layers:
            bm_seam = bmesh.new()
            bm_seam.from_mesh(new_obj.data)
            mark_uv_discontinuities_as_seams(bm_seam)
            bm_seam.to_mesh(new_obj.data)
            new_obj.data.update()
            bm_seam.free()

        # 6. Optional Split-Decimate-Weld Physical Isolation
        if split_weld_isolation:
            bm_split = bmesh.new()
            bm_split.from_mesh(new_obj.data)
            bm_split.edges.ensure_lookup_table()
            seam_edges = [e for e in bm_split.edges if e.seam]
            if seam_edges:
                bmesh.ops.split_edges(bm_split, edges=seam_edges)
                bm_split.to_mesh(new_obj.data)
                new_obj.data.update()
            bm_split.free()

        yield "Carte de protection", 0.2
        # 7. Generate Procedural Protection Weight Mask
        apply_protection_mask_to_object(
            obj=new_obj,
            preserve_uv=preserve_uv,
            preserve_silhouette=preserve_silhouette,
            silhouette_angle_rad=silhouette_angle_rad,
            preserve_boundary=preserve_boundary,
            preserve_material=preserve_material,
            preserve_skinning=preserve_skinning,
            uv_border_curvature=uv_border_curvature,
            lod_level=lod_level,
            relax_weights=relax_weights
        )

        yield "Décimation QEM", 0.35
        # 8. Pass 2 — QEM Guided Collapse Decimation with Delimit Barrier
        target_triangles = max(8, int(original_lod0_triangles * target_ratio))
        current_triangles = get_triangulated_face_count(new_obj)

        if current_triangles > target_triangles:
            eff_collapse_ratio = clamp(target_triangles / max(1, current_triangles), 0.002, 1.0)
            mod_qem = new_obj.modifiers.new(name="LOD_QEM_Collapse", type='DECIMATE')
            mod_qem.decimate_type = 'COLLAPSE'
            mod_qem.ratio = eff_collapse_ratio
            mod_qem.use_collapse_triangulate = False
            mod_qem.vertex_group = PROTECTION_VG_NAME
            mod_qem.invert_vertex_group = True
            mod_qem.vertex_group_factor = 0.50

            if use_delimit_uv:
                mod_qem.delimit = {'UV', 'MATERIAL', 'SEAM'}

            apply_modifier_safely(context, new_obj, mod_qem.name)

        # 9. Re-weld after Split if isolation was active
        if split_weld_isolation:
            bm_weld = bmesh.new()
            bm_weld.from_mesh(new_obj.data)
            bm_weld.verts.ensure_lookup_table()
            bmesh.ops.remove_doubles(bm_weld, verts=bm_weld.verts, dist=0.00001)
            bm_weld.to_mesh(new_obj.data)
            new_obj.data.update()
            bm_weld.free()

        yield "Dépliage des faces repliées", 0.6
        # 10. Unfold any folded triangles and harmonize manifold winding & smooth shading
        if lod0_obj:
            unfold_and_harmonize_shading(new_obj, lod0_obj)

        # 11. Pass 3 — Post-Decimation UV Repair via BVH Tree
        repaired_uvs = 0
        if bvh_repair_uv and lod0_obj:
            repaired_uvs = yield from _scaled(
                iter_repair_corrupted_uvs_via_bvh(new_obj, lod0_obj), "Réparation des UVs", 0.66, 0.78)

        # Validate mesh BEFORE Data Transfer so custom split normals are written onto valid loops
        new_obj.data.validate()
        new_obj.data.update()

        yield "Restauration des normales", 0.8
        # 12. Pass 4 — Shading Restoration (Custom Normal Transfer)
        if normal_strategy == 'TRANSFER' and lod0_obj:
            yield from _scaled(
                iter_transfer_lod0_normals(new_obj, lod0_obj), "Transfert des normales", 0.80, 0.98)

        elif normal_strategy == 'WEIGHTED':
            mod_wn = new_obj.modifiers.new(name="LOD_Weighted_Normal", type='WEIGHTED_NORMAL')
            mod_wn.keep_sharp = True
            apply_modifier_safely(context, new_obj, mod_wn.name)

        # 13. Clean up temporary vertex group
        if not keep_protection_mask and PROTECTION_VG_NAME in new_obj.vertex_groups:
            vg = new_obj.vertex_groups.get(PROTECTION_VG_NAME)
            new_obj.vertex_groups.remove(vg)

        new_obj.data.update()

        final_triangles = get_triangulated_face_count(new_obj)
        reduction_pct = (1.0 - (final_triangles / max(1, original_lod0_triangles))) * 100.0

        stats = {
            "lod_level": lod_level,
            "lod_name": lod_name,
            "triangles": final_triangles,
            "target_triangles": target_triangles,
            "reduction_pct": round(reduction_pct, 1),
            "culled_islands": culled_islands,
            "repaired_uvs": repaired_uvs
        }

        yield "Terminé", 1.0
        return new_obj, stats
    except BaseException:
        # Cancel (GeneratorExit) or failure: never leave a half-built LOD behind
        try:
            bpy.data.objects.remove(new_obj, do_unlink=True)
        except ReferenceError:
            pass
        try:
            if new_mesh.users == 0:
                bpy.data.meshes.remove(new_mesh)
        except ReferenceError:
            pass
        raise

# Alias de retrocompatibilite
fix_flipped_faces_and_smoothing = unfold_and_harmonize_shading
