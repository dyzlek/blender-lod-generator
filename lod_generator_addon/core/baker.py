"""
Normal and ambient occlusion bake from LOD0 onto distant LODs (Cycles, Selected to Active).
Each baked LOD gets its own copies of its materials, so LOD0 and the other LODs are
never modified.
"""

import bpy
import numpy as np

from ..utils.math_helpers import calculate_bounding_sphere
from ..utils.hierarchy import free_datablock_name

BAKE_UV_NAME = "LOD_BakeUV"

# UV suitability check: raster size and tolerated share of texels claimed by several faces
UV_CHECK_GRID = 128
UV_MAX_OVERLAP = 0.02


def uvs_suitable_for_bake(mesh: bpy.types.Mesh) -> bool:
    """
    False when the active UVs cannot hold a bake: missing, outside the 0-1 square
    (tiled textures) or overlapping (mirrored / stacked islands). Overlap is measured
    by rasterizing the UV triangles on a coarse grid (cell centers).
    """
    layer = mesh.uv_layers.active
    if layer is None or not mesh.polygons:
        return False
    uvs = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    layer.data.foreach_get("uv", uvs)
    uvs = uvs.reshape(-1, 2)
    if uvs.min() < -1e-3 or uvs.max() > 1.0 + 1e-3:
        return False

    grid = UV_CHECK_GRID
    hits = np.zeros((grid, grid), dtype=np.int32)
    for tri in mesh.loop_triangles if mesh.loop_triangles else ():
        a, b, c = (uvs[i] * grid for i in tri.loops)
        x0, x1 = int(max(0, np.floor(min(a[0], b[0], c[0])))), int(min(grid - 1, np.ceil(max(a[0], b[0], c[0]))))
        y0, y1 = int(max(0, np.floor(min(a[1], b[1], c[1])))), int(min(grid - 1, np.ceil(max(a[1], b[1], c[1]))))
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(d) < 1e-12:
            continue
        w0 = ((b[1] - c[1]) * (xs - c[0]) + (c[0] - b[0]) * (ys - c[1])) / d
        w1 = ((c[1] - a[1]) * (xs - c[0]) + (a[0] - c[0]) * (ys - c[1])) / d
        inside = (w0 >= 0) & (w1 >= 0) & (w0 + w1 <= 1)
        hits[y0:y1 + 1, x0:x1 + 1] += inside
    covered = np.count_nonzero(hits)
    if covered == 0:
        return False
    return np.count_nonzero(hits > 1) / covered <= UV_MAX_OVERLAP


def _ensure_principled_material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(free_datablock_name(bpy.data.materials, name))
    mat.use_nodes = True
    return mat


def _principled(mat: bpy.types.Material):
    return next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)


def _make_bake_uv(context, lod_obj: bpy.types.Object) -> str:
    """Creates a non-overlapping UV layer (Smart UV Project) used only by the baked maps."""
    mesh = lod_obj.data
    layer = mesh.uv_layers.get(BAKE_UV_NAME) or mesh.uv_layers.new(name=BAKE_UV_NAME)
    render_layer = next((uv.name for uv in mesh.uv_layers if uv.active_render), None)
    mesh.uv_layers.active = layer

    # Edit-mode operators follow the real active object (a context override is not enough
    # in the interface): make the LOD the only selected, active object for the unwrap
    view_layer = context.view_layer
    for obj in view_layer.objects:
        if obj.select_get():
            obj.select_set(False)
    lod_obj.select_set(True)
    view_layer.objects.active = lod_obj
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.smart_project(island_margin=0.01, scale_to_bounds=True)
        # Smart UV leaves thin parts as tiny islands: repack them (x3 texel coverage on a chair)
        bpy.ops.uv.pack_islands(margin=0.003, rotate=True, scale=True)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')

    # Keep the original UVs as render UVs for the existing textures.
    # Edit mode reallocates the layers: look them up again by name
    if render_layer and render_layer in mesh.uv_layers:
        mesh.uv_layers[render_layer].active_render = True
    mesh.uv_layers.active = mesh.uv_layers[BAKE_UV_NAME]
    return BAKE_UV_NAME


