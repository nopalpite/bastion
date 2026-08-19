"""Tests des routes Flask (login, dashboard, CRUD hôtes/salles) avec le
client de test Flask — pas de vraie connexion SSH/VNC.

Importer `app` déclenche eventlet.monkey_patch() (fait en tête de app.py,
avant tout le reste, voir son commentaire) pour tout le process pytest.
Sans effet sur ces tests, mais à garder en tête si des tests ajoutés plus
tard se comportent bizarrement avec le threading/les sockets standard."""
import pytest

import app as app_module
import store


@pytest.fixture
def client(machines_file, monkeypatch):
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
