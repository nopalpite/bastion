"""Génération d'identifiants lisibles (slug + désambiguïsation), partagée
entre store.py (machines/salles) et macro_store.py (macros) — un seul
endroit à corriger si cette logique évolue, plutôt que deux copies
susceptibles de diverger silencieusement l'une de l'autre."""
import re
import unicodedata


def slugify(text, fallback="item"):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or fallback


def unique_id(base, existing_ids):
    candidate = base
    i = 2
    while candidate in existing_ids:
        candidate = f"{base}-{i}"
        i += 1
    return candidate
