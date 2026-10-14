r"""
Automated Test Suite for Procedural LOD Generator Add-on.
Run in headless Blender:
& "C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe" --background --python "c:\Users\dylan\Documents\blender\test_lod_generator.py"
"""

import sys
import os
import math
import bpy
import bmesh
from mathutils import Vector

# Ensure addon directory is on sys.path
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
ADDON_DIR = REPO_ROOT
for p in [REPO_ROOT, TESTS_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

print("=" * 70)
print("  DÉBUT DES TESTS UNITAIRES : PROCEDURAL LOD GENERATOR")
print("=" * 70)


def test_registration():
    print("\n--- Test 1: Enregistrement de l'Add-on ---")
    import lod_generator_addon
    lod_generator_addon.register()

    assert hasattr(bpy.types.Scene, "lod_generator_settings"), "Scene.lod_generator_settings non trouvé !"
    assert hasattr(bpy.ops, "lod"), "bpy.ops.lod non enregistré !"
    assert hasattr(bpy.ops.lod, "generate"), "Opérateur lod.generate manquant !"
    assert hasattr(bpy.ops.lod, "visualize_mask"), "Opérateur lod.visualize_mask manquant !"
    assert hasattr(bpy.ops.lod, "clear_lods"), "Opérateur lod.clear_lods manquant !"
    assert hasattr(bpy.ops.lod, "toggle_exploded_view"), "Opérateur lod.toggle_exploded_view manquant !"
    assert hasattr(bpy.ops.lod, "camera_distance_preview"), "Opérateur lod.camera_distance_preview manquant !"
    assert hasattr(bpy.ops.lod, "export"), "Opérateur lod.export manquant !"
    print("✓ Enregistrement et opérateurs validés avec succès.")


def create_test_model():
    """Creates a test mesh with Suzanne + UVs + marked seam + small rivet island + materials."""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = "TestHeroAsset"

    # Ensure UV map exists
    mesh = obj.data
    if len(mesh.uv_layers) == 0:
        mesh.uv_layers.new(name="UVMap")

    # Add 2 materials
    mat1 = bpy.data.materials.new("Mat_Body")
    mat2 = bpy.data.materials.new("Mat_Eyes")
    mesh.materials.append(mat1)
    mesh.materials.append(mat2)

    # Assign mat2 to some faces & mark some seams & add small rivet island via BMesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    # Mark first 10 edges as seams
    for i in range(min(10, len(bm.edges))):
        bm.edges[i].seam = True

    # Assign second material to some faces
    for i in range(min(20, len(bm.faces))):
        bm.faces[i].material_index = 1

    # Add a tiny detached cube island (rivet/bolt) at x=2.5 with size=0.04
    rivet_res = bmesh.ops.create_cube(bm, size=0.04)
    for v in rivet_res['verts']:
        v.co += Vector((2.5, 0.0, 0.0))

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    return obj


def test_topological_audit(obj):
    print("\n--- Test 2: Audit Topologique et Détection d'Îlots ---")
    from lod_generator_addon.core.analyzer import audit_mesh
    audit = audit_mesh(obj)

    print(f"  Sommets: {audit.vertex_count}")
    print(f"  Triangles: {audit.triangle_count}")
    print(f"  Couches UV: {audit.uv_layer_count} ({audit.uv_layers})")
    print(f"  Îlots détectés (BFS): {audit.island_count}")
    print(f"  Rayon bounding: {audit.bounding_radius:.3f}m")

    assert audit.is_valid, f"Audit échoué: {audit.errors}"
    assert audit.triangle_count > 500, "Nombre de triangles anormalement bas !"
    assert audit.island_count >= 2, f"Le rivet détaché n'a pas été détecté comme îlot distinct (islands={audit.island_count}) !"
    print("✓ Audit topologique et détection BFS des îlots validés.")


def test_vertex_protection_weighting(obj):
    print("\n--- Test 3: Carte de Protection et Pondération (Vertex Group) ---")
    from lod_generator_addon.core.weight_mapper import apply_protection_mask_to_object, PROTECTION_VG_NAME

    vg = apply_protection_mask_to_object(
        obj=obj,
        preserve_uv=0.85,
        preserve_silhouette=0.75,
        preserve_boundary=1.0,
        preserve_material=0.90,
        lod_level=1,
        relax_weights=True
    )

    assert vg is not None, "Vertex Group non créé !"
    assert vg.name == PROTECTION_VG_NAME, "Nom du groupe incorrect !"

    # Read back weights
    weights = []
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == vg.index:
                weights.append(g.weight)
                break

    assert len(weights) == len(obj.data.vertices), "Certains sommets n'ont pas de poids assigné !"
    max_w = max(weights)
    min_w = min(weights)
    print(f"  Poids minimum: {min_w:.3f}, Poids maximum: {max_w:.3f}")
    assert max_w >= 0.70, f"Le poids maximal de protection ({max_w}) devrait être >= 0.70 sur les coutures/angles !"
    assert min_w <= 0.10, f"Le poids minimal ({min_w}) devrait être proche de 0.0 sur les zones plates !"
    print("✓ Masque de pondération mathématique validé.")


def test_full_lod_pipeline(obj):
    print("\n--- Test 4: Pipeline Complet de Génération de LODs ---")
    settings = bpy.context.scene.lod_generator_settings
    settings.lod_count = 3
    settings.reduction_preset = 'EXPONENTIAL' # 50%, 25%, 12.5%
    settings.generation_mode = 'PARALLEL'
    settings.pre_triangulate = True
    settings.use_delimit_uv = True
    settings.uv_border_curvature = True
    settings.bvh_repair_uv = True
    settings.split_weld_isolation = False
    settings.pre_planar_dissolve = False
    settings.cull_small_parts = True
    settings.cull_threshold = 2.5
    settings.normal_strategy = 'TRANSFER'
    settings.setup_lod_group = True
    settings.generate_collider = True
    settings.collider_type = 'UCX'

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    res = bpy.ops.lod.generate()
    assert res == {'FINISHED'}, "L'opérateur lod.generate n'a pas retourné FINISHED !"

    base_name = "TestHeroAsset"
    lod0 = bpy.data.objects.get(f"{base_name}_LOD0")
    lod1 = bpy.data.objects.get(f"{base_name}_LOD1")
    lod2 = bpy.data.objects.get(f"{base_name}_LOD2")
    lod3 = bpy.data.objects.get(f"{base_name}_LOD3")
    empty_grp = bpy.data.objects.get(f"LODGroup_{base_name}")
    collider = bpy.data.objects.get(f"UCX_{base_name}_01")

    assert lod0 is not None, "LOD0 manquant !"
    assert lod1 is not None, "LOD1 manquant !"
    assert lod2 is not None, "LOD2 manquant !"
    assert lod3 is not None, "LOD3 manquant !"
    assert empty_grp is not None, "Empty LODGroup manquant !"
    assert empty_grp.get("fbx_type") == "LodGroup", "Propriété fbx_type = 'LodGroup' manquante !"
    assert collider is not None, "Collider UCX_ manquant !"

    # Verify hierarchy
    assert lod0.parent == empty_grp, "LOD0 non parenté sous l'Empty !"
    assert lod1.parent == empty_grp, "LOD1 non parenté sous l'Empty !"
    assert lod2.parent == empty_grp, "LOD2 non parenté sous l'Empty !"
    assert lod3.parent == empty_grp, "LOD3 non parenté sous l'Empty !"

    from lod_generator_addon.utils.math_helpers import get_triangulated_face_count
    t0 = get_triangulated_face_count(lod0)
    t1 = get_triangulated_face_count(lod1)
    t2 = get_triangulated_face_count(lod2)
    t3 = get_triangulated_face_count(lod3)

    print(f"  LOD0 Triangles: {t0}")
    print(f"  LOD1 Triangles: {t1} (Réduction: {(1.0 - t1/t0)*100:.1f}%)")
    print(f"  LOD2 Triangles: {t2} (Réduction: {(1.0 - t2/t0)*100:.1f}%)")
    print(f"  LOD3 Triangles: {t3} (Réduction: {(1.0 - t3/t0)*100:.1f}%)")

    assert t0 > t1 > t2 > t3, f"Décroissance polygonale non respectée : {t0} > {t1} > {t2} > {t3}"
    assert len(settings.stats_items) == 4, f"Nombre d'entrées stats incorrect ({len(settings.stats_items)}) !"
    print("✓ Pipeline LODs, hiérarchie Empty 'LodGroup', collider UCX_ et statistiques validés.")


def test_exploded_view():
    print("\n--- Test 5: Vue Éclatée (Exploded View) ---")
    settings = bpy.context.scene.lod_generator_settings
    base_name = "TestHeroAsset"
    lod0 = bpy.data.objects.get(f"{base_name}_LOD0")
    lod1 = bpy.data.objects.get(f"{base_name}_LOD1")
    lod2 = bpy.data.objects.get(f"{base_name}_LOD2")

    bpy.context.view_layer.objects.active = lod0

    # Toggle On
    bpy.ops.lod.toggle_exploded_view()
    assert settings.is_exploded is True, "settings.is_exploded devrait être True !"
    assert lod1.location.x > 0.1, f"LOD1 devrait être décalé sur X (x={lod1.location.x}) !"
    assert lod2.location.x > lod1.location.x, "LOD2 devrait être plus éloigné sur X que LOD1 !"
    print(f"  Décalage Vue Éclatée : LOD0 x={lod0.location.x:.2f}, LOD1 x={lod1.location.x:.2f}, LOD2 x={lod2.location.x:.2f}")

    # Toggle Off (Align back)
    bpy.ops.lod.toggle_exploded_view()
    assert settings.is_exploded is False, "settings.is_exploded devrait être False !"
    assert abs(lod1.location.x) < 1e-4, "LOD1 devrait être réaligné à 0.0 !"
    assert abs(lod2.location.x) < 1e-4, "LOD2 devrait être réaligné à 0.0 !"
    print("✓ Basculement vue éclatée et réalignement validés.")


def test_engine_export():
    print("\n--- Test 6: Exportation Moteur (FBX et glTF) ---")
    settings = bpy.context.scene.lod_generator_settings
    base_name = "TestHeroAsset"
    lod0 = bpy.data.objects.get(f"{base_name}_LOD0")
    bpy.context.view_layer.objects.active = lod0

    export_dir = os.path.join(ADDON_DIR, "test_exports_lod")
    os.makedirs(export_dir, exist_ok=True)
    settings.export_path = export_dir

    # FBX Export
    settings.export_format = 'FBX'
    res_fbx = bpy.ops.lod.export()
    assert res_fbx == {'FINISHED'}, "Export FBX échoué !"
    fbx_file = os.path.join(export_dir, f"{base_name}_LODs.fbx")
    assert os.path.exists(fbx_file), "Fichier FBX non créé sur le disque !"
    assert os.path.getsize(fbx_file) > 1000, "Taille du fichier FBX anormale !"
    print(f"  Fichier FBX créé: {fbx_file} ({os.path.getsize(fbx_file):,} octets)")

    # glTF Export
    settings.export_format = 'GLTF'
    res_gltf = bpy.ops.lod.export()
    assert res_gltf == {'FINISHED'}, "Export glTF échoué !"
    gltf_file = os.path.join(export_dir, f"{base_name}_LODs.glb")
    assert os.path.exists(gltf_file), "Fichier GLB non créé sur le disque !"
    assert os.path.getsize(gltf_file) > 1000, "Taille du fichier GLB anormale !"
    print(f"  Fichier glTF créé: {gltf_file} ({os.path.getsize(gltf_file):,} octets)")

    print("✓ Exportateurs FBX et glTF pour Unreal / Unity / Godot validés.")


def test_uv_preservation_suite():
    print("\n--- Test 7: Algorithmes Avancés de Préservation des UVs ---")
    from lod_generator_addon.utils.math_helpers import (
        calculate_uv_signed_area,
        calculate_uv_angle_deviation,
        calculate_triangle_3d_area
    )

    # 1. Test signed area calculation
    uv0 = Vector((0.0, 0.0))
    uv1 = Vector((1.0, 0.0))
    uv2 = Vector((0.0, 1.0))
    area_normal = calculate_uv_signed_area(uv0, uv1, uv2)
    assert abs(area_normal - 0.5) < 1e-5, f"Aire UV normale erronée : {area_normal}"

    # Inverted triangle
    area_flipped = calculate_uv_signed_area(uv0, uv2, uv1)
    assert abs(area_flipped - (-0.5)) < 1e-5, f"Aire UV retournée erronée : {area_flipped}"

    # Degenerate (barcode) triangle (collinear)
    uv_deg = Vector((0.5, 0.0))
    area_deg = calculate_uv_signed_area(uv0, uv1, uv_deg)
    assert abs(area_deg) < 1e-6, f"Aire UV dégénérée non détectée : {area_deg}"

    # 2. Test 2D UV angle deviation
    dir_straight1 = Vector((1.0, 0.0, 0.0))
    dir_straight2 = Vector((-1.0, 0.0, 0.0))
    dev_straight = calculate_uv_angle_deviation(dir_straight1, dir_straight2)
    assert dev_straight < 1e-4, f"Déviation d'une ligne droite non nulle : {dev_straight}"

    dir_turn = Vector((0.0, 1.0, 0.0))
    dev_turn = calculate_uv_angle_deviation(dir_straight1, dir_turn)
    assert abs(dev_turn - math.radians(90.0)) < 1e-4, f"Déviation d'un virage 90° erronée : {dev_turn}"

    print("  Calcul d'aire signee 2D : Normal=0.5, Inverse=-0.5, Degenere (Code-barres) ~ 0.0")
    print("  Deviation angulaire 2D UV : Ligne droite = 0.0 deg, Virage a 90 deg = 90.0 deg")

    # 3. Test repair_corrupted_uvs_via_bvh (Issue 1)
    from lod_generator_addon.core.decimator import repair_corrupted_uvs_via_bvh
    mesh_src = bpy.data.meshes.new("TestUV_LOD0")
    obj_src = bpy.data.objects.new("TestUV_LOD0", mesh_src)
    bpy.context.collection.objects.link(obj_src)
    bm_s = bmesh.new()
    vA = bm_s.verts.new((0.0, 0.0, 0.0))
    vB = bm_s.verts.new((1.0, 0.0, 0.0))
    vC = bm_s.verts.new((1.0, 1.0, 0.0))
    vD = bm_s.verts.new((0.0, 1.0, 0.0))
    f_src = bm_s.faces.new((vA, vB, vC, vD))
    uv_l = bm_s.loops.layers.uv.new("UVMap")
    f_src.loops[0][uv_l].uv = (0.0, 0.0)
    f_src.loops[1][uv_l].uv = (1.0, 0.0)
    f_src.loops[2][uv_l].uv = (1.0, 1.0)
    f_src.loops[3][uv_l].uv = (0.0, 1.0)
    bm_s.to_mesh(mesh_src)
    bm_s.free()

    mesh_tgt = bpy.data.meshes.new("TestUV_Target")
    obj_tgt = bpy.data.objects.new("TestUV_Target", mesh_tgt)
    bpy.context.collection.objects.link(obj_tgt)
    bm_t = bmesh.new()
    t0 = bm_t.verts.new((0.0, 0.0, 0.0))
    t1 = bm_t.verts.new((1.0, 0.0, 0.0))
    t2 = bm_t.verts.new((0.0, 1.0, 0.0))
    f_tgt = bm_t.faces.new((t0, t1, t2))
    uv_lt = bm_t.loops.layers.uv.new("UVMap")
    # Degenerate collinear UVs (code-barre)
    f_tgt.loops[0][uv_lt].uv = (0.0, 0.0)
    f_tgt.loops[1][uv_lt].uv = (0.5, 0.0)
    f_tgt.loops[2][uv_lt].uv = (1.0, 0.0)
    bm_t.to_mesh(mesh_tgt)
    bm_t.free()

    repaired = repair_corrupted_uvs_via_bvh(obj_tgt, obj_src)
    assert repaired == 1, f"Le triangle degenere aurait du etre repare (repaired={repaired})"

    bm_check = bmesh.new()
    bm_check.from_mesh(obj_tgt.data)
    bm_check.faces.ensure_lookup_table()
    uv_c = bm_check.loops.layers.uv.active
    uv_res = [l[uv_c].uv for l in bm_check.faces[0].loops]
    area_repaired = abs(calculate_uv_signed_area(uv_res[0], uv_res[1], uv_res[2]))
    assert area_repaired > 1e-4, f"L'aire UV reparee doit etre saine (>1e-4), trouvee: {area_repaired}"
    bm_check.free()

    bpy.data.objects.remove(obj_src, do_unlink=True)
    bpy.data.meshes.remove(mesh_src)
    bpy.data.objects.remove(obj_tgt, do_unlink=True)
    bpy.data.meshes.remove(mesh_tgt)
    print("  Reparation BVH : Triangulation source, aspect ratio et echantillonnage interieur valides [OK]")

    # 4. Test mark_uv_discontinuities_as_seams and LOD3 floor (Issue 2)
    from lod_generator_addon.core.weight_mapper import mark_uv_discontinuities_as_seams, compute_vertex_protection_weights
    bm_seam_test = bmesh.new()
    s0 = bm_seam_test.verts.new((0.0, 0.0, 0.0))
    s1 = bm_seam_test.verts.new((1.0, 0.0, 0.0))
    s2 = bm_seam_test.verts.new((1.0, 1.0, 0.0))
    s3 = bm_seam_test.verts.new((0.0, 1.0, 0.0))
    f_left = bm_seam_test.faces.new((s0, s1, s2))
    f_right = bm_seam_test.faces.new((s0, s2, s3))
    uv_l_test = bm_seam_test.loops.layers.uv.new("UVMap")
    # Shared edge between s0 and s2
    # Island 1
    f_left.loops[0][uv_l_test].uv = (0.0, 0.0)
    f_left.loops[1][uv_l_test].uv = (0.5, 0.0)
    f_left.loops[2][uv_l_test].uv = (0.5, 0.5)
    # Island 2 (offset in UV space, simulating a texture seam without explicit mark)
    f_right.loops[0][uv_l_test].uv = (0.7, 0.7)
    f_right.loops[1][uv_l_test].uv = (1.0, 1.0)
    f_right.loops[2][uv_l_test].uv = (0.7, 1.0)

    marked_seams = mark_uv_discontinuities_as_seams(bm_seam_test)
    assert marked_seams == 1, f"L'arete de coupure UV aurait du etre marquee en seam (marked={marked_seams})"
    shared_edge = bm_seam_test.edges.get((s0, s2))
    assert shared_edge and shared_edge.seam, "L'arete partagee doit avoir e.seam == True"

    # Verify LOD3 protection weights maintain floor >= 0.5 on seams
    weights_lod3 = compute_vertex_protection_weights(bm_seam_test, lod_level=3, relax_weights=True)
    assert weights_lod3[s0.index] >= 0.5, f"Le sommet de couture au LOD3 doit conserver un poids >= 0.5 (w={weights_lod3[s0.index]})"
    bm_seam_test.free()
    print("  Detection coutures UV & plancher LOD3 (w >= 0.5) valides [OK]")

    # 5. Test UV Sphere pole detection and unfold_and_harmonize_shading
    from lod_generator_addon.core.decimator import unfold_and_harmonize_shading, fix_flipped_faces_and_smoothing
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=16, radius=1.0)
    sphere_obj = bpy.context.active_object
    bm_sph = bmesh.new()
    bm_sph.from_mesh(sphere_obj.data)
    bm_sph.verts.ensure_lookup_table()

    weights_sph = compute_vertex_protection_weights(bm_sph, obj=sphere_obj)
    north_pole = [v for v in bm_sph.verts if v.co.z > 0.99][0]
    south_pole = [v for v in bm_sph.verts if v.co.z < -0.99][0]
    assert weights_sph[north_pole.index] == 0.0, f"Le pole Nord lisse doit avoir un poids de 0.0 (w={weights_sph[north_pole.index]})"
    assert weights_sph[south_pole.index] == 0.0, f"Le pole Sud lisse doit avoir un poids de 0.0 (w={weights_sph[south_pole.index]})"
    bm_sph.free()

    # Test unfold_and_harmonize_shading on a flipped face
    sphere_lod = sphere_obj.copy()
    sphere_lod.data = sphere_obj.data.copy()
    bpy.context.collection.objects.link(sphere_lod)
    bm_flip = bmesh.new()
    bm_flip.from_mesh(sphere_lod.data)
    bm_flip.faces.ensure_lookup_table()
    bm_flip.faces[0].normal_flip()
    bm_flip.to_mesh(sphere_lod.data)
    bm_flip.free()

    repaired_flips = unfold_and_harmonize_shading(sphere_lod, sphere_obj)
    assert repaired_flips >= 1, f"Les sommets de la face inversee auraient du etre detectes et relaxes (repaired={repaired_flips})"

    mesh_sph = sphere_obj.data
    mesh_lod = sphere_lod.data
    bpy.data.objects.remove(sphere_obj, do_unlink=True)
    bpy.data.objects.remove(sphere_lod, do_unlink=True)
    if mesh_sph.users == 0: bpy.data.meshes.remove(mesh_sph)
    if mesh_lod.users == 0: bpy.data.meshes.remove(mesh_lod)
    print("  Singularites polaires (w=0.0) & depliage / harmonisation de shading valides [OK]")
    print("[OK] Validation mathematique du module de preservation des UVs.")


