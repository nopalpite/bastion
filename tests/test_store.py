"""Tests pour store.py: CRUD machines/salles sur un machines.yaml temporaire
(fixture machines_file, voir conftest.py) — aucun accès au fichier réel du
projet."""
import store


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