def _prepare_materials(lod_obj: bpy.types.Object) -> list[bpy.types.Material]:
    """Gives the LOD its own copies of its materials (named '<LOD>_<material>')."""
    mesh = lod_obj.data
    if not mesh.materials:
        mesh.materials.append(_ensure_principled_material(f"{lod_obj.name}_Baked"))
    materials = []
    for index, mat in enumerate(mesh.materials):
        if mat is None:
            mat = _ensure_principled_material(f"{lod_obj.name}_Baked")
        else:
            new_name = free_datablock_name(bpy.data.materials, f"{lod_obj.name}_{mat.name}")
            mat = mat.copy()
            mat.name = new_name
        mat.use_nodes = True
        mesh.materials[index] = mat
        materials.append(mat)
    # Object-level material slots would override the copies
    for slot in lod_obj.material_slots:
        if slot.link == 'OBJECT':
            slot.link = 'DATA'
    return materials


def _add_image_node(mat: bpy.types.Material, image: bpy.types.Image, uv_name: str | None, location):
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    node = nodes.new('ShaderNodeTexImage')
    node.image = image
    node.location = location
    if uv_name:
        uv_node = nodes.new('ShaderNodeUVMap')
        uv_node.uv_map = uv_name
        uv_node.location = (location[0] - 220, location[1])
        links.new(uv_node.outputs['UV'], node.inputs['Vector'])
    return node


def _ao_material(distance: float) -> bpy.types.Material:
    """Temporary emission material outputting the Ambient Occlusion node (see _bake_ao)."""
    mat = bpy.data.materials.new("_LOD_AO_Bake")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    ao = nodes.new('ShaderNodeAmbientOcclusion')
    ao.inputs['Distance'].default_value = distance
    emission = nodes.new('ShaderNodeEmission')
    output = nodes.new('ShaderNodeOutputMaterial')
    links.new(ao.outputs['AO'], emission.inputs['Color'])
    links.new(emission.outputs['Emission'], output.inputs['Surface'])
    return mat


def _bake_ao(context, lod_obj, lod0_obj, materials, image_nodes, cage: float, samples: int, distance: float):
    """
    AO through an emission bake: Cycles' AO bake pass returns black in Blender 5.x, so
    LOD0 temporarily wears an "AO node -> Emission" material and is baked with EMIT.
    LOD0's material slots are restored afterwards.
    """
    ao_mat = _ao_material(distance)
    slots = [(slot, slot.material) for slot in lod0_obj.material_slots]
    appended = not slots
    try:
        if appended:
            lod0_obj.data.materials.append(ao_mat)
        for slot, _ in slots:
            slot.material = ao_mat
        _bake(context, 'EMIT', lod_obj, lod0_obj, materials, image_nodes, cage, samples)
    finally:
        for slot, original in slots:
            slot.material = original
        if appended:
            lod0_obj.data.materials.pop()
        bpy.data.materials.remove(ao_mat)


def _bake(context, bake_type: str, lod_obj, lod0_obj, materials, image_nodes, cage: float, samples: int):
    for mat, node in zip(materials, image_nodes):
        mat.node_tree.nodes.active = node
        node.select = True
    scene = context.scene
    scene.cycles.samples = samples
    with context.temp_override(active_object=lod_obj, object=lod_obj,
                               selected_objects=[lod0_obj, lod_obj]):
        bpy.ops.object.bake(
            type=bake_type,
            use_selected_to_active=True,
            cage_extrusion=cage,
            max_ray_distance=cage * 4.0,
            normal_space='TANGENT',
            margin=8,
            use_clear=True,
        )


def _wire_normal(mat, image_node, uv_name):
    bsdf = _principled(mat)
    if bsdf is None:
        return
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    normal_map = nodes.new('ShaderNodeNormalMap')
    normal_map.space = 'TANGENT'
    if uv_name:
        normal_map.uv_map = uv_name
    normal_map.location = (image_node.location[0] + 300, image_node.location[1])
    links.new(image_node.outputs['Color'], normal_map.inputs['Color'])
    links.new(normal_map.outputs['Normal'], bsdf.inputs['Normal'])