def _make_sphere(name, subdivisions=4):
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=1.0)
    bm.to_mesh(mesh)
    bm.free()
    mesh.uv_layers.new(name="UVMap")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _remove_object(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _select_only(obj):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)


def _bumpy_sphere(name, subdivisions=5):
    """Icosphere with a radial ripple: real detail for error budgets and normal bakes."""
    obj = _make_sphere(name, subdivisions)
    for v in obj.data.vertices:
        n = v.co.normalized()
        v.co = n * (1.0 + 0.04 * math.sin(12 * n.x) * math.sin(12 * n.y) * math.sin(12 * n.z))
    mat = bpy.data.materials.new(f"{name}_Mat")
    mat.use_nodes = True
    obj.data.materials.append(mat)
    for p in obj.data.polygons:
        p.use_smooth = True
    return obj


def test_error_budget():
    print("\n--- Test 12: Budget d'erreur et taille ecran (v1.2, #11) ---")
    from lod_generator_addon.core.decimator import find_ratio_for_error
    from lod_generator_addon.core.error_metrics import measure_deviation, screen_size_to_error
    from lod_generator_addon.utils.settings_map import pipeline_kwargs
    from lod_generator_addon.utils.math_helpers import get_triangulated_face_count

    src = _bumpy_sphere("TestBudget_LOD0")
    settings = bpy.context.scene.lod_generator_settings
    tris = get_triangulated_face_count(src)
    kwargs = pipeline_kwargs(settings)

    # Budget plus large -> ratio plus petit
    tight, tight_err = find_ratio_for_error(bpy.context, src, src, 0.004, 1, tris, **kwargs)
    loose, loose_err = find_ratio_for_error(bpy.context, src, src, 0.03, 1, tris, **kwargs)
    assert tight_err <= 0.004 and loose_err <= 0.03, "Budget d'erreur non respecte par la recherche !"
    assert loose < tight, f"Un budget plus large devrait reduire davantage ({loose:.3f} >= {tight:.3f})"

    # Pipeline complet en mode budget : chaque LOD respecte son budget
    _select_only(src)
    settings.lod_count = 3
    settings.reduction_mode = 'ERROR'
    settings.max_error = 0.004
    settings.error_growth = 2.0
    assert bpy.ops.lod.generate() == {'FINISHED'}
    items = list(settings.stats_items)
    for item in items[1:]:
        assert item.error_budget > 0.0
        assert item.deviation <= item.error_budget * 1.001, (
            f"{item.lod_name} : ecart {item.deviation:.4f} > budget {item.error_budget:.4f}")
    assert items[1].triangles >= items[2].triangles >= items[3].triangles, "Triangles non decroissants !"
    lod1 = bpy.data.objects["TestBudget_LOD1"]
    assert abs(measure_deviation(lod1, src) - items[1].deviation) < 1e-6

    # Mode taille ecran : erreur = diametre * px / (taille * hauteur)
    assert abs(screen_size_to_error(2.0, 0.25, 1.0, 1000) - 0.008) < 1e-9
    settings.reduction_mode = 'SCREEN'
    _select_only(src)
    assert bpy.ops.lod.generate() == {'FINISHED'}
    for item in list(settings.stats_items)[1:]:
        assert item.deviation <= item.error_budget * 1.001

    settings.reduction_mode = 'RATIO'
    bpy.ops.lod.clear_lods()
    _remove_object(src)
    print(f"  budget 4 mm -> ratio {tight:.3f}, budget 30 mm -> ratio {loose:.3f} [OK]")
    print("✓ Budget d'erreur et mode taille ecran valides.")


