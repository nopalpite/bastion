"""Tests pour store.py: CRUD machines/salles sur un machines.yaml temporaire
(fixture machines_file, voir conftest.py) — aucun accès au fichier réel du
projet."""
import os

import gen_vnc_tokens
import store


def _tokens_file_content():
    if not os.path.exists(gen_vnc_tokens.TOKENS_FILE):
        return ""
    with open(gen_vnc_tokens.TOKENS_FILE, encoding="utf-8") as f:
        return f.read()


def test_add_and_get_machine(machines_file):
    machine_id = store.add_machine(name="Serveur Test", os_type="linux", host="10.0.0.5")
    machine = store.get_machine(machine_id)
    assert machine is not None
    assert machine["name"] == "Serveur Test"
    assert machine["host"] == "10.0.0.5"
    assert machine["ssh_port"] == 22


def test_add_machine_slugifies_id(machines_file):
    machine_id = store.add_machine(name="Serveur Web Linux", os_type="linux", host="10.0.0.1")
    assert machine_id == "serveur-web-linux"


def test_add_machine_deduplicates_id(machines_file):
    id1 = store.add_machine(name="Serveur", os_type="linux", host="10.0.0.1")
    id2 = store.add_machine(name="Serveur", os_type="linux", host="10.0.0.2")
    assert id1 == "serveur"
    assert id2 == "serveur-2"


def test_add_machine_windows_default_rdp_port(machines_file):
    machine_id = store.add_machine(name="Win", os_type="windows", host="10.0.0.9")
    machine = store.get_machine(machine_id)
    assert machine["rdp_port"] == 3389


def test_add_windows_machine_stores_encrypted_rdp_credentials(machines_file, credentials_key):
    machine_id = store.add_machine(
        name="Win", os_type="windows", host="10.0.0.9",
        rdp_username="administrateur", rdp_password="hunter2", rdp_domain="CORP",
    )
    machine = store.get_machine(machine_id)
    assert machine["rdp_username"] == "administrateur"
    assert machine["rdp_domain"] == "CORP"
    assert machine["rdp_password"] != "hunter2"  # jamais en clair


def test_linux_machine_has_no_rdp_port(machines_file):
    machine_id = store.add_machine(name="Lin", os_type="linux", host="10.0.0.9")
    machine = store.get_machine(machine_id)
    assert "rdp_port" not in machine


def test_update_machine_switching_from_windows_clears_rdp_fields(machines_file, credentials_key):
    machine_id = store.add_machine(
        name="Win", os_type="windows", host="10.0.0.9",
        rdp_username="admin", rdp_password="hunter2",
    )
    store.update_machine(machine_id, name="Win", os_type="linux", host="10.0.0.9")
    machine = store.get_machine(machine_id)
    assert "rdp_port" not in machine
    assert "rdp_username" not in machine
    assert "rdp_password" not in machine


def test_add_machine_in_room_gets_a_position(machines_file):
    room_id = store.add_room("Salle A")
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1", room_id=room_id,
    )
    machine = store.get_machine(machine_id)
    assert machine["room"] == room_id
    assert "x" in machine["position"] and "y" in machine["position"]


def test_add_machine_stores_encrypted_credentials(machines_file, credentials_key):
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1",
        username="root", password="hunter2",
    )
    machine = store.get_machine(machine_id)
    assert machine["credentials"]["username"] == "root"
    assert machine["credentials"]["password"] != "hunter2"  # jamais en clair
    assert store.has_stored_credentials(machine)


def test_add_machine_without_credentials_key_stores_nothing(machines_file):
    # pas de fixture credentials_key ici: BASTION_CREDENTIALS_KEY absente
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1",
        username="root", password="hunter2",
    )
    machine = store.get_machine(machine_id)
    assert "credentials" not in machine


def test_update_machine_room_change_resets_position(machines_file):
    room_a = store.add_room("Salle A")
    room_b = store.add_room("Salle B")
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1", room_id=room_a,
    )

    store.update_machine(
        machine_id, name="Serveur", os_type="linux", host="10.0.0.1", room_id=room_b,
    )

    machine = store.get_machine(machine_id)
    assert machine["room"] == room_b
    assert machine["position"] is not None  # nouvelle position en grille attribuée


def test_update_machine_clear_credentials(machines_file, credentials_key):
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1", username="root", password="hunter2",
    )
    store.update_machine(
        machine_id, name="Serveur", os_type="linux", host="10.0.0.1", clear_credentials=True,
    )
    machine = store.get_machine(machine_id)
    assert "credentials" not in machine


def test_delete_machine(machines_file):
    machine_id = store.add_machine(name="Serveur", os_type="linux", host="10.0.0.1")
    store.delete_machine(machine_id)
    assert store.get_machine(machine_id) is None


def test_delete_room_detaches_machines_instead_of_deleting_them(machines_file):
    room_id = store.add_room("Salle A")
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1", room_id=room_id,
    )

    store.delete_room(room_id)

    assert store.get_room(room_id) is None
    machine = store.get_machine(machine_id)
    assert machine is not None
    assert "room" not in machine
    assert "position" not in machine


