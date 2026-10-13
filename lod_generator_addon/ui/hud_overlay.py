"""
HUD overlay module (désactivé pour préserver un Viewport 3D 100% propre).
Les statistiques sont consultables directement dans le panneau N-Panel (section 8).
"""


def register_overlay():
    """No-op: aucun dessin superposé dans la 3D View."""
    pass


def unregister_overlay():
    """No-op."""
    pass