def test_remesh():
    print("\n--- Test 13: Remesh Voxel / QuadriFlow avant decimation (v1.2, #14) ---")
    from lod_generator_addon.core.decimator import generate_lod_mesh
    from lod_generator_addon.core.analyzer import find_connected_islands
    from lod_generator_addon.core.remesher import remesh_object
    from lod_generator_addon.utils.math_helpers import get_triangulated_face_count

    # Kitbash sale : deux cubes qui se chevauchent + une petite boule interne, 2 materiaux
    mesh = bpy.data.meshes.new("TestDirty_Mesh")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    geom = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.translate(bm, verts=geom["verts"], vec=(0.5, 0.3, 0.2))
    geom = bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.2)
    for f in bm.faces:
        f.material_index = 1 if f.calc_center_median().x > 0.6 else 0
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=3, use_grid_fill=True)
    bm.to_mesh(mesh)
    bm.free()
    mesh.uv_layers.new(name="UVMap")
    mesh.materials.append(bpy.data.materials.new("TestDirty_A"))
    mesh.materials.append(bpy.data.materials.new("TestDirty_B"))
    src = bpy.data.objects.new("TestDirty_LOD0", mesh)
    bpy.context.scene.collection.objects.link(src)

    bm = bmesh.new()
    bm.from_mesh(src.data)
    islands_before = len(find_connected_islands(bm))
    bm.free()
    assert islands_before == 3

    tris = get_triangulated_face_count(src)
    lod, _ = generate_lod_mesh(bpy.context, src, src, "TestDirty_LOD1", 0.5, 1, tris,
                               remesh_mode='VOXEL', remesh_voxel_pct=3.0, cull_small_parts=False)
    bm = bmesh.new()
    bm.from_mesh(lod.data)
    islands_after = len(find_connected_islands(bm))
    bm.free()
    assert islands_after == 1, f"Remesh voxel : {islands_after} ilots au lieu de 1"
    assert "UVMap" in lod.data.uv_layers, "UVs perdues par le remesh !"
    assert {p.material_index for p in lod.data.polygons} == {0, 1}, "Index de materiaux perdus !"
    assert len(lod.data.materials) == 2
    _remove_object(lod)

    # QuadriFlow (ou repli voxel) sur une sphere propre
    sphere = _make_sphere("TestQuadri", subdivisions=3)
    faces_before = len(sphere.data.polygons)
    used = remesh_object(bpy.context, sphere, 'QUADRIFLOW', quadriflow_ratio=0.5)
    assert used in {'QUADRIFLOW', 'VOXEL'} and len(sphere.data.polygons) != faces_before
    _remove_object(sphere)
    _remove_object(src)
    print(f"  ilots {islands_before} -> {islands_after}, QuadriFlow -> {used} [OK]")
    print("✓ Remesh avant decimation valide (UVs et materiaux conserves).")


