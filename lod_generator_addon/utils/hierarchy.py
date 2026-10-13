"""
Hierarchy and Scene Organization for LOD Generator.
Manages collections, Empty 'LodGroup' parenting for FBX/glTF export,
and exploded view layouts.
"""

import bpy
from mathutils import Vector, Matrix
from .math_helpers import calculate_bounding_box


def get_base_name(obj_name: str) -> str:
    """
    Extracts a clean base name from an object name by stripping existing _LOD suffixes.
    e.g. 'Hero_Armor_LOD0' -> 'Hero_Armor'
    """
    name = obj_name
    for i in range(10):
        suffix = f"_LOD{i}"
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
        suffix_lower = f"_lod{i}"
        if name.endswith(suffix_lower):
            name = name[:-len(suffix_lower)]
            break
    return name


def get_or_create_collection(context, collection_name: str) -> bpy.types.Collection:
    """
    Retrieves an existing collection or creates a new one linked to the scene.
    """
    scene = context.scene
    if collection_name in bpy.data.collections:
        col = bpy.data.collections[collection_name]
    else:
        col = bpy.data.collections.new(collection_name)
        scene.collection.children.link(col)
    return col


def move_to_collection(obj: bpy.types.Object, target_col: bpy.types.Collection):
    """
    Moves an object to the target collection and unlinks it from all other collections.
    """
    for col in list(obj.users_collection):
        if col != target_col:
            col.objects.unlink(obj)
    if obj.name not in target_col.objects:
        target_col.objects.link(obj)


def setup_lod_group_empty(context, base_name: str, lod_objects: list[bpy.types.Object], collider_obj=None) -> bpy.types.Object:
    """
    Creates an Empty configured as an FBX 'LodGroup' parent.
    All LODs are strictly parented under this Empty with local matrices reset to identity.
    """
    if not lod_objects:
        return None

    lod0 = lod_objects[0]
    world_matrix = lod0.matrix_world.copy()

    # Determine collection
    col_name = f"{base_name}_LODs"
    collection = get_or_create_collection(context, col_name)

    # Check for existing empty
    empty_name = f"LODGroup_{base_name}"
    empty_obj = bpy.data.objects.get(empty_name)
    if not empty_obj:
        empty_obj = bpy.data.objects.new(empty_name, None)
        empty_obj.empty_display_type = 'ARROWS'
        empty_obj.empty_display_size = 0.5
        collection.objects.link(empty_obj)
    
    empty_obj.matrix_world = world_matrix
    empty_obj["fbx_type"] = "LodGroup"

    # Parent all LODs in strict order
    for idx, obj in enumerate(lod_objects):
        move_to_collection(obj, collection)
        # Store inverse world matrix so relative transform becomes identity
        obj.parent = empty_obj
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.location = Vector((0.0, 0.0, 0.0))
        obj.rotation_euler = Vector((0.0, 0.0, 0.0))
        obj.scale = Vector((1.0, 1.0, 1.0))

    if collider_obj:
        move_to_collection(collider_obj, collection)
        collider_obj.parent = empty_obj
        collider_obj.matrix_parent_inverse = Matrix.Identity(4)
        collider_obj.location = Vector((0.0, 0.0, 0.0))
        collider_obj.rotation_euler = Vector((0.0, 0.0, 0.0))
        collider_obj.scale = Vector((1.0, 1.0, 1.0))

    return empty_obj


def apply_exploded_view(lod_objects: list[bpy.types.Object], spacing_factor: float = 1.5):
    """
    Offsets each LOD along the X axis for visual side-by-side inspection in the 3D viewport.
    """
    if not lod_objects:
        return

    # Calculate width of LOD0
    lod0 = lod_objects[0]
    min_co, max_co, dimensions = calculate_bounding_box(lod0)
    width = dimensions.x
    if width < 0.1:
        width = (dimensions.y + dimensions.z) * 0.5
    if width < 0.1:
        width = 1.0

    offset_step = width * spacing_factor

    for idx, obj in enumerate(lod_objects):
        obj.location.x = idx * offset_step
        obj.location.y = 0.0
        obj.location.z = 0.0


def reset_exploded_view(lod_objects: list[bpy.types.Object]):
    """
    Resets all LOD objects back to center location (0, 0, 0).
    """
    for obj in lod_objects:
        obj.location = Vector((0.0, 0.0, 0.0))
