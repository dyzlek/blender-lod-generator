# Procedural LOD Generator Pro pour Blender

Add-on professionnel complet pour **Blender 4.x et 5.x** permettant de générer automatiquement des chaînes de **LODs (Levels of Detail)** optimisées pour les moteurs de jeu vidéo (**Unreal Engine 5**, **Unity URP/HDRP**, **Godot 4.x**, **WebXR / Three.js**).

---

## Architecture et Pipeline

Contrairement à un simple modificateur Decimate qui étire les textures, détruit les coutures UV et corrompt les normales de surface, ce générateur opère selon un pipeline en 5 phases :

1. **Pré-triangulation et Audit Topologique :**
   - **Pré-triangulation du LOD0 (`BEAUTY` + `CLIP` + `Keep Normals`)** : Élimine les quads non planaires et N-gons concaves avant la réduction pour prévenir l'apparition de distorsions et stries d'étirement.
   - Contrôle d'échelle non uniforme.
   - Partitionnement en îlots géométriques par parcours de graphe **BFS** (*Breadth-First Search*).

2. **Pondération Dynamique et Détection de Courbure 2D UV :**
   - Calcul vectoriel par sommet $W_i \in [0.0, 1.0]$ combinant 6 critères :
     - **Bordures ouvertes** ($W = 1.0$) : Évite la formation de trous sur les surfaces non fermées.
     - **Coutures UV et ruptures** ($W = 0.85$) : Verrouille les coutures de texture pour éliminer tout étirement d'UV.
     - **Courbure 2D des bordures d'îlots UV** ($W = 1.0$) : Mesure l'angle dans le plan UV. Si le bord présente une courbure ($> 5^\circ$), verrouille le sommet pour préserver la continuité du dépliage.
     - **Frontières de matériaux** ($W = 0.90$) : Préserve les transitions entre shaders.
     - **Silhouette et angles dièdres** ($W = 0.75 \times \text{ratio}$) : Détecte les arêtes vives 3D ($\theta = \arccos(\vec{n}_1 \cdot \vec{n}_2)$).
     - **Articulations de Skinning** ($W = 0.70$) : Protège les boucles d'articulation partageant plusieurs influences d'os.
   - **Visualisation du masque en un clic** : Bascule directe en mode Weight Paint dans le viewport.

3. **Simplification Sécurisée (Barrière Topologique C++) :**
   - **Verrouillage natif `delimit={'UV', 'MATERIAL', 'SEAM'}`** sur le modificateur Decimate : Interdit en C++ toute fusion de sommets entre îlots UV disjoints.
   - **Isolation physique optionnelle (Split -> Decimate -> Weld)** : Découpe temporaire par *Edge Split* sur les coutures pour rendre les traversées d'UV impossibles, suivie d'une re-soudure finale.
   - **Suppression d'îlots géométriques (LOD2 / LOD3+) :** Élimine automatiquement les micro-composants non visibles à distance ($< 2.5\%$).

4. **Audit Post-Décimation et Réparation Barycentrique via BVH Tree :**
   - Calcul de l'aire signée 2D de chaque triangle dans l'espace UV :
     $$\text{Aire\_UV} = \frac{1}{2} \left( (u_2 - u_1)(v_3 - v_1) - (v_2 - v_1)(u_3 - u_1) \right)$$
   - Détection des triangles écrasés ($|\text{Aire\_UV}| < 10^{-6}$) ou retournés (*UV foldovers*).
   - Re-projection automatique des coordonnées $(u, v)$ par interpolation barycentrique exacte depuis le LOD0 haute définition via `mathutils.bvhtree.BVHTree`.

5. **Restauration de l'Ombrage et Conditionnement Moteur :**
   - Projection des *Custom Split Normals* du LOD0 via *Data Transfer* pour éliminer les sauts d'ombrage en jeu.
   - Hiérarchie Empty avec propriété native `fbx_type = 'LodGroup'` reconnue automatiquement par Unreal Engine et Unity.
   - Génération de colliders physiques convexes `UCX_[Nom]_01` ou boîtes orientées `UBX_[Nom]_01`.
   - **Vue éclatée (Exploded View) :** Décalage le long de l'axe X pour comparaison visuelle simultanée des LODs.
   - **Simulation de distance caméra :** Basculement dynamique du LOD actif en temps réel dans la vue 3D selon la distance de vue.
   - **Export FBX / glTF direct.**

---

## Structure du Code

```text
lod_generator_addon/
│
├── __init__.py               # Enregistrement bl_info et point d'entrée
├── properties.py             # Modèle de données Scene.lod_generator_settings & stats
├── SPECIFICATION_TECHNIQUE.md# Spécification technique complète
│
├── core/                     # Logique algorithmique
│   ├── analyzer.py           # Audit du maillage, parcours BFS d'îlots, métriques
│   ├── weight_mapper.py      # Poids de protection 3D et détection de courbure UV
│   ├── decimator.py          # Pile : Triangulate -> Delimit -> Decimate -> BVH UV Repair
│   └── collider_gen.py       # Génération d'enveloppes convexes (UCX_) et boîtes (UBX_)
│
├── utils/
│   ├── math_helpers.py       # Calculs de volume, triangulation, aires signées 2D UV
│   └── hierarchy.py          # Gestion des collections, conteneur LodGroup et vue éclatée
│
├── operators/                # Opérateurs Blender
│   ├── op_generate.py        # Pipeline principal, masque de protection, nettoyage
│   ├── op_preview.py         # Vue éclatée et simulation interactive de distance caméra
│   └── op_export.py          # Exportation FBX et glTF
│
└── ui/
    └── panels.py             # Panneau latéral N-Panel dans la vue 3D
```