def test_bake_and_impostor():
    print("\n--- Test 14: Bake normales & AO + imposteur billboard (v1.2, #12 #13) ---")
    import numpy as np

    src = _bumpy_sphere("TestBake_LOD0", subdivisions=5)
    lod0_mat = src.data.materials[0]
    lod0_nodes = len(lod0_mat.node_tree.nodes)
    scenes_before = len(bpy.data.scenes)
    cameras_before = len(bpy.data.cameras)

    settings = bpy.context.scene.lod_generator_settings
    settings.lod_count = 3
    settings.reduction_mode = 'RATIO'
    settings.reduction_preset = 'EXPONENTIAL'
    settings.bake_enabled = True
    settings.bake_from_level = 3
    settings.bake_resolution = '256'
    settings.bake_ao = True
    settings.bake_ao_samples = 2
    settings.impostor_enabled = True
    settings.impostor_views = '8'
    settings.impostor_resolution = 32
    settings.impostor_engine = 'WORKBENCH'
    engine_before = bpy.context.scene.render.engine

    _select_only(src)
    assert bpy.ops.lod.generate() == {'FINISHED'}

    # Bake : maps creees, non uniformes, branchees ; LOD0 et LOD1/2 inchanges
    lod3 = bpy.data.objects["TestBake_LOD3"]
    normal = bpy.data.images.get("TestBake_LOD3_Normal")
    ao = bpy.data.images.get("TestBake_LOD3_AO")
    assert normal and ao and normal.packed_file and ao.packed_file, "Maps bakees absentes ou non empaquetees !"
    pixels = np.empty(len(normal.pixels), dtype=np.float32)
    normal.pixels.foreach_get(pixels)
    assert pixels.reshape(-1, 4)[:, 0].std() > 0.01, "Normal map plate (aucun rayon n'a touche LOD0) !"
    apx = np.empty(len(ao.pixels), dtype=np.float32)
    ao.pixels.foreach_get(apx)
    ao_values = apx.reshape(-1, 4)[:, 0]
    assert (ao_values == 0.0).mean() < 0.5 and ao_values.max() > 0.5, "AO vide ou noire !"
    mat3 = lod3.data.materials[0]
    assert mat3 is not lod0_mat and mat3.name.startswith("TestBake_LOD3_")
    assert any(n.type == 'NORMAL_MAP' for n in mat3.node_tree.nodes)
    assert len(lod0_mat.node_tree.nodes) == lod0_nodes, "Materiau de LOD0 modifie par le bake !"
    assert bpy.data.objects["TestBake_LOD1"].data.materials[0] is lod0_mat
    assert bpy.context.scene.render.engine == engine_before, "Moteur de rendu non restaure !"

    # Imposteur : LOD4, 8 quads, atlas 3x3 cases, alpha, aucune donnee temporaire
    imp = bpy.data.objects.get("TestBake_LOD4")
    assert imp is not None and len(imp.data.polygons) == 8, "Imposteur absent ou mal forme !"
    atlas = bpy.data.images.get("TestBake_LOD4_ImpostorAtlas")
    assert atlas and tuple(atlas.size) == (96, 96), f"Atlas inattendu : {atlas and tuple(atlas.size)}"
    apix = np.empty(len(atlas.pixels), dtype=np.float32)
    atlas.pixels.foreach_get(apix)
    alpha = apix.reshape(-1, 4)[:, 3]
    assert alpha.max() > 0.5 and alpha.min() < 0.01, "Atlas sans silhouette ou sans transparence !"
    assert len(bpy.data.scenes) == scenes_before and len(bpy.data.cameras) == cameras_before, \
        "Scene ou camera temporaire laissee !"
    assert imp.parent and imp.parent.name == "LODGroup_TestBake", "Imposteur hors du LodGroup !"
    assert [i.lod_name for i in settings.stats_items][-1] == "TestBake_LOD4"

    # Nettoyage : objets, materiaux et images des LODs supprimes
    bpy.ops.lod.clear_lods()
    leftovers = [b.name for b in list(bpy.data.materials) + list(bpy.data.images)
                 if b.name.startswith("TestBake_LOD") and not b.name.startswith("TestBake_LOD0")]
    assert not leftovers, f"Donnees orphelines apres nettoyage : {leftovers}"

    settings.bake_enabled = False
    settings.impostor_enabled = False
    _remove_object(src)
    print("  normal + AO bakes (256 px), imposteur 8 vues (atlas 96x96) [OK]")
    print("✓ Bake et imposteur valides, LOD0 intact, aucune donnee temporaire.")


