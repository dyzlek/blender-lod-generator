"""
Topological Analysis and Mesh Audit Module for LOD Generator.
Performs sanity checks, scale verification, non-manifold checks,
loose geometry detection, and connected components (islands) partitioning via BFS.
"""

from collections import deque
from dataclasses import dataclass, field
import bmesh
import bpy
from mathutils import Vector
from ..utils.math_helpers import get_triangulated_face_count, calculate_coords_bounding_sphere


@dataclass
class MeshAuditResult:
    """Holds complete topological audit details of an inspected mesh."""
    is_valid: bool = False
    name: str = ""
    vertex_count: int = 0
    polygon_count: int = 0
    triangle_count: int = 0
    uv_layer_count: int = 0
    uv_layers: list[str] = field(default_factory=list)
    has_armature: bool = False
    armature_names: list[str] = field(default_factory=list)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    is_uniform_scale: bool = True
    loose_vertices: int = 0
    loose_edges: int = 0
    open_boundary_edges: int = 0
    non_manifold_edges: int = 0
    island_count: int = 0
    dimensions: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounding_radius: float = 0.0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def audit_mesh(obj: bpy.types.Object) -> MeshAuditResult:
    """
    Executes a complete inspection of the target object before LOD generation.
    Validates type, geometry, transformations, UV maps, and topology.
    """
    result = MeshAuditResult()
    if not obj:
        result.errors.append("Aucun objet spécifié pour l'analyse.")
        return result

    result.name = obj.name

    if obj.type != 'MESH':
        result.errors.append(f"L'objet '{obj.name}' n'est pas un MESH (type actuel: {obj.type}).")
        return result

    mesh = obj.data
    if not mesh or len(mesh.vertices) == 0:
        result.errors.append(f"Le maillage de '{obj.name}' ne contient aucun sommet.")
        return result

    result.vertex_count = len(mesh.vertices)
    result.polygon_count = len(mesh.polygons)
    result.triangle_count = get_triangulated_face_count(obj)

    # Check Scale
    s = obj.scale
    result.scale = (round(s.x, 4), round(s.y, 4), round(s.z, 4))
    scale_uniform = (abs(s.x - s.y) < 1e-4) and (abs(s.y - s.z) < 1e-4) and (abs(s.x - 1.0) < 1e-4)
    result.is_uniform_scale = scale_uniform
    if not scale_uniform:
        result.warnings.append(
            f"L'échelle locale de '{obj.name}' n'est pas appliquée ({s.x:.2f}, {s.y:.2f}, {s.z:.2f}). "
            "Les distances QEM et angles dièdres peuvent être déformés. L'échelle sera normalisée lors de la copie."
        )

    # Check UVs
    result.uv_layer_count = len(mesh.uv_layers)
    result.uv_layers = [layer.name for layer in mesh.uv_layers]
    if result.uv_layer_count == 0:
        result.warnings.append(f"L'objet '{obj.name}' ne possède aucune carte UV.")

    # Check Dimensions & Bounding
    dims = obj.dimensions
    result.dimensions = (round(dims.x, 4), round(dims.y, 4), round(dims.z, 4))
    if dims.length < 1e-5:
        result.errors.append(f"L'objet '{obj.name}' a un volume nul ou quasiment nul.")
        return result

    # Check Armature / Skinning
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object:
            result.has_armature = True
            result.armature_names.append(mod.object.name)

    # Inspect Topology via temporary BMesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    loose_v = 0
    for v in bm.verts:
        if len(v.link_edges) == 0:
            loose_v += 1
    result.loose_vertices = loose_v

    loose_e = 0
    boundary_e = 0
    non_manifold_e = 0
    for e in bm.edges:
        face_count = len(e.link_faces)
        if face_count == 0:
            loose_e += 1
        elif face_count == 1:
            boundary_e += 1
        elif face_count > 2:
            non_manifold_e += 1

    result.loose_edges = loose_e
    result.open_boundary_edges = boundary_e
    result.non_manifold_edges = non_manifold_e

    if non_manifold_e > 0:
        result.warnings.append(
            f"Détection de {non_manifold_e} arêtes non-manifold (plus de 2 faces incidentes)."
        )

    # Connected islands count
    islands = find_connected_islands(bm)
    result.island_count = len(islands)

    coords = [v.co for v in bm.verts]
    _, radius = calculate_coords_bounding_sphere(coords)
    result.bounding_radius = radius

    bm.free()

    result.is_valid = (len(result.errors) == 0)
    return result


def find_connected_islands(bm: bmesh.types.BMesh) -> list[list[bmesh.types.BMVert]]:
    """
    Partitions vertices of a BMesh into connected components (mesh islands)
    using Breadth-First Search (BFS) graph traversal over BMVert.link_edges.
    """
    visited = set()
    islands = []

    for start_vert in bm.verts:
        if start_vert in visited:
            continue

        island = []
        queue = deque([start_vert])
        visited.add(start_vert)

        while queue:
            current = queue.popleft()
            island.append(current)

            for edge in current.link_edges:
                other = edge.other_vert(current)
                if other and other not in visited:
                    visited.add(other)
                    queue.append(other)

        islands.append(island)

    return islands


def clean_mesh_topology(bm: bmesh.types.BMesh, merge_distance: float = 0.0001) -> dict:
    """
    Cleans up duplicate vertices, loose edges, and unlinked vertices on a BMesh.
    Returns a summary dictionary of removed elements.
    """
    initial_v = len(bm.verts)
    initial_e = len(bm.edges)

    # 1. Merge vertices within epsilon
    if merge_distance > 0.0:
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_distance)

    # 2. Delete loose edges (edges without faces)
    loose_edges = [e for e in bm.edges if len(e.link_faces) == 0]
    if loose_edges:
        bmesh.ops.delete(bm, geom=loose_edges, context='EDGES')

    # 3. Delete loose vertices (verts without edges)
    loose_verts = [v for v in bm.verts if len(v.link_edges) == 0]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    return {
        "verts_removed": initial_v - len(bm.verts),
        "edges_removed": initial_e - len(bm.edges)
    }
