"""Tests pour monitor.py: ping_host/check_port et l'assemblage des
vérifications par machine — sans dépendre d'une vraie machine distante."""
import socket
import subprocess

import history
import monitor


def test_check_port_open_local_listener():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert monitor.check_port("127.0.0.1", port) is True
    finally:
        server.close()


def test_check_port_nothing_listening_returns_false():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()  # port fermé juste après: rien n'écoute dessus
    assert monitor.check_port("127.0.0.1", port) is False


def test_ping_host_success(monkeypatch):
    monkeypatch.setattr(
        monitor.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0),
    )
    up, latency = monitor.ping_host("10.0.0.1")
    assert up is True
    assert latency is not None


def test_ping_host_non_zero_exit(monkeypatch):
    monkeypatch.setattr(
        monitor.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=1),
    )
    up, latency = monitor.ping_host("10.0.0.1")
    assert up is False
    assert latency is None


def test_ping_host_timeout_is_treated_as_down(monkeypatch):
    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ping", timeout=1)

    monkeypatch.setattr(monitor.subprocess, "run", raise_timeout)
    up, latency = monitor.ping_host("10.0.0.1")
    assert up is False
    assert latency is None


def test_check_services_probes_ssh_and_vnc(monkeypatch):
    # VNC est sondé mais UNIQUEMENT via vnc_tls_bridge.probe_available —
    # jamais un check_port() générique dessus, qui serait une simple
    # connexion TCP sans même lire la poignée de main RFB (voir le
    # docstring de probe_available pour le raisonnement complet sur ce qui
    # est sûr ou non vis-à-vis du blacklistage anti-bruteforce de RealVNC).
    monkeypatch.setattr(monitor, "check_port", lambda host, port, timeout=None: True)
    monkeypatch.setattr(monitor.vnc_tls_bridge, "probe_available",
                         lambda machine, timeout=None: True)
    machine = {"host": "10.0.0.1", "ssh_port": 22, "vnc_port": 5900}
    services = monitor._check_services(machine)
    assert services == {"ssh": True, "vnc": True}


def test_check_services_vnc_down_reported_independently_of_ssh(monkeypatch):
    monkeypatch.setattr(monitor, "check_port", lambda host, port, timeout=None: True)
    monkeypatch.setattr(monitor.vnc_tls_bridge, "probe_available",
                         lambda machine, timeout=None: False)
    machine = {"host": "10.0.0.1", "ssh_port": 22, "vnc_port": 5900}
    services = monitor._check_services(machine)
    assert services == {"ssh": True, "vnc": False}


def test_check_services_skips_vnc_without_vnc_port_configured(monkeypatch):
    probe_calls = []
    monkeypatch.setattr(monitor, "check_port", lambda host, port, timeout=None: True)
    monkeypatch.setattr(
        monitor.vnc_tls_bridge, "probe_available",
        lambda machine, timeout=None: probe_calls.append(machine) or True,
    )
    services = monitor._check_services({"host": "10.0.0.1", "ssh_port": 22})
    assert services == {"ssh": True}
    assert probe_calls == []  # jamais appelé si pas de vnc_port


def test_check_services_without_ssh_port_configured(monkeypatch):
    monkeypatch.setattr(monitor, "check_port", lambda host, port, timeout=None: True)
    services = monitor._check_services({"host": "10.0.0.1"})
    assert services == {}


def test_run_checks_once_updates_status_store(monkeypatch):
    machine = {"id": "m1", "host": "10.0.0.1", "ssh_port": 22}
    monkeypatch.setattr(monitor, "load_machines", lambda: [machine])
    monkeypatch.setattr(monitor, "ping_host", lambda host, timeout=None: (True, 12.3))
    monkeypatch.setattr(monitor, "check_port", lambda host, port, timeout=None: True)

    results = monitor.run_checks_once()

    assert results["m1"]["status"] == "up"
    assert results["m1"]["latency_ms"] == 12.3
    assert results["m1"]["services"] == {"ssh": True}
    assert monitor.get_status_snapshot()["m1"]["status"] == "up"


# --- _record_history: écrit dans history.py (page /stats), séparément de
# run_checks_once() ci-dessus pour ne pas lui donner un effet de bord
# caché sur un vrai fichier SQLite (voir le commentaire de _record_history
# dans monitor.py). ---------------------------------------------------

def test_record_history_writes_each_result(history_db):
    results = {
        "m1": {"status": "up", "latency_ms": 12.3, "services": {}, "checked_at": 0},
        "m2": {"status": "down", "latency_ms": None, "services": {}, "checked_at": 0},
    }

    monitor._record_history(results)

    assert history.get_uptime_percentage("m1", since_seconds=3600) == 100.0
    assert history.get_uptime_percentage("m2", since_seconds=3600) == 0.0


def test_record_history_does_not_raise_on_write_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("disque plein")

    monkeypatch.setattr(monitor.history, "record_check", boom)
    # Ne doit pas lever, juste logger (voir le commentaire de la fonction) —
    # la boucle de monitoring ne doit pas planter pour un souci d'écriture
    # sur ce fichier secondaire.
    monitor._record_history({"m1": {"status": "up", "latency_ms": 1.0}})
