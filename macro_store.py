"""Bibliothèque de macros (commandes SSH réutilisables) — voir
macro_runner.py pour leur exécution sur un pool de machines choisi
dans l'interface.

Même conception volontairement simple que store.py: un fichier YAML
plat (macros.yaml), relu/réécrit en entier à chaque modification —
inutile de complexifier pour quelques dizaines de macros tout au plus.
Contrairement à machines.yaml, ce fichier n'est pas fourni dans le
dépôt (une bibliothèque de macros vide au premier démarrage est un
état parfaitement valide, alors qu'un inventaire de machines vide
serait suspect) : _load() tolère donc son absence.
"""
import re
import threading
import unicodedata

import yaml

from config import MACROS_FILE

_lock = threading.Lock()


def _load():
    try:
        with open(MACROS_FILE, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}
    data.setdefault("macros", [])
    return data


def _save(data):
    with open(MACROS_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def _slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "macro"


def _unique_id(base, existing_ids):
    candidate = base
    i = 2
    while candidate in existing_ids:
        candidate = f"{base}-{i}"
        i += 1
    return candidate


def load_macros():
    return _load()["macros"]


def get_macro(macro_id):
    for m in load_macros():
        if m["id"] == macro_id:
            return m
    return None


def add_macro(name, command):
    with _lock:
        data = _load()
        existing = {m["id"] for m in data["macros"]}
        macro_id = _unique_id(_slugify(name), existing)
        data["macros"].append({"id": macro_id, "name": name, "command": command})
        _save(data)
        return macro_id


def update_macro(macro_id, name, command):
    with _lock:
        data = _load()
        for m in data["macros"]:
            if m["id"] == macro_id:
                m["name"] = name
                m["command"] = command
        _save(data)


def delete_macro(macro_id):
    with _lock:
        data = _load()
        data["macros"] = [m for m in data["macros"] if m["id"] != macro_id]
        _save(data)
