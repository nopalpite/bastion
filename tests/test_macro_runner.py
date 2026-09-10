"""Tests pour macro_runner.py: exécution d'une macro sur un pool de
machines — pas de vraie connexion SSH (ssh_client.connect est
monkeypatché, même approche que tests/test_ssh_ws.py)."""
import paramiko
import pytest

import macro_runner
from ssh_client import HostKeyChanged


class FakeStream:
    def __init__(self, data=b"", exit_status=0):
        self._data = data
        self.channel = FakeChannel(exit_status)

    def read(self):
        return self._data


class FakeChannel:
    def __init__(self, exit_status):
        self._exit_status = exit_status

    def recv_exit_status(self):
        return self._exit_status


class FakeSSHClient:
    def __init__(self, exit_status=0, stdout=b"ok\n", stderr=b""):
        self._exit_status = exit_status
        self._stdout = stdout
        self._stderr = stderr
        self.closed = False

    def exec_command(self, command, timeout=None):
        return None, FakeStream(self._stdout, self._exit_status), FakeStream(self._stderr)

    def close(self):
        self.closed = True


MACHINE_WITH_CREDS = {
    "id": "m1", "name": "Serveur Test", "host": "10.0.0.1",
    "credentials": {"username": "root", "password": "encrypted-blob"},
}


def _machine_without_creds():
    return {"id": "m2", "name": "Sans identifiants", "host": "10.0.0.2"}


@pytest.fixture(autouse=True)
def _decrypt(monkeypatch):
    monkeypatch.setattr(macro_runner.credentials, "decrypt", lambda blob: "hunter2")


def test_run_on_machine_fails_without_stored_credentials():
    result = macro_runner._run_on_machine(_machine_without_creds(), "uptime", timeout=5)

    assert result["ok"] is False
    assert "identifiant" in result["output"].lower()


def test_run_on_machine_connects_with_decrypted_password(monkeypatch):
    connect_calls = []

    def fake_connect(machine, username, password, timeout=None):
        connect_calls.append((machine["id"], username, password))
        return FakeSSHClient()

    monkeypatch.setattr(macro_runner.ssh_client, "connect", fake_connect)

    macro_runner._run_on_machine(MACHINE_WITH_CREDS, "uptime", timeout=5)

    assert connect_calls == [("m1", "root", "hunter2")]


def test_run_on_machine_reports_success_and_output(monkeypatch):
    monkeypatch.setattr(
        macro_runner.ssh_client, "connect",
        lambda *a, **k: FakeSSHClient(exit_status=0, stdout=b"salut\n"),
    )

    result = macro_runner._run_on_machine(MACHINE_WITH_CREDS, "echo salut", timeout=5)

    assert result == {
        "machine_id": "m1", "machine_name": "Serveur Test", "ok": True, "output": "salut",
    }


def test_run_on_machine_reports_nonzero_exit_status_as_failure(monkeypatch):
    monkeypatch.setattr(
        macro_runner.ssh_client, "connect",
        lambda *a, **k: FakeSSHClient(exit_status=1, stderr=b"command not found\n"),
    )

    result = macro_runner._run_on_machine(MACHINE_WITH_CREDS, "nope", timeout=5)

    assert result["ok"] is False
    assert "command not found" in result["output"]


def test_run_on_machine_closes_client_even_on_command_failure(monkeypatch):
    client = FakeSSHClient(exit_status=1)
    monkeypatch.setattr(macro_runner.ssh_client, "connect", lambda *a, **k: client)

    macro_runner._run_on_machine(MACHINE_WITH_CREDS, "nope", timeout=5)

    assert client.closed


def test_run_on_machine_reports_authentication_failure(monkeypatch):
    def raise_auth(*a, **k):
        raise paramiko.AuthenticationException("nope")

    monkeypatch.setattr(macro_runner.ssh_client, "connect", raise_auth)

    result = macro_runner._run_on_machine(MACHINE_WITH_CREDS, "uptime", timeout=5)

    assert result["ok"] is False
    assert "authentification" in result["output"].lower()


def test_run_on_machine_reports_host_key_changed(monkeypatch):
    def raise_changed(*a, **k):
        raise HostKeyChanged(None, "la clé a changé")

    monkeypatch.setattr(macro_runner.ssh_client, "connect", raise_changed)

    result = macro_runner._run_on_machine(MACHINE_WITH_CREDS, "uptime", timeout=5)

    assert result["ok"] is False
    assert "clé d'hôte" in result["output"]


def test_run_on_machine_reports_generic_connection_error(monkeypatch):
    def raise_oserror(*a, **k):
        raise OSError("connexion refusée")

    monkeypatch.setattr(macro_runner.ssh_client, "connect", raise_oserror)

    result = macro_runner._run_on_machine(MACHINE_WITH_CREDS, "uptime", timeout=5)

    assert result["ok"] is False
    assert "connexion refusée" in result["output"]


def test_run_macro_preserves_machine_order(monkeypatch):
    machines = [
        {"id": "a", "name": "A"}, {"id": "b", "name": "B"}, {"id": "c", "name": "C"},
    ]
    monkeypatch.setattr(
        macro_runner, "_run_on_machine",
        lambda machine, command, timeout: {"machine_id": machine["id"]},
    )

    results = macro_runner.run_macro("uptime", machines)

    assert [r["machine_id"] for r in results] == ["a", "b", "c"]
