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
import threading

import yaml

from config import MACROS_FILE
from ids import slugify, unique_id

_lock = threading.Lock()


def _load():
    try:
        with open(MACROS_FILE, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}
    data.setdefault("macros", [])
    for m in data["macros"]:
        # macros.yaml est modifiable à la main (voir docstring du module) :
        # une entrée créée avant l'ajout du champ os, ou éditée
        # manuellement sans lui, ne doit pas faire planter /macros/<id>/run
        # (qui fait macro["os"]) — même esprit que la migration de
        # machines.yaml dans store.py (_migrate_legacy_vnc_bridge_fields).
        m.setdefault("os", "linux")
    return data


def _save(data):
    with open(MACROS_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def load_macros():
    return _load()["macros"]


def get_macro(macro_id):
    for m in load_macros():
        if m["id"] == macro_id:
            return m
    return None


def add_macro(name, command, os_type):
    with _lock:
        data = _load()
        existing = {m["id"] for m in data["macros"]}
        macro_id = unique_id(slugify(name, fallback="macro"), existing)
        data["macros"].append({
            "id": macro_id, "name": name, "command": command, "os": os_type,
        })
        _save(data)
        return macro_id


def update_macro(macro_id, name, command, os_type):
    with _lock:
        data = _load()
        for m in data["macros"]:
            if m["id"] == macro_id:
                m["name"] = name
                m["command"] = command
                m["os"] = os_type
        _save(data)


def delete_macro(macro_id):
    with _lock:
        data = _load()
        data["macros"] = [m for m in data["macros"] if m["id"] != macro_id]
        _save(data)
