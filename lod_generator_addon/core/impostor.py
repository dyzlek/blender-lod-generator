"""
Billboard impostor for the last LOD.
LOD0 is rendered from N angles around Z (orthographic camera, transparent film) in an
isolated temporary scene; the views are packed into an atlas and mapped onto a "star"
of N/2 crossed vertical planes. Each plane shows the view from its front direction on
one side and the opposite view on the other side: works in any engine, no special shader.
"""

import math
import os
import tempfile

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

from ..utils.math_helpers import calculate_bounding_sphere
from ..utils.hierarchy import free_datablock_name

TMP_SCENE_NAME = "_LOD_Impostor_Scene"


def _set_engine(scene, engine: str):
    """'WORKBENCH' or 'EEVEE' (identifier differs between Blender versions)."""
    candidates = ['BLENDER_WORKBENCH'] if engine == 'WORKBENCH' else ['BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE']
    for identifier in candidates:
        try:
            scene.render.engine = identifier
            return
        except TypeError:
            continue


def _world_bounds(obj):
    """Bounding sphere center (world) and radius (world units)."""
    center, radius = calculate_bounding_sphere(obj)
    scale = max(abs(s) for s in obj.matrix_world.to_scale())
    return obj.matrix_world @ center, radius * scale


def capture_views(context, obj: bpy.types.Object, views: int, resolution: int, engine: str) -> list[np.ndarray]:
    """Renders obj from `views` angles around Z; returns RGBA float arrays (bottom-up rows)."""
    center, radius = _world_bounds(obj)

    scene = bpy.data.scenes.new(TMP_SCENE_NAME)
    cam_data = bpy.data.cameras.new(f"{TMP_SCENE_NAME}_Cam")
    cam_obj = bpy.data.objects.new(f"{TMP_SCENE_NAME}_Cam", cam_data)
    light_data = bpy.data.lights.new(f"{TMP_SCENE_NAME}_Sun", 'SUN')
    light_obj = bpy.data.objects.new(f"{TMP_SCENE_NAME}_Sun", light_data)
    tmp_dir = tempfile.mkdtemp(prefix="lod_impostor_")
    images = []
    try:
        scene.collection.objects.link(obj)
        scene.collection.objects.link(cam_obj)
        scene.collection.objects.link(light_obj)
        scene.world = context.scene.world
        scene.camera = cam_obj

        cam_data.type = 'ORTHO'
        cam_data.ortho_scale = 2.0 * radius * 1.02
        cam_data.clip_start = radius * 0.01
        cam_data.clip_end = radius * 10.0
        light_data.energy = 3.0
        light_obj.rotation_euler = (math.radians(50.0), 0.0, math.radians(30.0))

        render = scene.render
        _set_engine(scene, engine)
        render.resolution_x = render.resolution_y = resolution
        render.resolution_percentage = 100
        render.film_transparent = True
        render.image_settings.file_format = 'PNG'
        render.image_settings.color_mode = 'RGBA'
        scene.view_settings.view_transform = 'Standard'
        if engine == 'WORKBENCH':
            scene.display.shading.light = 'STUDIO'
            scene.display.shading.color_type = 'TEXTURE'

        for i in range(views):
            angle = 2.0 * math.pi * i / views
            direction = Vector((math.cos(angle), math.sin(angle), 0.0))
            cam_obj.location = center + direction * radius * 3.0
            cam_obj.rotation_euler = (-direction).to_track_quat('-Z', 'Y').to_euler()

            path = os.path.join(tmp_dir, f"view_{i:02d}.png")
            render.filepath = path
            with context.temp_override(scene=scene):
                bpy.ops.render.render(write_still=True, scene=scene.name)
            img = bpy.data.images.load(path)
            pixels = np.empty(resolution * resolution * 4, dtype=np.float32)
            img.pixels.foreach_get(pixels)
            images.append(pixels.reshape(resolution, resolution, 4))
            bpy.data.images.remove(img)
            os.remove(path)
    finally:
        bpy.data.scenes.remove(scene)
        bpy.data.objects.remove(cam_obj)
        bpy.data.cameras.remove(cam_data)
        bpy.data.objects.remove(light_obj)
        bpy.data.lights.remove(light_data)
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
    return images