def test_set_machine_host_key(machines_file):
    machine_id = store.add_machine(name="Serveur", os_type="linux", host="10.0.0.1")
    store.set_machine_host_key(machine_id, "ssh-ed25519", "ZmFrZQ==")
    machine = store.get_machine(machine_id)
    assert machine["host_key"] == {"type": "ssh-ed25519", "key": "ZmFrZQ=="}


def test_add_machine_with_vnc_port_assigns_bridge_port(machines_file):
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1", vnc_port=5900,
    )
    machine = store.get_machine(machine_id)
    assert machine["vnc_bridge_port"] >= store.VNC_BRIDGE_BASE_PORT


def test_add_machine_with_vnc_port_assigns_distinct_bridge_ports(machines_file):
    id1 = store.add_machine(name="Srv1", os_type="linux", host="10.0.0.1", vnc_port=5900)
    id2 = store.add_machine(name="Srv2", os_type="linux", host="10.0.0.2", vnc_port=5900)
    port1 = store.get_machine(id1)["vnc_bridge_port"]
    port2 = store.get_machine(id2)["vnc_bridge_port"]
    assert port1 != port2


def test_update_machine_removing_vnc_port_clears_bridge_fields(machines_file):
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1", vnc_port=5900,
    )
    store.set_machine_vnc_cert_fingerprint(machine_id, "abc123")

    store.update_machine(
        machine_id, name="Serveur", os_type="linux", host="10.0.0.1", vnc_port=None,
    )

    machine = store.get_machine(machine_id)
    assert "vnc_bridge_port" not in machine
    assert "vnc_tls_cert_fingerprint" not in machine


def test_update_machine_keeps_same_bridge_port(machines_file):
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1", vnc_port=5900,
    )
    original_port = store.get_machine(machine_id)["vnc_bridge_port"]

    store.update_machine(
        machine_id, name="Serveur", os_type="linux", host="10.0.0.1", vnc_port=5901,
    )

    assert store.get_machine(machine_id)["vnc_bridge_port"] == original_port


def test_legacy_vnc_tls_fields_migrate_transparently_on_read(machines_file):
    machines_file.write_text(
        "rooms: []\n"
        "machines:\n"
        "  - id: legacy\n"
        "    name: legacy\n"
        "    os: linux\n"
        "    host: 10.0.0.9\n"
        "    vnc_port: 5900\n"
        "    vnc_tls: true\n"
        "    vnc_tls_local_port: 6100\n",
        encoding="utf-8",
    )
    machine = store.get_machine("legacy")
    assert machine["vnc_bridge_port"] == 6100
    assert "vnc_tls_local_port" not in machine
    assert "vnc_tls" not in machine


def test_set_machine_vnc_cert_fingerprint(machines_file):
    machine_id = store.add_machine(name="Serveur", os_type="linux", host="10.0.0.1", vnc_port=5900)
    store.set_machine_vnc_cert_fingerprint(machine_id, "deadbeef")
    assert store.get_machine(machine_id)["vnc_tls_cert_fingerprint"] == "deadbeef"


# --- Régénération automatique de vnc_tokens.conf (voir gen_vnc_tokens.py:
# websockify relit ce fichier à chaque connexion sans le mettre en cache,
# donc le régénérer à chaque modification de l'inventaire suffit — pas
# besoin de redémarrer le conteneur pour qu'une machine VNC ajoutée via
# l'interface devienne joignable) ------------------------------------

def test_add_machine_with_vnc_regenerates_tokens_file(machines_file):
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1", vnc_port=5900,
    )
    bridge_port = store.get_machine(machine_id)["vnc_bridge_port"]
    assert f"{machine_id}: 127.0.0.1:{bridge_port}" in _tokens_file_content()


def test_add_machine_without_vnc_still_writes_tokens_file(machines_file):
    # Le fichier doit être (re)créé même vide, pas planter s'il n'existe
    # pas encore (premier ajout d'une machine sans VNC après un
    # "docker compose up" par exemple).
    store.add_machine(name="Serveur", os_type="linux", host="10.0.0.1")
    assert os.path.exists(gen_vnc_tokens.TOKENS_FILE)
    assert _tokens_file_content() == ""


def test_delete_machine_regenerates_tokens_file(machines_file):
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1", vnc_port=5900,
    )
    assert machine_id in _tokens_file_content()

    store.delete_machine(machine_id)
    assert machine_id not in _tokens_file_content()


def test_update_machine_removing_vnc_regenerates_tokens_file(machines_file):
    machine_id = store.add_machine(
        name="Serveur", os_type="linux", host="10.0.0.1", vnc_port=5900,
    )
    assert machine_id in _tokens_file_content()

    store.update_machine(
        machine_id, name="Serveur", os_type="linux", host="10.0.0.1", vnc_port=None,
    )
    assert machine_id not in _tokens_file_content()