---

## Installation

### Installation via archive ZIP
1. Dans Blender, ouvrez le menu **Edit** > **Preferences** > **Add-ons** (ou **Get Extensions**).
2. Cliquez sur l'icône menu en haut à droite > **Install from Disk...**
3. Sélectionnez le fichier `lod_generator_addon.zip`.
4. Activez **Procedural LOD Generator Pro**.

### Installation manuelle
Copiez le dossier `lod_generator_addon` dans votre répertoire d'add-ons :
- **Windows :** `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\`
- **Linux :** `~/.config/blender/<version>/scripts/addons/`
- **macOS :** `~/Library/Application Support/Blender/<version>/scripts/addons/`

---

## Guide d'Utilisation

1. Sélectionnez le maillage cible dans la vue 3D.
2. Ouvrez le panneau latéral avec la touche **`N`** et accédez à l'onglet **LOD Generator**.
3. Configurez les options :
   - Nombre de niveaux désirés.
   - Profil de décimation (exponentiel, linéaire, cinématique, manuel).
   - Options de protection des UVs et des normales.
4. *(Optionnel)* Activez **Aperçu en direct** (section 7) pour voir le LOD choisi en wireframe par-dessus l'objet, recalculé à chaque changement de réglage, avant de générer.
5. Cliquez sur **Générer les LODs**. Une barre de progression indique l'étape en cours ; **Échap** ou **Annuler** interrompt la génération (les LODs déjà terminés sont conservés si l'option est cochée).
6. Contrôlez les niveaux générés avec la **Vue éclatée** ou la **Simulation de distance caméra**.
7. Exportez vers le format de votre moteur cible (`FBX` ou `glTF`).

> **Mise à jour de l'addon :** si une ancienne version est encore chargée en mémoire après l'installation d'un nouveau zip, un bandeau rouge l'indique en haut du panneau, avec un bouton **Recharger l'addon**. La version active est affichée dans l'en-tête du panneau.

### Qualité de décimation

- **Mode de réduction** (section 2) : *Pourcentage* (preset classique), *Budget d'erreur* (« réduis tant que l'écart reste sous 2 mm », tolérance multipliée à chaque niveau) ou *Taille écran* (écart toléré déduit du seuil d'écran de chaque LOD et d'une erreur en pixels). L'écart réellement mesuré (Hausdorff avec LOD0) est affiché dans le tableau de statistiques.
- **Remesh avant décimation** (section 5) : *Voxel* (fusionne les îlots, referme les trous) ou *QuadriFlow* (quads propres, repli sur Voxel si besoin) pour les scans, sculpts et kitbash. UVs et matériaux sont restaurés depuis la source.
- **Bake normales & AO** (section 6) : les détails de LOD0 sont bakés (Cycles) sur les LODs lointains, dans des copies de leurs matériaux. Si les UVs se chevauchent ou sont tuilées, un calque `LOD_BakeUV` est créé automatiquement.
- **Imposteur** (section 6) : dernier niveau en billboard « étoile » (8 ou 16 vues capturées dans un atlas), idéal pour la végétation et les décors lointains.

---

## Intégration Moteurs

### Unreal Engine 5
- Importez le fichier `.fbx` généré.
- La métadonnée `LodGroup` est détectée automatiquement pour configurer les paliers.
- Le maillage `UCX_[Nom]_01` est instantanément associé en tant que collider physique.

### Unity (URP / HDRP)
- Importez le fichier `.fbx`.
- Unity applique automatiquement le composant `LOD Group` avec les sous-maillages correspondants.

### Godot 4.x
- Importez le fichier `.glb`.
- Les conventions de nommage `_LOD0`, `_LOD1` et les maillages de collision sont reconnus nativement.

---

## Tests Automatisés

```powershell
& "C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe" --background --python "tests/test_lod_generator.py"
```

```text
======================================================================
  DEBUT DES TESTS UNITAIRES : PROCEDURAL LOD GENERATOR
======================================================================
--- Test 1: Enregistrement de l'Add-on ---                                [OK]
--- Test 2: Audit Topologique et Detection d'Ilots (BFS) ---             [OK]
--- Test 3: Carte de Protection et Ponderation (Vertex Group) ---        [OK]
--- Test 4: Pipeline Complet de Generation de LODs ---                   [OK]
--- Test 5: Vue Eclatee (Exploded View) ---                              [OK]
--- Test 6: Exportation Moteur (FBX et glTF) ---                         [OK]
--- Test 7: Algorithmes Avances de Preservation des UVs ---              [OK]
--- Test 8: Nettoyage et Reinitialisation ---                            [OK]
======================================================================
  TOUS LES TESTS SONT PASSES AVEC SUCCES.
======================================================================
```
