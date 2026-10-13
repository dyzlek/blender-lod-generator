"""
Collision Mesh Generator for Game Engines (Unreal Engine / Unity / Godot).
Generates UCX_ (Convex Hull) and UBX_ (Oriented Box) colliders
with optimized physics wire display and naming conventions.
"""

import bmesh
import bpy
from mathutils import Vector
from ..utils.math_helpers import calculate_bounding_box


def get_or_create_collider_material() -> bpy.types.Material:
    """Creates or returns a semi-transparent green wire material for colliders."""
    mat_name = "M_LOD_Collider_Wire"
    mat = bpy.data.materials.get(mat_name)
    if not mat:
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        mat.diffuse_color = (0.1, 0.9, 0.2, 0.35)
        # Configure shader nodes for transparency
        tree = mat.node_tree
        tree.nodes.clear()
        node_out = tree.nodes.new(type='ShaderNodeOutputMaterial')
        node_bsdf = tree.nodes.new(type='ShaderNodeBsdfPrincipled')
        node_bsdf.inputs['Base Color'].default_value = (0.1, 0.9, 0.2, 1.0)
        node_bsdf.inputs['Alpha'].default_value = 0.35
        node_bsdf.inputs['Roughness'].default_value = 0.5
        tree.links.new(node_bsdf.outputs['BSDF'], node_out.inputs['Surface'])
        mat.blend_method = 'BLEND'
    return mat


def generate_convex_hull_collider(context, source_obj: bpy.types.Object, base_name: str, max_faces: int = 48) -> bpy.types.Object:
    """
    Generates a UCX_ convex hull collider suitable for Unreal Engine and Unity.
    Computes 3D convex hull via BMesh and optionally simplifies polygon count.
    """
    collider_name = f"UCX_{base_name}_01"

    # Remove existing collider if present
    existing = bpy.data.objects.get(collider_name)
    if existing:
        bpy.data.objects.remove(existing, do_unlink=True)

    mesh_data = bpy.data.meshes.new(f"{collider_name}_Mesh")
    collider_obj = bpy.data.objects.new(collider_name, mesh_data)
    context.collection.objects.link(collider_obj)

    # Compute Convex Hull from source object
    bm = bmesh.new()
    bm.from_mesh(source_obj.data)
    bm.verts.ensure_lookup_table()

    # Convex hull operation
    res = bmesh.ops.convex_hull(bm, input=bm.verts)

    # Delete interior and unused geometry returned by convex hull
    unused_geom = res.get('geom_interior', []) + res.get('geom_unused', []) + res.get('geom_holes', [])
    if unused_geom:
        bmesh.ops.delete(bm, geom=unused_geom, context='VERTS')

    bm.to_mesh(mesh_data)
    bm.free()

    # Simplify hull if face count is too high for real-time physics
    if len(mesh_data.polygons) > max_faces:
        mod_dec = collider_obj.modifiers.new(name="Hull_Simplify", type='DECIMATE')
        mod_dec.decimate_type = 'COLLAPSE'
        mod_dec.ratio = max(0.2, max_faces / max(1, len(mesh_data.polygons)))
        
        prev_active = context.view_layer.objects.active
        context.view_layer.objects.active = collider_obj
        collider_obj.select_set(True)
        try:
            bpy.ops.object.modifier_apply(modifier=mod_dec.name)
        except Exception:
            pass
        context.view_layer.objects.active = prev_active

    mesh_data.validate()
    mesh_data.update()

    # Visual setup for game dev viewport
    collider_obj.display_type = 'WIRE'
    collider_obj.show_wire = True
    mat = get_or_create_collider_material()
    collider_obj.data.materials.append(mat)

    return collider_obj


def generate_box_collider(context, source_obj: bpy.types.Object, base_name: str) -> bpy.types.Object:
    """
    Generates a UBX_ axis-aligned or oriented bounding box collider.
    """
    collider_name = f"UBX_{base_name}_01"

    existing = bpy.data.objects.get(collider_name)
    if existing:
        bpy.data.objects.remove(existing, do_unlink=True)

    min_co, max_co, dimensions = calculate_bounding_box(source_obj)
    center = (min_co + max_co) * 0.5

    mesh_data = bpy.data.meshes.new(f"{collider_name}_Mesh")
    collider_obj = bpy.data.objects.new(collider_name, mesh_data)
    context.collection.objects.link(collider_obj)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x = center.x + v.co.x * dimensions.x
        v.co.y = center.y + v.co.y * dimensions.y
        v.co.z = center.z + v.co.z * dimensions.z
    bm.to_mesh(mesh_data)
    bm.free()

    collider_obj.display_type = 'WIRE'
    collider_obj.show_wire = True
    mat = get_or_create_collider_material()
    collider_obj.data.materials.append(mat)

    return collider_obj
