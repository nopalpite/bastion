"""Validation et lecture des images de plan de salle (voir
templates/room_form.html, templates/map.html, static/js/map.js).

N'importe quelle résolution/ratio d'image est accepté. Ce qui compte est
que le conteneur CSS du plan (`.map-wrap`, voir style.css) affiche
toujours EXACTEMENT le même ratio largeur/hauteur que l'image réelle —
sinon `object-fit: contain` ajoute des bandes vides dont la taille varie
selon l'écran, et une position de machine stockée en % (voir
store.update_machine_position) dérive par rapport à l'image affichée.

`get_image_size()` lit ce ratio côté serveur (pour l'injecter directement
dans le CSS au premier rendu, sans attendre le chargement de l'image côté
navigateur — voir app.py:map_view et map.html) pour les formats raster
(PNG/JPG). Le SVG, vectoriel par nature, n'a pas de résolution fixe que
Pillow puisse lire : son ratio réel est déterminé côté navigateur à la
place (voir le filet de sécurité dans static/js/map.js).

Module séparé de app.py pour rester testable sans dépendre d'eventlet
(voir son docstring)."""
from PIL import Image, UnidentifiedImageError

# Ratio par défaut: salle sans plan importé (fond neutre), ou image dont
# la résolution n'a pas pu être lue (SVG, fichier introuvable...) tant que
# le filet de sécurité JS ne l'a pas corrigé.
DEFAULT_MAP_RATIO = (16, 9)


def validate_map_image(file_stream):
    """Vérifie qu'un flux est bien une image raster lisible (PNG/JPG) —
    pas de contrainte de résolution, seulement l'intégrité du fichier.
    Retourne un message d'erreur (str) si invalide, None sinon.
    Repositionne le curseur du flux à 0 dans tous les cas, pour que
    l'appelant puisse ensuite sauvegarder le fichier normalement."""
    try:
        # "with" plutôt que Image.open(...).close(): sur un flux déjà
        # ouvert par l'appelant (pas un chemin), Image.close() ferme le
        # flux sous-jacent sans condition, alors que le context manager ne
        # le fait que si Pillow l'a lui-même ouvert (_exclusive_fp) — ici,
        # on ne veut PAS que la validation ferme le flux de l'appelant.
        with Image.open(file_stream):
            pass
    except (UnidentifiedImageError, OSError):
        return "Fichier image illisible ou corrompu."
    finally:
        file_stream.seek(0)
    return None


def get_image_size(path):
    """Lit la résolution (largeur, hauteur) d'une image raster déjà
    enregistrée sur disque. Retourne None si le fichier n'est pas une
    image raster lisible (SVG, fichier manquant/corrompu...) — l'appelant
    doit alors se rabattre sur DEFAULT_MAP_RATIO."""
    try:
        with Image.open(path) as img:
            return img.size
    except (UnidentifiedImageError, OSError):
        return None
