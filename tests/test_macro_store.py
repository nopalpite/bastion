"""Tests pour macro_store.py: bibliothèque de macros (voir /macros)."""
import yaml

import macro_store


def test_load_macros_empty_when_file_missing(macros_file):
    # Contrairement à machines.yaml, macros.yaml n'existe pas forcément
    # (voir le docstring du module) -- ne doit pas lever.
    assert macro_store.load_macros() == []


def test_add_macro_creates_slugified_id(macros_file):
    macro_id = macro_store.add_macro(
        "Mettre à jour apt", "sudo apt update && sudo apt upgrade -y", "linux",
    )

    macro = macro_store.get_macro(macro_id)
    assert macro_id == "mettre-a-jour-apt"
    assert macro["name"] == "Mettre à jour apt"
    assert macro["command"] == "sudo apt update && sudo apt upgrade -y"
    assert macro["os"] == "linux"


def test_add_macro_disambiguates_duplicate_names(macros_file):
    first_id = macro_store.add_macro("Reboot", "reboot", "linux")
    second_id = macro_store.add_macro("Reboot", "reboot now", "linux")

    assert first_id == "reboot"
    assert second_id == "reboot-2"


def test_get_macro_returns_none_when_missing(macros_file):
    assert macro_store.get_macro("does-not-exist") is None


def test_update_macro_changes_name_command_and_os(macros_file):
    macro_id = macro_store.add_macro("Reboot", "reboot", "linux")

    macro_store.update_macro(macro_id, "Redémarrer", "sudo reboot", "windows")

    macro = macro_store.get_macro(macro_id)
    assert macro["name"] == "Redémarrer"
    assert macro["command"] == "sudo reboot"
    assert macro["os"] == "windows"


def test_delete_macro_removes_it(macros_file):
    macro_id = macro_store.add_macro("Reboot", "reboot", "linux")

    macro_store.delete_macro(macro_id)

    assert macro_store.get_macro(macro_id) is None


def test_macros_persist_across_loads(macros_file):
    macro_store.add_macro("Reboot", "reboot", "linux")

    assert len(macro_store.load_macros()) == 1
    assert len(macro_store.load_macros()) == 1  # relit le fichier, pas un cache


def test_load_macros_defaults_missing_os_to_linux(macros_file):
    # macros.yaml est modifiable à la main (voir docstring du module) --
    # une entrée écrite sans "os" (fichier édité avant l'ajout du champ,
    # ou à la main) ne doit pas faire planter /macros/<id>/run derrière.
    macros_file.write_text(
        yaml.safe_dump({"macros": [{"id": "legacy", "name": "Legacy", "command": "uptime"}]}),
        encoding="utf-8",
    )

    macro = macro_store.get_macro("legacy")

    assert macro["os"] == "linux"