def build_atlas(name: str, views: list[np.ndarray]) -> tuple[bpy.types.Image, int, int]:
    """Packs the views into a grid image; returns (image, columns, rows)."""
    count = len(views)
    resolution = views[0].shape[0]
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    atlas = np.zeros((rows * resolution, cols * resolution, 4), dtype=np.float32)
    for i, view in enumerate(views):
        col, row = i % cols, i // cols
        atlas[row * resolution:(row + 1) * resolution, col * resolution:(col + 1) * resolution] = view

    image = bpy.data.images.new(free_datablock_name(bpy.data.images, name),
                                cols * resolution, rows * resolution, alpha=True)
    image.pixels.foreach_set(atlas.ravel())
    image.pack()
    return image, cols, rows


def _impostor_material(name: str, image: bpy.types.Image) -> bpy.types.Material:
    mat = bpy.data.materials.new(free_datablock_name(bpy.data.materials, name))
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    bsdf.inputs['Roughness'].default_value = 1.0
    tex = nodes.new('ShaderNodeTexImage')
    tex.image = image
    tex.location = (-400, 0)
    links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
    mat.use_backface_culling = True
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = 'DITHERED'
    if hasattr(mat, "blend_method"):
        try:
            mat.blend_method = 'CLIP'
        except TypeError:
            pass
    return mat


def build_star_mesh(name: str, obj: bpy.types.Object, views: int, cols: int, rows: int) -> bpy.types.Mesh:
    """
    N/2 crossed vertical quads through the bounding sphere center, in obj's local space.
    Plane k faces view k on its front side and view k + N/2 on its back side (slightly
    offset to avoid z-fighting); UVs point at the matching atlas cells.
    """
    center_w, radius = _world_bounds(obj)
    to_local = obj.matrix_world.inverted()
    up = Vector((0.0, 0.0, 1.0))
    eps = radius * 0.002

    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")

    def add_quad(view_index: int, direction: Vector, offset: float):
        right = Vector((-direction.y, direction.x, 0.0))
        base = center_w + direction * offset
        corners = [base - right * radius - up * radius, base + right * radius - up * radius,
                   base + right * radius + up * radius, base - right * radius + up * radius]
        col, row = view_index % cols, view_index // cols
        u0, v0, u1, v1 = col / cols, row / rows, (col + 1) / cols, (row + 1) / rows
        uvs = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        face = bm.faces.new([bm.verts.new(to_local @ c) for c in corners])
        for loop, uv in zip(face.loops, uvs):
            loop[uv_layer].uv = uv

    half = views // 2
    for k in range(half):
        angle = 2.0 * math.pi * k / views
        direction = Vector((math.cos(angle), math.sin(angle), 0.0))
        add_quad(k, direction, 0.0)
        add_quad(k + half, -direction, eps)

    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def generate_impostor(context, lod0_obj: bpy.types.Object, name: str, views: int = 8,
                      resolution: int = 256, engine: str = 'WORKBENCH') -> bpy.types.Object:
    """Creates the impostor object `name` (same transform as LOD0), linked like LOD0."""
    captures = capture_views(context, lod0_obj, views, resolution, engine)
    atlas, cols, rows = build_atlas(f"{name}_ImpostorAtlas", captures)
    mesh = build_star_mesh(name, lod0_obj, views, cols, rows)
    mesh.materials.append(_impostor_material(f"{name}_Impostor", atlas))

    obj = bpy.data.objects.new(name, mesh)
    obj.matrix_world = lod0_obj.matrix_world.copy()
    for col in lod0_obj.users_collection:
        col.objects.link(obj)
    if not obj.users_collection:
        context.collection.objects.link(obj)
    return obj
