"""Tests pour ssh_ws.py: enregistrement des sessions dans l'historique
(voir history.py) autour de l'ouverture/fermeture d'un shell — pas de
vraie connexion SSH (ssh_client.connect est monkeypatché).

register_ssh_handlers(socketio) enregistre ses gestionnaires via
@socketio.on(...) sur l'objet passé en argument plutôt que sur un module
global : FakeSocketIO ci-dessous capture juste ces enregistrements pour
pouvoir les invoquer directement, sans dépendre de flask_socketio pour de
vrai."""
import pytest

import history
import ssh_ws


class FakeSocketIO:
    def __init__(self):
        self.handlers = {}
        self.emitted = []

    def on(self, event):
        def decorator(fn):
            self.handlers[event] = fn
            return fn
        return decorator

    def emit(self, event, data=None, room=None):
        self.emitted.append((event, data, room))


class FakeRequest:
    sid = "sid-1"
    remote_addr = "10.0.0.9"


class FakeChannel:
    def recv(self, n):
        return b""  # flux vide: le thread stream_output se termine tout de suite

    def resize_pty(self, width=None, height=None):
        pass

    def send(self, data):
        pass

    def close(self):
        pass


class FakeSSHClient:
    def invoke_shell(self, term=None):
        return FakeChannel()

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _clear_ssh_ws_state():
    # sessions/pending_key_confirmation sont des dicts au niveau module,
    # partagés entre tous les sid — à vider entre chaque test pour ne pas
    # laisser une session du test précédent fausser celui-ci.
    ssh_ws.sessions.clear()
    ssh_ws.pending_key_confirmation.clear()
    yield
    ssh_ws.sessions.clear()
    ssh_ws.pending_key_confirmation.clear()


@pytest.fixture
def wired_socketio(monkeypatch):
    socketio = FakeSocketIO()
    monkeypatch.setattr(ssh_ws, "request", FakeRequest())
    monkeypatch.setattr(ssh_ws.ssh_client, "connect", lambda machine, u, p: FakeSSHClient())
    monkeypatch.setattr(
        ssh_ws, "get_machine",
        lambda mid: {"id": mid, "name": "Serveur Test", "host": "10.0.0.1"},
    )
    ssh_ws.register_ssh_handlers(socketio)
    return socketio


def test_ssh_connect_starts_a_session(wired_socketio, history_db):
    wired_socketio.handlers["ssh_connect"](
        {"machine_id": "m1", "username": "root", "password": "hunter2"},
    )

    sessions = history.get_recent_sessions()
    assert len(sessions) == 1
    assert sessions[0]["machine_id"] == "m1"
    assert sessions[0]["protocol"] == "ssh"
    assert sessions[0]["source_ip"] == "10.0.0.9"
    assert sessions[0]["ended_at"] is None


def test_disconnect_ends_the_session(wired_socketio, history_db):
    wired_socketio.handlers["ssh_connect"](
        {"machine_id": "m1", "username": "root", "password": "hunter2"},
    )
    wired_socketio.handlers["disconnect"]()

    sessions = history.get_recent_sessions()
    assert sessions[0]["ended_at"] is not None


def test_session_start_failure_does_not_block_connection(wired_socketio, monkeypatch, history_db):
    def boom(*a, **k):
        raise OSError("disque plein")

    monkeypatch.setattr(ssh_ws.history, "start_session", boom)

    wired_socketio.handlers["ssh_connect"](
        {"machine_id": "m1", "username": "root", "password": "hunter2"},
    )

    # La connexion SSH elle-même doit réussir malgré l'échec d'enregistrement.
    assert "sid-1" in ssh_ws.sessions
    assert ssh_ws.sessions["sid-1"]["session_id"] is None


def test_disconnect_tolerates_history_failure(wired_socketio, monkeypatch, history_db):
    wired_socketio.handlers["ssh_connect"](
        {"machine_id": "m1", "username": "root", "password": "hunter2"},
    )

    def boom(session_id):
        raise OSError("disque plein")

    monkeypatch.setattr(ssh_ws.history, "end_session", boom)

    wired_socketio.handlers["disconnect"]()  # ne doit pas lever

    assert "sid-1" not in ssh_ws.sessions
