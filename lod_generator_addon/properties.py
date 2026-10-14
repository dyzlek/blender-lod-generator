"""
Data Models and Property Groups for Procedural LOD Generator.
Declares scene-level settings and stats tracking for UI panels and operators.
"""

import math
import bpy
from bpy.props import (
    IntProperty,
    FloatProperty,
    BoolProperty,
    EnumProperty,
    StringProperty,
    CollectionProperty,
    PointerProperty,
    FloatVectorProperty,
)


def _update_preview_enabled(self, context):
    from .ui import hud_overlay
    if self.preview_enabled:
        hud_overlay.start_preview()
    else:
        hud_overlay.stop_preview()


class LODStatItem(bpy.types.PropertyGroup):
    """Stores per-level metrics for UI reporting."""
    lod_name: StringProperty(name="Nom", default="")
    lod_level: IntProperty(name="Niveau", default=0)
    triangles: IntProperty(name="Triangles", default=0)
    reduction_pct: FloatProperty(name="Réduction %", default=0.0)
    screen_threshold: FloatProperty(name="Seuil d'écran", default=0.5)
    culled_islands: IntProperty(name="Îlots culled", default=0)
    repaired_uv_faces: IntProperty(name="Faces UV réparées", default=0)
    deviation: FloatProperty(name="Écart max", default=0.0, subtype='DISTANCE', unit='LENGTH')
    error_budget: FloatProperty(name="Budget d'erreur", default=0.0, subtype='DISTANCE', unit='LENGTH')
    is_impostor: BoolProperty(name="Imposteur", default=False)