def _wire_ao(mat, image_node):
    """Multiplies the baked AO into the base color (linked texture or plain value)."""
    bsdf = _principled(mat)
    if bsdf is None:
        return
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    base_input = bsdf.inputs['Base Color']
    mix = nodes.new('ShaderNodeMix')
    mix.data_type = 'RGBA'
    mix.blend_type = 'MULTIPLY'
    mix.inputs['Factor'].default_value = 1.0
    mix.location = (image_node.location[0] + 300, image_node.location[1])
    color_a = next(i for i in mix.inputs if i.name == 'A' and i.type == 'RGBA')
    color_b = next(i for i in mix.inputs if i.name == 'B' and i.type == 'RGBA')
    if base_input.links:
        links.new(base_input.links[0].from_socket, color_a)
    else:
        color_a.default_value = base_input.default_value
    links.new(image_node.outputs['Color'], color_b)
    result = next(o for o in mix.outputs if o.name == 'Result' and o.type == 'RGBA')
    links.new(result, base_input)


def bake_lod_maps(context, lod_obj: bpy.types.Object, lod0_obj: bpy.types.Object, resolution: int = 1024,
                  bake_ao: bool = True, dedicated_uv: bool = False, ao_samples: int = 16,
                  cage_pct: float = 2.0) -> dict:
    """
    Bakes LOD0's tangent normals (and AO) onto lod_obj and wires them into copies of its
    materials. When the LOD's UVs are tiled or overlapping, a dedicated UV layer is
    created automatically. Returns {"normal", "ao", "uv", "auto_uv"}.
    """
    scene = context.scene
    prev_engine = scene.render.engine
    prev_samples = scene.cycles.samples if hasattr(scene, "cycles") else None
    prev_active = context.view_layer.objects.active
    prev_selected = [o for o in context.view_layer.objects if o.select_get()]

    uv_name = None
    mesh = lod_obj.data
    if mesh.uv_layers and mesh.uv_layers.active is None:
        mesh.uv_layers.active = mesh.uv_layers[0]
    mesh.calc_loop_triangles()
    auto_uv = not dedicated_uv and not uvs_suitable_for_bake(mesh)
    if dedicated_uv or auto_uv:
        uv_name = _make_bake_uv(context, lod_obj)

    _, radius = calculate_bounding_sphere(lod0_obj)
    cage = max(radius * cage_pct / 100.0, 1e-4)
    materials = _prepare_materials(lod_obj)

    normal_img = bpy.data.images.new(free_datablock_name(bpy.data.images, f"{lod_obj.name}_Normal"),
                                     resolution, resolution, alpha=False)
    normal_img.colorspace_settings.name = 'Non-Color'
    ao_img = (bpy.data.images.new(free_datablock_name(bpy.data.images, f"{lod_obj.name}_AO"),
                                  resolution, resolution, alpha=False) if bake_ao else None)
    if ao_img:
        ao_img.colorspace_settings.name = 'Non-Color'

    # Isolate the pair: the other LODs overlap LOD0 exactly and would occlude the AO
    # (and any other render-visible object would catch the bake rays)
    isolated = [o for o in context.view_layer.objects
                if o not in (lod_obj, lod0_obj) and not o.hide_render]
    try:
        for obj in isolated:
            obj.hide_render = True
        scene.render.engine = 'CYCLES'
        for obj in prev_selected:
            obj.select_set(False)
        lod0_obj.select_set(True)
        lod_obj.select_set(True)
        context.view_layer.objects.active = lod_obj

        normal_nodes = [_add_image_node(m, normal_img, uv_name, (-700, -300)) for m in materials]
        _bake(context, 'NORMAL', lod_obj, lod0_obj, materials, normal_nodes, cage, 1)
        for mat, node in zip(materials, normal_nodes):
            _wire_normal(mat, node, uv_name)

        if ao_img:
            ao_nodes = [_add_image_node(m, ao_img, uv_name, (-700, 300)) for m in materials]
            _bake_ao(context, lod_obj, lod0_obj, materials, ao_nodes, cage, ao_samples, radius * 0.25)
            for mat, node in zip(materials, ao_nodes):
                _wire_ao(mat, node)

        for img in (normal_img, ao_img):
            if img:
                img.pack()
    finally:
        for obj in isolated:
            obj.hide_render = False
        scene.render.engine = prev_engine
        if prev_samples is not None:
            scene.cycles.samples = prev_samples
        lod0_obj.select_set(False)
        lod_obj.select_set(False)
        for obj in prev_selected:
            if obj.name in context.view_layer.objects:
                obj.select_set(True)
        context.view_layer.objects.active = prev_active

    return {"normal": normal_img, "ao": ao_img, "uv": uv_name, "auto_uv": auto_uv}
