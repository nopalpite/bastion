"""Tests des routes Flask (login, dashboard, CRUD hôtes/salles) avec le
client de test Flask — pas de vraie connexion SSH/VNC.

Importer `app` déclenche eventlet.monkey_patch() (fait en tête de app.py,
avant tout le reste, voir son commentaire) pour tout le process pytest.
Sans effet sur ces tests, mais à garder en tête si des tests ajoutés plus
tard se comportent bizarrement avec le threading/les sockets standard."""
import io
import time

import pytest

import app as app_module
import discovery
import history
import macro_runner
import macro_store
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


def test_new_host_get_prefills_from_query_params(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.get("/hosts/new?host=10.0.0.9&name=srv-discovered&ssh_port=22&vnc_port=5901")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'value="10.0.0.9"' in body
    assert 'value="srv-discovered"' in body
    assert 'value="5901"' in body


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


# --- Onglets multi-sessions (voir static/js/tabs.js) : la page nue
# chargée dans une iframe d'onglet masque son lien "Retour" via
# ?embedded=1, qui y naviguerait l'iframe au lieu de fermer l'onglet. ---

def test_terminal_page_shows_retour_link_by_default(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })

    resp = client.get("/terminal/serveur-test")

    assert "Retour" in resp.get_data(as_text=True)


def test_terminal_page_hides_retour_link_when_embedded(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })

    resp = client.get("/terminal/serveur-test?embedded=1")

    assert "Retour" not in resp.get_data(as_text=True)


def test_vnc_page_shows_retour_link_by_default(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })

    resp = client.get("/vnc/serveur-test")

    assert "Retour" in resp.get_data(as_text=True)


def test_vnc_page_hides_retour_link_when_embedded(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })

    resp = client.get("/vnc/serveur-test?embedded=1")

    assert "Retour" not in resp.get_data(as_text=True)


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
    assert resp.json["latency"][-1] == 5.0


# --- GET /api/machines: inventaire en lecture seule pour un outil externe
# (Ansible...), authentifié par jeton (api_token_required) plutôt que par
# la session cookie de login_required -- voir app.py/README. ------------

def test_api_machines_404s_when_token_not_configured(client):
    # app_module.config.API_TOKEN vaut "" par défaut dans l'environnement
    # de test (BASTION_API_TOKEN non définie) -- la route n'existe pas
    # tant qu'elle n'est pas configurée, même sans authentification.
    resp = client.get("/api/machines")
    assert resp.status_code == 404


def test_api_machines_rejects_missing_authorization_header(client, monkeypatch):
    monkeypatch.setattr(app_module.config, "API_TOKEN", "sekret")
    resp = client.get("/api/machines")
    assert resp.status_code == 401


def test_api_machines_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setattr(app_module.config, "API_TOKEN", "sekret")
    resp = client.get("/api/machines", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_api_machines_returns_inventory_with_correct_token(client, monkeypatch):
    monkeypatch.setattr(app_module.config, "API_TOKEN", "sekret")
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/rooms/new", data={"name": "Salle Expo"})
    client.post("/tags", data={"name": "Ecran"})
    client.post("/tags", data={"name": "gpio"})
    client.post("/hosts/new", data={
        "name": "Borne 1", "os": "linux", "host": "10.0.0.5", "ssh_port": "22",
        "room": "salle-expo", "tags": ["ecran", "gpio"],
    })

    resp = client.get("/api/machines", headers={"Authorization": "Bearer sekret"})

    assert resp.status_code == 200
    machines = resp.json
    assert len(machines) == 1
    machine = machines[0]
    assert machine == {
        "id": "borne-1", "name": "Borne 1", "host": "10.0.0.5", "os": "linux",
        "ssh_port": 22, "site": "Salle Expo", "tags": ["ecran", "gpio"],
    }


def test_api_machines_never_leaks_credentials(client, monkeypatch, credentials_key):
    monkeypatch.setattr(app_module.config, "API_TOKEN", "sekret")
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Avec identifiants", "os": "linux", "host": "10.0.0.6", "ssh_port": "22",
        "username": "root", "password": "hunter2",
        "vnc_port": "5900", "vnc_password": "vncpass",
    })

    resp = client.get("/api/machines", headers={"Authorization": "Bearer sekret"})

    assert resp.status_code == 200
    machine = resp.json[0]
    sensitive_keys = {
        "credentials", "host_key", "vnc_password", "vnc_username",
        "vnc_bridge_port", "vnc_tls_cert_fingerprint",
    }
    assert not sensitive_keys & machine.keys()