class LODGeneratorSettings(bpy.types.PropertyGroup):
    """Global parameters for procedural LOD generation pipeline."""

    # --- Général ---
    lod_count: IntProperty(
        name="Niveaux de LOD",
        description="Nombre de niveaux de détail à générer après le LOD0 (ex: 3 génère LOD1, LOD2, LOD3)",
        default=3,
        min=1,
        max=6
    )

    reduction_preset: EnumProperty(
        name="Preset de réduction",
        description="Formule mathématique de distribution polygonale",
        items=[
            ('EXPONENTIAL', "Exponentielle (0.5^k)", "Standard AAA : divise par 2 à chaque niveau (50%, 25%, 12.5%...)"),
            ('LINEAR', "Linéaire", "Décroissance linéaire régulière"),
            ('CINEMATIC', "Cinématique (0.7^k)", "Décroissance douce pour assets de héros (70%, 49%, 34%...)"),
            ('CUSTOM', "Manuel / Personnalisé", "Ratios configurés manuellement par niveau")
        ],
        default='EXPONENTIAL'
    )

    custom_lod1_ratio: FloatProperty(name="LOD1 Ratio", default=0.50, min=0.01, max=1.0, subtype='PERCENTAGE')
    custom_lod2_ratio: FloatProperty(name="LOD2 Ratio", default=0.25, min=0.01, max=1.0, subtype='PERCENTAGE')
    custom_lod3_ratio: FloatProperty(name="LOD3 Ratio", default=0.125, min=0.01, max=1.0, subtype='PERCENTAGE')
    custom_lod4_ratio: FloatProperty(name="LOD4 Ratio", default=0.06, min=0.01, max=1.0, subtype='PERCENTAGE')
    custom_lod5_ratio: FloatProperty(name="LOD5 Ratio", default=0.03, min=0.01, max=1.0, subtype='PERCENTAGE')
    custom_lod6_ratio: FloatProperty(name="LOD6 Ratio", default=0.01, min=0.005, max=1.0, subtype='PERCENTAGE')

    generation_mode: EnumProperty(
        name="Mode de génération",
        description="Stratégie de dérivation géométrique",
        items=[
            ('PARALLEL', "Parallèle (depuis LOD0)", "Précision maximale, évite l'accumulation d'erreurs (Recommandé)"),
            ('CASCADE', "Cascade (LODk-1 → LODk)", "Calcul incrémental ultra-rapide")
        ],
        default='PARALLEL'
    )

    # --- Préservation topologique & UVs ---
    pre_triangulate: BoolProperty(
        name="Pré-triangulation propre",
        description="Triangule le modèle avant décimation (Beauty + Keep Normals) pour éliminer les N-Gons concaves et stries codes-barres",
        default=True
    )

    use_delimit_uv: BoolProperty(
        name="Verrouillage Delimit UV",
        description="Active la barrière topologique C++ native delimit={'UV', 'MATERIAL', 'SEAM'} sur Decimate Collapse pour interdire les fusions à travers les coutures",
        default=True
    )

    uv_border_curvature: BoolProperty(
        name="Courbure 2D des Bordures UV",
        description="Détecte les virages dans l'espace UV (> 5°) pour verrouiller les courbes concaves et empêcher les débordements hors texture",
        default=True
    )

    bvh_repair_uv: BoolProperty(
        name="Réparation Barycentrique BVH",
        description="Détecte les triangles UV écrasés (Aire ≈ 0) ou retournés et répare leurs coordonnées via projection barycentrique depuis le LOD0",
        default=True
    )

    split_weld_isolation: BoolProperty(
        name="Isolation Split-Decimate-Weld",
        description="Isole physiquement les îlots UV par Edge Split temporaire avant décimation et ressoude à distance infime",
        default=False
    )

    preserve_uv_seams: FloatProperty(
        name="Coutures UV",
        description="Poids de protection injecté sur les sommets des coutures UV (W_uv)",
        default=0.85,
        min=0.0,
        max=1.0
    )

    preserve_silhouette: FloatProperty(
        name="Silhouette & Angles",
        description="Poids de préservation des arêtes vives et de la courbure dièdre (W_curve)",
        default=0.75,
        min=0.0,
        max=1.0
    )

    silhouette_angle: FloatProperty(
        name="Angle silhouette seuil",
        description="Angle dièdre minimal pour considérer une arête comme structurante",
        default=math.radians(35.0),
        min=math.radians(5.0),
        max=math.radians(90.0),
        subtype='ANGLE'
    )

    preserve_open_boundaries: FloatProperty(
        name="Bordures ouvertes",
        description="Poids de protection absolue des arêtes de bordures (W_boundary)",
        default=1.0,
        min=0.0,
        max=1.0
    )

    preserve_material_borders: FloatProperty(
        name="Frontières de matériaux",
        description="Poids de protection des délimitations entre matériaux distincts",
        default=0.90,
        min=0.0,
        max=1.0
    )

    preserve_skinning: FloatProperty(
        name="Articulations / Skinning",
        description="Poids de protection des articulations d'armature (coudes, genoux)",
        default=0.70,
        min=0.0,
        max=1.0
    )

    relax_weights_per_lod: BoolProperty(
        name="Relâchement par LOD",
        description="Adoucit progressivement les contraintes UV et courbures sur LOD2 (W^2) et LOD3+",
        default=True
    )

    # --- Optimisation & Culling ---
    pre_planar_dissolve: BoolProperty(
        name="Pré-nettoyage planaire",
        description="Dissout les faces coplanaires avant QEM uniquement pour les surfaces architecturales plates (Désactiver sur surfaces organiques/courbes)",
        default=False
    )

    planar_angle_limit: FloatProperty(
        name="Angle planaire limite",
        description="Angle maximal entre faces pour la dissolution planaire (<= 0.5° pour éviter de courber les N-Gons)",
        default=math.radians(0.5),
        min=math.radians(0.1),
        max=math.radians(5.0),
        subtype='ANGLE'
    )

    cull_small_parts: BoolProperty(
        name="Culling petits fragments",
        description="Supprime les îlots détachés isolés (vis, rivets, boulons) sur LOD2 et LOD3+",
        default=True
    )

    cull_threshold: FloatProperty(
        name="Seuil culling (%)",
        description="Rayon relatif maximal d'un îlot pour être supprimé sur LOD2 (%)",
        default=2.5,
        min=0.1,
        max=25.0
    )

    # --- Ombrage & Normales ---
    normal_strategy: EnumProperty(
        name="Stratégie de normales",
        description="Méthode de restauration de l'ombrage pour éliminer le popping lumineux",
        items=[
            ('TRANSFER', "Data Transfer (LOD0)", "Projette les Custom Split Normals du LOD0 (Recommandé)"),
            ('WEIGHTED', "Weighted Normals", "Génère des normales pondérées par l'aire"),
            ('NONE', "Aucun", "Normales standard recalculées")
        ],
        default='TRANSFER'
    )

    # --- Sortie Moteur & Colliders ---
    setup_lod_group: BoolProperty(
        name="Hiérarchie 'LodGroup'",
        description="Crée un parent Empty configuré avec fbx_type = 'LodGroup' (reconnu par Unreal et Unity)",
        default=True
    )

    generate_collider: BoolProperty(
        name="Générer Collider",
        description="Crée automatiquement une enveloppe de collision optimisée pour moteur",
        default=False
    )

    collider_type: EnumProperty(
        name="Type de collider",
        items=[
            ('UCX', "UCX_ (Convex Hull)", "Enveloppe convexe pour Unreal Engine et Unity"),
            ('UBX', "UBX_ (Boîte orientée)", "Boîte englobante orientée")
        ],
        default='UCX'
    )

    # --- Visualisation & Viewport ---
    is_exploded: BoolProperty(
        name="Vue éclatée",
        description="Aligne les LODs côte à côte sur l'axe X pour comparaison visuelle",
        default=False
    )

    explode_spacing: FloatProperty(
        name="Espacement vue éclatée",
        description="Facteur d'espacement basé sur la largeur de la bounding box",
        default=1.5,
        min=0.5,
        max=5.0
    )

    is_distance_sim_active: BoolProperty(
        name="Simulation distance active",
        default=False
    )

    hud_overlay_enabled: BoolProperty(
        name="HUD statistiques viewport",
        description="Affiche le compteur de triangles et le gain mémoire dans le viewport 3D",
        default=False
    )

    keep_protection_mask: BoolProperty(
        name="Conserver masque de protection",
        description="Conserve le Vertex Group '_LOD_Protection_Mask' sur les meshes pour debug",
        default=False
    )

    # --- Exportation ---
    export_path: StringProperty(
        name="Dossier d'export",
        description="Dossier de destination pour les fichiers exportés",
        subtype='DIR_PATH',
        default="//"
    )

    export_format: EnumProperty(
        name="Format d'export",
        items=[
            ('FBX', "FBX (.fbx)", "Standard Unreal Engine & Unity avec LodGroup"),
            ('GLTF', "glTF (.glb)", "Standard Godot & WebXR")
        ],
        default='FBX'
    )

    # --- Mode de réduction : pourcentage, budget d'erreur ou taille écran ---
    reduction_mode: EnumProperty(
        name="Mode de réduction",
        description="Comment la quantité de triangles de chaque LOD est déterminée",
        items=[
            ('RATIO', "Pourcentage", "Ratio de triangles par niveau (preset ci-dessus)"),
            ('ERROR', "Budget d'erreur", "Réduit tant que l'écart géométrique avec LOD0 reste sous un seuil"),
            ('SCREEN', "Taille écran", "Écart toléré déduit du seuil d'écran de chaque LOD et d'une erreur en pixels"),
        ],
        default='RATIO'
    )

    max_error: FloatProperty(
        name="Erreur max (LOD1)",
        description="Écart géométrique maximal toléré pour le LOD1 (distance de Hausdorff avec LOD0)",
        default=0.002,
        min=0.00001,
        soft_max=0.1,
        precision=4,
        subtype='DISTANCE',
        unit='LENGTH'
    )

    error_growth: FloatProperty(
        name="Croissance par niveau",
        description="Multiplicateur de l'erreur tolérée à chaque niveau (x2 : 2 mm, 4 mm, 8 mm...)",
        default=2.0,
        min=1.0,
        max=8.0
    )

    pixel_error: FloatProperty(
        name="Erreur écran (px)",
        description="Écart maximal toléré, en pixels, au seuil d'écran de chaque LOD",
        default=1.0,
        min=0.1,
        max=20.0
    )

    screen_height_px: IntProperty(
        name="Hauteur écran (px)",
        description="Résolution verticale de référence pour le mode Taille écran",
        default=1080,
        min=240,
        max=8640
    )

    # --- Remesh avant décimation ---
    remesh_mode: EnumProperty(
        name="Remesh",
        description="Remaillage avant décimation pour les modèles sales (scans, sculpts, kitbash)",
        items=[
            ('NONE', "Aucun", "Décime directement la topologie d'origine"),
            ('VOXEL', "Voxel", "Fusionne les îlots et referme la surface"),
            ('QUADRIFLOW', "QuadriFlow", "Remaillage en quads propres (repli sur Voxel en cas d'échec)"),
        ],
        default='NONE'
    )

    remesh_voxel_pct: FloatProperty(
        name="Taille voxel (%)",
        description="Taille de voxel en pourcentage du rayon englobant",
        default=1.0,
        min=0.1,
        max=10.0
    )

    remesh_quadriflow_ratio: FloatProperty(
        name="Faces QuadriFlow",
        description="Nombre de faces cible relatif à la source",
        default=0.5,
        min=0.01,
        max=1.0,
        subtype='FACTOR'
    )

    remesh_from_level: IntProperty(
        name="À partir du LOD",
        description="Premier niveau remaillé (LOD0 n'est jamais modifié)",
        default=1,
        min=1,
        max=6
    )

    # --- Bake des normales et de l'AO ---
    bake_enabled: BoolProperty(
        name="Bake normales & AO",
        description="Bake les détails de LOD0 (normal map, occlusion) sur les LODs lointains",
        default=False
    )

    bake_from_level: IntProperty(
        name="À partir du LOD",
        description="Premier niveau baké",
        default=3,
        min=1,
        max=6
    )

    bake_resolution: EnumProperty(
        name="Résolution",
        items=[(str(r), f"{r} px", "") for r in (256, 512, 1024, 2048, 4096)],
        default='1024'
    )

    bake_ao: BoolProperty(
        name="Ambient Occlusion",
        description="Bake aussi l'occlusion ambiante de LOD0 (multipliée à la couleur de base)",
        default=True
    )

    bake_ao_samples: IntProperty(
        name="Échantillons AO",
        default=16,
        min=1,
        max=1024
    )

    bake_dedicated_uv: BoolProperty(
        name="UVs dédiées au bake",
        description="Crée un calque UV sans chevauchement (Smart UV Project) pour les maps bakées",
        default=False
    )

    # --- Imposteur billboard ---
    impostor_enabled: BoolProperty(
        name="Imposteur (dernier LOD)",
        description="Ajoute un dernier niveau : billboard texturé capturé sous plusieurs angles",
        default=False
    )

    impostor_views: EnumProperty(
        name="Vues",
        items=[('8', "8 angles", ""), ('16', "16 angles", "")],
        default='8'
    )

    impostor_resolution: IntProperty(
        name="Résolution par vue",
        default=256,
        min=32,
        max=2048
    )

    impostor_engine: EnumProperty(
        name="Moteur de capture",
        items=[
            ('WORKBENCH', "Workbench", "Rapide, couleurs et textures sans éclairage réaliste"),
            ('EEVEE', "EEVEE", "Fidèle aux matériaux"),
        ],
        default='EEVEE'
    )

    # --- Aperçu en direct (wireframe superposé à LOD0) ---
    preview_enabled: BoolProperty(
        name="Aperçu en direct",
        description="Dessine en wireframe le LOD choisi par-dessus l'objet actif, recalculé à chaque changement de réglage",
        default=False,
        update=_update_preview_enabled
    )

    preview_level: IntProperty(
        name="Niveau d'aperçu",
        description="Niveau de LOD prévisualisé",
        default=1,
        min=1,
        max=6
    )

    preview_color: FloatVectorProperty(
        name="Couleur",
        description="Couleur du wireframe d'aperçu",
        subtype='COLOR_GAMMA',
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 0.55, 0.1, 0.9)
    )

    # --- Progression de la génération (opérateur modal) ---
    is_generating: BoolProperty(default=False)
    generation_progress: FloatProperty(default=0.0, min=0.0, max=1.0, subtype='FACTOR')
    generation_label: StringProperty(default="")
    cancel_requested: BoolProperty(default=False)

    cancel_keep_completed: BoolProperty(
        name="Garder les LODs terminés si annulé",
        description="En cas d'annulation, conserve les niveaux déjà générés (sinon tout est retiré)",
        default=True
    )

    # --- Données de session ---
    stats_items: CollectionProperty(type=LODStatItem)
    base_asset_name: StringProperty(default="")
    lod0_initial_triangles: IntProperty(default=0)


classes = (
    LODStatItem,
    LODGeneratorSettings,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.lod_generator_settings = PointerProperty(type=LODGeneratorSettings)


def unregister():
    if hasattr(bpy.types.Scene, "lod_generator_settings"):
        del bpy.types.Scene.lod_generator_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