def test_double_walled_shell():
    print("\n--- Test 8b: Coque a double paroi (ballon, solidify) ---")
    from lod_generator_addon.core.decimator import generate_lod_mesh
    from lod_generator_addon.utils.math_helpers import get_triangulated_face_count

    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=0.3, location=(0, 0, 0))
    shell = bpy.context.active_object
    shell.name = "TestShell_LOD0"
    bpy.ops.object.shade_smooth()
    mod = shell.modifiers.new("Solidify", 'SOLIDIFY')
    mod.thickness = 0.014
    mod.use_rim = False
    bpy.ops.object.modifier_apply(modifier=mod.name)

    tris = get_triangulated_face_count(shell)
    for level, ratio in [(1, 0.5), (2, 0.25)]:
        lod, _ = generate_lod_mesh(bpy.context, shell, shell, f"TestShell_LOD{level}", ratio, level, tris)
        me = lod.data
        inverted = sum(
            1 for p in me.polygons for li in p.loop_indices
            if me.corner_normals[li].vector.dot(p.normal) < 0.0
        )
        assert inverted == 0, f"LOD{level}: {inverted} normales inversees (paroi opposee echantillonnee) !"
        inward = sum(1 for p in me.polygons if p.normal.dot(p.center) < 0.0)
        assert inward > 0, f"LOD{level}: la paroi interieure a ete retournee vers l'exterieur !"
        mesh = lod.data
        bpy.data.objects.remove(lod, do_unlink=True)
        bpy.data.meshes.remove(mesh)

    mesh = shell.data
    bpy.data.objects.remove(shell, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    print("✓ Parois interieures preservees, aucune normale inversee.")


def test_cleanup():
    print("\n--- Test 8: Nettoyage et Réinitialisation ---")
    base_name = "TestHeroAsset"
    lod0 = bpy.data.objects.get(f"{base_name}_LOD0")
    bpy.context.view_layer.objects.active = lod0

    bpy.ops.lod.clear_lods()

    assert bpy.data.objects.get(f"{base_name}_LOD1") is None, "LOD1 non supprimé !"
    assert bpy.data.objects.get(f"{base_name}_LOD2") is None, "LOD2 non supprimé !"
    assert bpy.data.objects.get(f"{base_name}_LOD3") is None, "LOD3 non supprimé !"
    assert bpy.data.objects.get(f"LODGroup_{base_name}") is None, "Empty LODGroup non supprimé !"
    assert bpy.data.objects.get(f"UCX_{base_name}_01") is None, "Collider non supprimé !"

    # Verify no orphan mesh data blocks remain in memory (Issue 4)
    assert bpy.data.meshes.get(f"{base_name}_LOD1_Mesh") is None, "Mesh LOD1_Mesh orphelin dans bpy.data.meshes !"
    assert bpy.data.meshes.get(f"{base_name}_LOD2_Mesh") is None, "Mesh LOD2_Mesh orphelin dans bpy.data.meshes !"
    assert bpy.data.meshes.get(f"{base_name}_LOD3_Mesh") is None, "Mesh LOD3_Mesh orphelin dans bpy.data.meshes !"
    assert bpy.data.meshes.get(f"UCX_{base_name}_01_Mesh") is None, "Mesh UCX orphelin dans bpy.data.meshes !"
    print("✓ Nettoyage et purge des maillages orphelins validés.")


def main():
    try:
        test_registration()
        obj = create_test_model()
        test_topological_audit(obj)
        test_vertex_protection_weighting(obj)
        test_full_lod_pipeline(obj)
        test_exploded_view()
        test_engine_export()
        test_uv_preservation_suite()
        test_double_walled_shell()
        test_cleanup()
        test_error_budget()
        test_remesh()
        test_bake_and_impostor()

        print("\n" + "=" * 70)
        print("  TOUS LES TESTS SONT PASSES AVEC SUCCES.")
        print("=" * 70)

    except Exception as err:
        print(f"\n[ERREUR] Echec lors de l'execution des tests : {err}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
