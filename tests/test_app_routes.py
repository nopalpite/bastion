"""Tests des routes Flask (login, dashboard, CRUD hôtes/salles) avec le
client de test Flask — pas de vraie connexion SSH/VNC.

Importer `app` déclenche eventlet.monkey_patch() (fait en tête de app.py,
avant tout le reste, voir son commentaire) pour tout le process pytest.
Sans effet sur ces tests, mais à garder en tête si des tests ajoutés plus
tard se comportent bizarrement avec le threading/les sockets standard."""
import time

import pytest

import app as app_module
import history
import store


@pytest.fixture
def client(machines_file, history_db, monkeypatch):
    monkeypatch.setattr(app_module.config, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module.config, "ADMIN_PASSWORD", "admin")
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    with app_module.app.test_client() as test_client:
        yield test_client


def test_dashboard_redirects_to_login_when_logged_out(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_rejects_wrong_credentials(client):
    resp = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert resp.status_code == 200
    assert "Identifiants incorrects." in resp.get_data(as_text=True)


def test_login_accepts_correct_credentials_and_reaches_dashboard(client):
    resp = client.post(
        "/login", data={"username": "admin", "password": "admin"}, follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"bastion" in resp.data.lower()


def test_new_host_requires_login(client):
    resp = client.get("/hosts/new")
    assert resp.status_code == 302


def test_new_host_creates_machine(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })

    assert resp.status_code == 302
    machine = store.get_machine("serveur-test")
    assert machine is not None
    assert machine["host"] == "10.0.0.1"


def test_new_host_rejects_missing_required_fields(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post("/hosts/new", data={"name": "", "os": "linux", "host": "10.0.0.1"})

    assert resp.status_code == 200  # ré-affiche le formulaire avec une erreur
    assert "obligatoires" in resp.get_data(as_text=True)
    assert store.load_machines() == []


def test_edit_host_delete_flag_removes_machine(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })

    resp = client.post("/hosts/serveur-test/edit", data={"delete": "1"})

    assert resp.status_code == 302
    assert store.get_machine("serveur-test") is None


def test_terminal_page_404s_for_unknown_machine(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    resp = client.get("/terminal/does-not-exist")
    assert resp.status_code == 404


# --- /stats: page de statistiques de disponibilité (voir history.py) ---

def test_stats_page_requires_login(client):
    resp = client.get("/stats")
    assert resp.status_code == 302


def test_stats_page_loads(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })

    resp = client.get("/stats")

    assert resp.status_code == 200
    assert "Serveur Test" in resp.get_data(as_text=True)


def test_stats_settings_updates_retention(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post("/stats/settings", data={"retention_days": "10"})

    assert resp.status_code == 302
    assert history.get_retention_days() == 10


def test_stats_settings_rejects_invalid_value(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post("/stats/settings", data={"retention_days": "pas-un-nombre"})

    assert resp.status_code == 302
    assert "error=" in resp.headers["Location"]


def test_stats_purge_deletes_old_entries(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    history.record_check("m1", "down", None, checked_at=time.time() - 40 * 86400)

    resp = client.post("/stats/purge")

    assert resp.status_code == 302
    assert "purged=1" in resp.headers["Location"]


def test_api_history_404s_for_unknown_machine(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    resp = client.get("/api/history/does-not-exist")
    assert resp.status_code == 404


def test_api_history_returns_timeline_json(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })
    history.record_check("serveur-test", "up", 5.0)

    resp = client.get("/api/history/serveur-test?hours=1")

    assert resp.status_code == 200
    assert resp.json["timeline"][-1] == 100.0
