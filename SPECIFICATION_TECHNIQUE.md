# Spécification Technique & Architecture : Générateur de LODs Procédural pour le Jeu Vidéo (Blender)

Ce document définit l'architecture complète, les fondements mathématiques, les algorithmes topologiques et le pipeline d'exécution pour concevoir un add-on Blender professionnel de génération de **LODs (Levels of Detail)** destiné aux moteurs de jeu (**Unreal Engine 5**, **Unity URP/HDRP**, **Godot 4.x**, **WebXR/Three.js**).

---

## 1. Vision et Problématique Métier

Un simple modificateur *Decimate* dans Blender détruit rapidement un maillage destiné au jeu vidéo : les coutures UV (*UV Seams*) s'étirent, les bordures de matériaux bavent, les normales de surface s'effondrent (créant des artefacts noirs avec les *Normal Maps* tangentielles), et la silhouette générale perd son volume.

Pour rivaliser avec des solutions industrielles comme **Simplygon** ou **InstaLOD**, l'application agit comme un pipeline intelligent en cinq phases :

1. **Pré-triangulation & Audit Topologique** (triangulation propre préalable, analyse d'échelle, extraction des îlots géométriques par parcours BFS).
2. **Pondération Dynamique & Analyse de Courbure UV** (génération d'une carte d'importance par sommet combinant courbures 3D et virages 2D dans l'espace UV).
3. **Simplification Sécurisée & Verrouillage Topologique** (décimation native avec `delimit = {'UV', 'MATERIAL', 'SEAM'}`, QEM guidé, isolation physique temporaire *Split -> Decimate -> Weld*).
4. **Audit Post-Décimation & Réparation Barycentrique des UVs** (détection d'aires signées dégénérées ou retournées, reconstruction barycentrique via *BVH Tree* depuis le LOD0).
5. **Restauration de l'Ombrage & Conditionnement Moteur** (transfert des *Custom Split Normals*, hiérarchie `LodGroup`, calcul des seuils d'écran, génération de *Colliders* physiques).

---

## 2. Fondements Algorithmiques et Mathématiques

### A. L'Algorithme QEM (Quadric Error Metrics — Garland & Heckbert)

L'algorithme de contraction d'arêtes (*Edge Collapse*) fusionne deux sommets $v_1$ et $v_2$ en un nouveau sommet $\bar{v}$ tout en minimisant la déformation géométrique par rapport à la surface d'origine :

1. **Définition des plans incidents :** Chaque triangle définit un plan $p = [a, b, c, d]^T$ avec $a^2 + b^2 + c^2 = 1$.
2. **Matrice d'erreur fondamentale ($K_p$) :**
$$K_p = p \cdot p^T = \begin{bmatrix} a^2 & ab & ac & ad \\ ab & b^2 & bc & bd \\ ac & bc & c^2 & cd \\ ad & bd & cd & d^2 \end{bmatrix}$$
3. **Quadrique par sommet ($Q_v$) :** $Q_v = \sum_{p \in \text{plans}(v)} K_p$
4. **Coût de contraction :** $\Delta(\bar{v}) = \bar{v}^T (Q_1 + Q_2) \bar{v}$
5. **Injection du masque de protection :** Le modificateur nativement C++ `DECIMATE` (mode *Collapse*) de Blender est piloté par un *Vertex Group* (`_LOD_Protection_Mask`) avec `invert_vertex_group = True`. Un poids de $1.0$ (rouge) protège le sommet contre la réduction, tandis qu'un poids de $0.0$ (bleu) le rend prioritaire pour la contraction.

---

### B. Analyse de Courbure 3D & Préservation de la Silhouette

Pour chaque arête $e$ reliant deux faces de normales $\vec{n}_1$ et $\vec{n}_2$, on calcule l'angle dièdre :
$$\theta = \arccos(\text{clamp}(\vec{n}_1 \cdot \vec{n}_2, -1.0, 1.0))$$
Si $\theta \ge \theta_{\text{seuil}}$ (ex: $35^\circ$), le poids attribué est :
$$W_{\text{curve}} = \min\left(1.0, \frac{\theta}{\pi / 2}\right) \cdot \alpha_{\text{silhouette}}$$

---

### C. Stratégies de Réduction Multi-Niveaux (LOD0 à LOD4)

| Niveau | Cible Typique | Algorithme Principal | Logique Topologique |
| --- | --- | --- | --- |
| **LOD 0** | 100 % (Source) | Triangulation propre + Audit | Mesh d'origine nettoyé, échelle appliquée, normales verrouillées. |
| **LOD 1** | 50 % à 75 % | **QEM Sécurisé (`delimit`)** | Verrouillage strict des UVs, coutures et matériaux via `delimit = {'UV', 'MATERIAL', 'SEAM'}`. |
| **LOD 2** | 25 % à 40 % | **QEM Guidé + Culling Îlots** | Suppression des petits fragments isolés ($< 2.5\%$), assouplissement $W' = W^2$. |
| **LOD 3** | 10 % à 15 % | **QEM Agressif + Réparation BVH** | Culling renforcé ($< 5\%$), réparation barycentrique des triangles étirés. |
| **LOD 4** | Moins de 1 % | **Imposteur / Boîte / Low-Poly** | Remplacement par plans ou boîte simplifiée pour l'extrême distance. |

---

## 3. Module Complémentaire : Préservation des UVs & Éradication des Artefacts de Texture

### A. Diagnostic des Artefacts Visuels de Décimation

Lors d'une décimation automatique sans garde-fous stricts, deux familles de défauts majeurs surviennent :

1. **Déchirures triangulaires / en losange (UV Seam Bleeding & Pont d'Îlots UV) :**
   - *Observation :* Apparition de larges triangles sombres traversant des tranches courbées (ex: haut d'un dossier de chaise, montants).
   - *Cause mathématique :* Un sommet 3D sur une couture possède plusieurs coordonnées 2D $(u, v)$ (une par `BMLoop`). Si l'arête contractée est à cheval sur la couture, un triangle 3D se retrouve avec deux sommets dans un îlot UV et le troisième dans un autre. Le triangle traverse toute la texture en diagonale.
2. **Micro-rectangles striés façon "Code-Barres" :**
   - *Observation :* Rectangles aux lignes verticales étirées sur les surfaces cintrées.
   - *Cause mathématique :* Dégénérescence de coordonnées UV où les 3 sommets du triangle sont quasi alignés sur une dimension (ex: même coordonnée $V$), étirant une unique ligne de pixels sur toute la largeur $U$. Provoqué par la triangulation désordonnée de N-Gons après un *Planar Dissolve* inadapté.

---

### B. Les 5 Règles Algorithmiques Anti-Artefacts

#### 1. Verrouillage Natif `delimit = {'UV', 'MATERIAL', 'SEAM'}` sur le Collapse
Un simple Vertex Group ne suffit pas : avec un ratio agressif, Blender finit par contracter les sommets de poids $1.0$.
L'activation obligatoire de `delimit = {'UV', 'MATERIAL', 'SEAM'}` sur le modificateur Decimate (mode Collapse) agit en C++ comme une **barrière topologique absolue** : il est strictement interdit de fusionner des sommets appartenant à des îlots UV distincts.

#### 2. L'Algorithme "Split -> Decimate -> Weld" (Isolation Physique Temporaire)
Pour garantir qu'aucun sommet ne saute d'un îlot UV à un autre sur des courbes complexes :
1. **Edge Split sur coutures :** Toute arête séparant des loops aux coordonnées UV disjointes est marquée `edge.seam = True` et physiquement découpée (`bmesh.ops.split_edges`). Chaque îlot UV devient un maillage 3D temporairement indépendant.
2. **Poids 1.0 sur les nouvelles bordures :** Ces arêtes devenues ouvertes reçoivent un poids de $1.0$ pour que les bords ne s'écartent pas.
3. **Decimate Collapse :** La décimation QEM s'exécute à l'intérieur de chaque îlot sans jamais pouvoir déborder.
4. **Re-soudure finale (Weld) :** Reconstitution de la continuité 3D par fusion de sommets à distance infime ($\epsilon = 0.00001\text{ m}$) en conservant les normales intactes.

#### 3. Contrôle de la Triangulation & Ban du Planar Dissolve sur Surfaces Courbes
- **Pré-Triangulation systématique du LOD0 :** Avant le calcul des poids et la décimation, application d'un modificateur `TRIANGULATE` (`quad_method='BEAUTY'`, `ngon_method='CLIP'`, `keep_custom_normals=True`). Le solveur QEM travaille ainsi sur les vraies diagonales géométriques sans générer de retournement de face inattendu.
- **Désactivation du Planar Dissolve sur surfaces organiques :** La passe planaire n'est autorisée que si l'angle limite est strictement inférieur à $0.5^\circ$ (pièces architecturales plates), évitant la formation de N-Gons concaves sur les cylindres et dossiers courbes.

#### 4. Détection Mathématique des "UV Foldovers" & Réparation par BVH Tree
Pour chaque triangle du maillage réduit :
1. **Calcul de l'Aire Signée 2D :**
$$\text{Aire\_UV} = \frac{1}{2} \left( (u_2 - u_1)(v_3 - v_1) - (v_2 - v_1)(u_3 - u_1) \right)$$
2. **Diagnostic :**
   - Si $|\text{Aire\_UV}| < 10^{-6}$ alors que l'aire 3D est $> 10^{-5}$ : triangle UV écrasé (strie code-barres).
   - Si le signe de $\text{Aire\_UV}$ s'inverse par rapport aux faces voisines : triangle retourné (*UV Foldover*).
3. **Réparation Automatique par BVH Tree :**
   Un `mathutils.bvhtree.BVHTree` est instancié sur le LOD0 source. Pour chaque coin (`BMLoop`) de la face dégradée, le script projette un rayon sur le LOD0, identifie la face originale la plus proche et calcule la coordonnée $(u, v)$ par interpolation barycentrique exacte :
   $$UV_P = \text{barycentric\_transform}(P, A, B, C, UV_A, UV_B, UV_C)$$
   La coordonnée corrompue du LOD est immédiatement écrasée par la valeur haute fidélité.

#### 5. Protection de la Courbure 2D dans l'Espace UV (Virages de Bordures)
Lors du calcul du masque de pondération :
1. Identifier toutes les arêtes situées sur le contour d'un îlot UV.
2. Pour chaque sommet sur cette bordure UV, mesurer la déviation angulaire entre les deux segments UV incidents $\vec{e}_{uv1}$ et $\vec{e}_{uv2}$.
3. Si la bordure UV tourne (déviation $> 5^\circ$, comme sur un dossier courbé), verrouiller le sommet avec un poids maximal ($1.0$). Si la bordure est une ligne droite ($< 5^\circ$), autoriser la contraction le long de cette ligne droite.

---

## 4. Pipeline d'Exécution Mis à Jour (Séquence Complète)

```text
[Mesh Source LOD0]
       │
       ▼
1. PRÉ-TRIANGULATION (Modificateur Triangulate : Beauty + Clip + Keep Normals)
   └─> Élimine tout quad non-planaire et prévient les N-Gons concaves.
       │
       ▼
2. AUDIT & ANALYSE UV / SILHOUETTE (BMesh)
   ├─> Détection des discontinuités UV et marquage `edge.seam = True`.
   ├─> Mesure de la courbure 3D (dièdre) et de la courbure 2D (virages de bordures UV).
   └─> Génération du Vertex Group `_LOD_Protection_Mask`.
       │
       ▼
3. CULLING D'ÎLOTS GÉOMÉTRIQUES (LOD2 / LOD3+)
   └─> Suppression des micro-éléments détachés (rivets, vis) sous le seuil relatif (2.5% à 5%).
       │
       ▼
4. DÉCIMATION SÉCURISÉE (Modificateur Decimate : Collapse)
   ├─> Activation stricte de `delimit = {'UV', 'MATERIAL', 'SEAM'}`.
   ├─> Option d'isolation physique Split-Decimate-Weld si requise.
   └─> Injection du Vertex Group inversé pour piloter la file de priorité QEM.
       │
       ▼
5. RÉPARATION POST-DÉCIMATION DES UVs (BVH Tree LOD0)
   ├─> Calcul de l'aire signée 2D de chaque triangle UV.
   └─> Restauration barycentrique des coordonnées des faces écrasées ou retournées.
       │
       ▼
6. RESTAURATION DE L'OMBRAGE (Modificateur Data Transfer)
   └─> Projection des Custom Split Normals du LOD0 (Nearest Face Interpolated).
       │
       ▼
7. CONDITIONNEMENT MOTEUR (Empty 'LodGroup' & Colliders)
   └─> Hiérarchie prête pour import automatique dans Unreal Engine et Unity.
```

---

## 5. Organisation des Fichiers & Modèle de Données

```text
lod_generator_addon/
├── __init__.py               # Déclaration bl_info et chargement des modules
├── properties.py             # Propriétés UI et modèles de données
├── core/
│   ├── analyzer.py           # Audit topologique, détection BFS d'îlots
│   ├── weight_mapper.py      # Poids de protection 3D et virages 2D UV
│   ├── decimator.py          # Pile complète : Triangulate -> Delimit -> Decimate -> BVH Repair
│   └── collider_gen.py       # Colliders UCX_ (Convex Hull) et UBX_ (Box)
├── utils/
│   ├── math_helpers.py       # Bounding volumes, triangles triangulés, aires signées 2D
│   └── hierarchy.py          # Empty 'LodGroup', collections et vue éclatée
├── operators/
│   ├── op_generate.py        # Opérateur principal et mode Weight Paint
│   ├── op_preview.py         # Vue éclatée et simulation interactive de distance caméra
│   └── op_export.py          # Exportateur FBX / glTF
└── ui/
    ├── panels.py             # N-Panel dans la 3D View
    └── hud_overlay.py        # Overlay GPU / BLF dans le viewport
```

---

## 6. Guide d'Intégration Moteur

- **Unreal Engine 5 :** Le glisser-déposer du `.fbx` généré configure automatiquement le composant `LodGroup` avec les niveaux LOD0..LOD3 et le collider `UCX_`.
- **Unity (URP/HDRP) :** Crée automatiquement un composant `LOD Group` avec les seuils de distance calculés.
- **Godot 4.x :** Import direct du `.glb` avec reconnaissance native des suffixes `_LOD0`, `_LOD1` et des collisions.