# --- /tags: catalogue géré (voir store.add_tag_to_catalog/delete_tag_
# from_catalog) -- une machine ne peut porter qu'un tag présent ici. ----

def test_tags_page_requires_login(client):
    resp = client.get("/tags")
    assert resp.status_code == 302


def test_tags_post_adds_a_tag(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/tags", data={"name": "Écran HD"})

    resp = client.get("/tags")

    assert resp.status_code == 200
    assert b"ecran-hd" in resp.data  # slugifié


def test_delete_tag_removes_it_from_catalog_and_machines(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/tags", data={"name": "ecran"})
    client.post("/hosts/new", data={
        "name": "Borne 1", "os": "linux", "host": "10.0.0.5", "ssh_port": "22",
        "tags": ["ecran"],
    })

    resp = client.post("/tags/ecran/delete", follow_redirects=True)

    assert resp.status_code == 200
    # Pas de scraping du HTML rendu ici: tags.html a un placeholder
    # "ecran" sur le champ d'ajout, toujours présent quel que soit l'état
    # du catalogue -- vérifier l'état réel via store plutôt qu'un
    # substring fragile.
    assert store.load_tag_catalog() == []
    assert "tags" not in store.get_machine("borne-1")


# --- /discover: découverte réseau (voir discovery.py) -------------------

def test_discover_page_requires_login(client):
    resp = client.get("/discover")
    assert resp.status_code == 302


def test_discover_get_shows_form_without_scanning(client, monkeypatch):
    client.post("/login", data={"username": "admin", "password": "admin"})
    called = []
    monkeypatch.setattr(discovery, "run_discovery", lambda cidr: called.append(cidr) or [])

    resp = client.get("/discover")

    assert resp.status_code == 200
    assert called == []  # une visite GET ne doit jamais déclencher de scan


def test_discover_post_shows_results(client, monkeypatch):
    client.post("/login", data={"username": "admin", "password": "admin"})
    monkeypatch.setattr(
        discovery, "run_discovery",
        lambda cidr: [{"ip": "10.0.0.5", "hostname": "srv.local", "ssh": True, "vnc_port": None}],
    )

    resp = client.post("/discover", data={"cidr": "10.0.0.0/29"})

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "10.0.0.5" in body
    assert "srv.local" in body


def test_discover_post_shows_error_on_invalid_range(client, monkeypatch):
    client.post("/login", data={"username": "admin", "password": "admin"})

    def boom(cidr):
        raise discovery.DiscoveryError("Plage trop grande")

    monkeypatch.setattr(discovery, "run_discovery", boom)

    resp = client.post("/discover", data={"cidr": "10.0.0.0/8"})

    assert resp.status_code == 200
    assert "Plage trop grande" in resp.get_data(as_text=True)


def test_bulk_add_hosts_requires_login(client):
    resp = client.post("/hosts/bulk-add", data={"count": "1", "select_0": "on"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_bulk_add_hosts_creates_only_checked_rows(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post("/hosts/bulk-add", data={
        "count": "2",
        "select_0": "on",
        "host_0": "10.0.0.5", "name_0": "srv-discovered", "vnc_0": "",
        # ligne 1 non cochée: pas de select_1, ne doit pas être ajoutée
        "host_1": "10.0.0.6", "name_1": "srv-other", "vnc_1": "",
        "os_type": "linux",
    })

    assert resp.status_code == 302
    assert "bulk_added=1" in resp.headers["Location"]
    assert store.get_machine("srv-discovered") is not None
    assert store.get_machine("srv-other") is None


def test_bulk_add_hosts_falls_back_to_ip_when_no_hostname(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    client.post("/hosts/bulk-add", data={
        "count": "1", "select_0": "on",
        "host_0": "10.0.0.5", "name_0": "", "vnc_0": "",
        "os_type": "linux",
    })

    machine = store.get_machine("10-0-0-5")
    assert machine is not None
    assert machine["host"] == "10.0.0.5"


def test_bulk_add_hosts_applies_selected_os_and_vnc_port(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    client.post("/hosts/bulk-add", data={
        "count": "1", "select_0": "on",
        "host_0": "10.0.0.5", "name_0": "srv-win", "vnc_0": "5901",
        "os_type": "windows",
    })

    machine = store.get_machine("srv-win")
    assert machine["os"] == "windows"
    assert machine["vnc_port"] == 5901


def test_bulk_add_hosts_redirects_without_bulk_added_when_nothing_selected(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post(
        "/hosts/bulk-add", data={"count": "1", "host_0": "10.0.0.5", "os_type": "linux"},
    )

    assert resp.status_code == 302
    assert "bulk_added" not in resp.headers["Location"]


def test_bulk_add_hosts_rejects_invalid_os_type(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post("/hosts/bulk-add", data={
        "count": "1", "select_0": "on", "host_0": "10.0.0.5", "os_type": "macos",
    })

    assert resp.status_code == 400
    assert store.get_machine("10-0-0-5") is None


def test_bulk_add_hosts_rejects_missing_os_type(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post("/hosts/bulk-add", data={
        "count": "1", "select_0": "on", "host_0": "10.0.0.5",
    })

    assert resp.status_code == 400


def test_discover_marks_already_inventoried_hosts(client, monkeypatch):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.5", "ssh_port": "22",
    })
    monkeypatch.setattr(
        discovery, "run_discovery",
        lambda cidr: [{"ip": "10.0.0.5", "hostname": None, "ssh": True, "vnc_port": None}],
    )

    resp = client.post("/discover", data={"cidr": "10.0.0.0/29"})

    assert "Déjà dans l'inventaire" in resp.get_data(as_text=True)


# --- /sessions: journal des connexions (voir history.py) ---------------

def test_sessions_page_requires_login(client):
    resp = client.get("/sessions")
    assert resp.status_code == 302


def test_sessions_page_shows_recent_sessions(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })
    session_id = history.start_session("serveur-test", "ssh", source_ip="10.0.0.9")
    history.end_session(session_id)

    resp = client.get("/sessions")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Serveur Test" in body
    assert "SSH" in body
    assert "10.0.0.9" in body


def test_sessions_page_shows_open_session_as_in_progress(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    history.start_session("unknown-machine", "vnc")

    resp = client.get("/sessions")

    assert "en cours" in resp.get_data(as_text=True)


# --- Export / import de l'inventaire ------------------------------------

def test_export_hosts_requires_login(client):
    resp = client.get("/hosts/export")
    assert resp.status_code == 302


def test_export_hosts_returns_machines_yaml(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })

    resp = client.get("/hosts/export")

    assert resp.status_code == 200
    assert "serveur-test" in resp.get_data(as_text=True)
    assert "attachment" in resp.headers.get("Content-Disposition", "")


def test_import_hosts_requires_login(client):
    resp = client.get("/hosts/import")
    assert resp.status_code == 302


def test_import_hosts_replaces_inventory(client):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Ancien", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })
    new_yaml = (
        b"rooms: []\n"
        b"machines:\n"
        b"  - id: nouveau\n"
        b"    name: Nouveau\n"
        b"    os: linux\n"
        b"    host: 10.0.0.9\n"
        b"    ssh_port: 22\n"
    )

    resp = client.post(
        "/hosts/import",
        data={"import_file": (io.BytesIO(new_yaml), "machines.yaml")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert store.get_machine("nouveau") is not None
    assert store.get_machine("ancien") is None


def test_import_hosts_shows_error_on_invalid_file(client):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post(
        "/hosts/import",
        data={"import_file": (io.BytesIO(b"pas de la config valide"), "machines.yaml")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    assert "Structure invalide" in resp.get_data(as_text=True)


# --- Macros (exécution SSH sur un pool de machines, voir macro_runner.py)

def test_macros_list_requires_login(client, macros_file):
    resp = client.get("/macros")
    assert resp.status_code == 302


def test_macros_list_shows_existing_macros(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})
    macro_store.add_macro("Uptime", "uptime", "linux")

    resp = client.get("/macros")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Uptime" in body
    assert "uptime" in body
    assert "Linux" in body


def test_new_macro_get_shows_form(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})
    resp = client.get("/macros/new")
    assert resp.status_code == 200


def test_new_macro_creates_macro(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post(
        "/macros/new", data={"name": "Uptime", "command": "uptime", "os": "linux"},
    )

    assert resp.status_code == 302
    macro = macro_store.get_macro("uptime")
    assert macro is not None
    assert macro["command"] == "uptime"
    assert macro["os"] == "linux"


def test_new_macro_rejects_missing_fields(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post(
        "/macros/new", data={"name": "", "command": "uptime", "os": "linux"},
    )

    assert resp.status_code == 200
    assert "obligatoires" in resp.get_data(as_text=True)
    assert macro_store.load_macros() == []


def test_new_macro_rejects_invalid_os(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.post(
        "/macros/new", data={"name": "Uptime", "command": "uptime", "os": "macos"},
    )

    assert resp.status_code == 200
    assert "obligatoires" in resp.get_data(as_text=True)
    assert macro_store.load_macros() == []


def test_edit_macro_404s_for_unknown(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})
    resp = client.get("/macros/does-not-exist/edit")
    assert resp.status_code == 404


def test_edit_macro_updates_name_command_and_os(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})
    macro_id = macro_store.add_macro("Uptime", "uptime", "linux")

    resp = client.post(
        f"/macros/{macro_id}/edit",
        data={"name": "Uptime", "command": "uptime -p", "os": "windows"},
    )

    assert resp.status_code == 302
    macro = macro_store.get_macro(macro_id)
    assert macro["command"] == "uptime -p"
    assert macro["os"] == "windows"


def test_edit_macro_delete_flag_removes_macro(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})
    macro_id = macro_store.add_macro("Uptime", "uptime", "linux")

    resp = client.post(f"/macros/{macro_id}/edit", data={"delete": "1"})

    assert resp.status_code == 302
    assert macro_store.get_macro(macro_id) is None


def test_run_macro_route_requires_login(client, macros_file):
    macro_id = macro_store.add_macro("Uptime", "uptime", "linux")
    resp = client.get(f"/macros/{macro_id}/run")
    assert resp.status_code == 302


def test_run_macro_route_404s_for_unknown_macro(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})
    resp = client.get("/macros/does-not-exist/run")
    assert resp.status_code == 404


def test_run_macro_route_get_shows_machine_groups(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Test", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })
    macro_id = macro_store.add_macro("Uptime", "uptime", "linux")

    resp = client.get(f"/macros/{macro_id}/run")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Serveur Test" in body
    assert "pas d'identifiants mémorisés" in body


def test_run_macro_route_only_lists_machines_matching_macro_os(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Linux", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })
    client.post("/hosts/new", data={
        "name": "Serveur Windows", "os": "windows", "host": "10.0.0.2", "ssh_port": "22",
    })
    macro_id = macro_store.add_macro("Uptime", "uptime", "linux")

    resp = client.get(f"/macros/{macro_id}/run")

    body = resp.get_data(as_text=True)
    assert "Serveur Linux" in body
    assert "Serveur Windows" not in body


def test_run_macro_route_calls_runner_with_selected_machines(client, macros_file, monkeypatch):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur A", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })
    client.post("/hosts/new", data={
        "name": "Serveur B", "os": "linux", "host": "10.0.0.2", "ssh_port": "22",
    })
    macro_id = macro_store.add_macro("Uptime", "uptime", "linux")

    calls = []

    def fake_run_macro(command, machines, **kwargs):
        calls.append((command, sorted(m["id"] for m in machines)))
        return [
            {"machine_id": m["id"], "machine_name": m["name"], "ok": True, "output": "up 3 days"}
            for m in machines
        ]

    monkeypatch.setattr(macro_runner, "run_macro", fake_run_macro)

    resp = client.post(f"/macros/{macro_id}/run", data={"machine_id": ["serveur-a"]})

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert calls == [("uptime", ["serveur-a"])]
    assert "OK" in body
    assert "up 3 days" in body


def test_run_macro_route_records_history_on_execution(client, macros_file, monkeypatch):
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur A", "os": "linux", "host": "10.0.0.1", "ssh_port": "22",
    })
    macro_id = macro_store.add_macro("Uptime", "uptime", "linux")
    monkeypatch.setattr(
        macro_runner, "run_macro",
        lambda command, machines, **kwargs: [
            {"machine_id": m["id"], "machine_name": m["name"], "ok": True, "output": "up 3 days"}
            for m in machines
        ],
    )

    client.post(f"/macros/{macro_id}/run", data={"machine_id": ["serveur-a"]})

    runs = history.get_recent_macro_runs()
    assert len(runs) == 1
    assert runs[0]["macro_name"] == "Uptime"
    assert runs[0]["command"] == "uptime"
    assert runs[0]["results"] == [
        {"machine_id": "serveur-a", "machine_name": "Serveur A", "ok": True, "output": "up 3 days"},
    ]


def test_run_macro_route_does_not_record_history_without_selection(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})
    macro_id = macro_store.add_macro("Uptime", "uptime", "linux")

    client.post(f"/macros/{macro_id}/run", data={})

    assert history.get_recent_macro_runs() == []


def test_macro_history_requires_login(client, macros_file):
    resp = client.get("/macros/history")
    assert resp.status_code == 302


def test_macro_history_shows_recorded_runs(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})
    history.record_macro_run("uptime", "Uptime", "uptime", [
        {"machine_id": "m1", "machine_name": "Serveur A", "ok": True, "output": "up 3 days"},
        {
            "machine_id": "m2", "machine_name": "Serveur B", "ok": False,
            "output": "Échec de connexion",
        },
    ])

    resp = client.get("/macros/history")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Uptime" in body
    assert "1/2 OK" in body
    assert "Serveur A" in body
    assert "Serveur B" in body
    assert "Échec de connexion" in body


def test_macro_history_shows_empty_state_without_runs(client, macros_file):
    client.post("/login", data={"username": "admin", "password": "admin"})

    resp = client.get("/macros/history")

    assert "Aucune macro n'a encore été lancée" in resp.get_data(as_text=True)


def test_run_macro_route_ignores_selected_machine_with_wrong_os(client, macros_file, monkeypatch):
    # Défense en profondeur: même si le formulaire ne propose que les
    # machines du bon OS, la route elle-même refiltre côté serveur --
    # ici la seule machine soumise a le mauvais OS, donc le pool filtré
    # est vide et le lanceur n'est jamais appelé (comme sans sélection).
    client.post("/login", data={"username": "admin", "password": "admin"})
    client.post("/hosts/new", data={
        "name": "Serveur Windows", "os": "windows", "host": "10.0.0.1", "ssh_port": "22",
    })
    macro_id = macro_store.add_macro("Uptime", "uptime", "linux")

    def must_not_be_called(*a, **k):
        raise AssertionError("ne doit pas être appelé pour une machine du mauvais OS")

    monkeypatch.setattr(macro_runner, "run_macro", must_not_be_called)

    resp = client.post(f"/macros/{macro_id}/run", data={"machine_id": ["serveur-windows"]})

    assert "Aucune machine sélectionnée" in resp.get_data(as_text=True)


def test_run_macro_route_with_no_selection_skips_execution(client, macros_file, monkeypatch):
    client.post("/login", data={"username": "admin", "password": "admin"})
    macro_id = macro_store.add_macro("Uptime", "uptime", "linux")

    def must_not_be_called(*a, **k):
        raise AssertionError("ne doit pas être appelé sans machine sélectionnée")

    monkeypatch.setattr(macro_runner, "run_macro", must_not_be_called)

    resp = client.post(f"/macros/{macro_id}/run", data={})

    assert resp.status_code == 200
    assert "Aucune machine sélectionnée" in resp.get_data(as_text=True)
