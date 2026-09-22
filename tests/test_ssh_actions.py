"""Tests pour ssh_actions.py: actions rapides (reboot/shutdown) via une
commande SSH ponctuelle -- pas de vraie connexion SSH (ssh_client.connect
est monkeypatché, même approche que tests/test_macro_runner.py)."""
import paramiko
import pytest

import ssh_actions
import ssh_client
from ssh_client import HostKeyChanged, PrivateKeyError


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


class FakeStdin:
    def __init__(self):
        self.written = ""
        self.channel = self

    def write(self, data):
        self.written += data

    def flush(self):
        pass

    def shutdown_write(self):
        pass


class FakeSSHClient:
    def __init__(self, exit_status=0, stderr=b""):
        self._exit_status = exit_status
        self._stderr = stderr
        self.closed = False
        self.last_command = None
        self.stdin = FakeStdin()

    def exec_command(self, command, timeout=None):
        self.last_command = command
        return self.stdin, FakeStream(b"", self._exit_status), FakeStream(self._stderr)

    def close(self):
        self.closed = True


LINUX_MACHINE_WITH_PASSWORD = {
    "id": "m1", "name": "Linux", "os": "linux", "host": "10.0.0.1",
    "credentials": {"username": "root", "password": "encrypted-blob"},
}


@pytest.fixture(autouse=True)
def _decrypt(monkeypatch):
    monkeypatch.setattr(ssh_client.credentials, "decrypt", lambda blob: "hunter2")


@pytest.fixture(autouse=True)
def _get_machine(monkeypatch):
    machines = {}
    monkeypatch.setattr(ssh_actions, "get_machine", lambda mid: machines.get(mid))
    return machines


def test_run_action_rejects_unknown_action(_get_machine):
    _get_machine["m1"] = LINUX_MACHINE_WITH_PASSWORD
    with pytest.raises(ssh_actions.ActionError, match="Action inconnue"):
        ssh_actions.run_action("m1", "format-disk")


def test_run_action_rejects_unknown_machine():
    with pytest.raises(ssh_actions.ActionError, match="Machine inconnue"):
        ssh_actions.run_action("ghost", "reboot")


def test_run_action_requires_credentials_when_none_stored(_get_machine):
    _get_machine["m2"] = {"id": "m2", "name": "Sans creds", "os": "linux", "host": "10.0.0.2"}

    with pytest.raises(ssh_actions.MissingCredentialsError):
        ssh_actions.run_action("m2", "reboot")


def test_run_action_sends_ssh_password_as_sudo_password_by_default(monkeypatch, _get_machine):
    _get_machine["m1"] = LINUX_MACHINE_WITH_PASSWORD
    client = FakeSSHClient(exit_status=0)
    monkeypatch.setattr(ssh_actions.ssh_client, "connect", lambda *a, **k: client)

    ssh_actions.run_action("m1", "reboot")

    assert client.last_command == "sudo -S -p '' reboot"
    assert client.stdin.written == "hunter2\n"
    assert client.closed


def test_run_action_uses_dedicated_sudo_password_over_ssh_password(monkeypatch, _get_machine):
    machine = {
        "id": "m3", "name": "Avec sudo dédié", "os": "linux", "host": "10.0.0.3",
        "credentials": {
            "username": "root", "password": "encrypted-blob", "sudo_password": "encrypted-sudo",
        },
    }
    _get_machine["m3"] = machine
    monkeypatch.setattr(
        ssh_client.credentials, "decrypt",
        lambda blob: "sudopass" if blob == "encrypted-sudo" else "hunter2",
    )
    client = FakeSSHClient(exit_status=0)
    monkeypatch.setattr(ssh_actions.ssh_client, "connect", lambda *a, **k: client)

    ssh_actions.run_action("m3", "shutdown")

    assert client.stdin.written == "sudopass\n"


def test_run_action_fails_clearly_for_linux_with_key_only_auth_and_no_sudo_password(
    monkeypatch, _get_machine,
):
    machine = {
        "id": "m4", "name": "Clé seule", "os": "linux", "host": "10.0.0.4",
        "credentials": {"username": "root", "private_key": "encrypted-key"},
    }
    _get_machine["m4"] = machine
    monkeypatch.setattr(ssh_client, "load_private_key", lambda text, passphrase=None: object())
    connect_calls = []
    monkeypatch.setattr(
        ssh_actions.ssh_client, "connect",
        lambda *a, **k: connect_calls.append(1) or FakeSSHClient(),
    )

    with pytest.raises(ssh_actions.MissingCredentialsError, match="mot de passe sudo"):
        ssh_actions.run_action("m4", "reboot")
    assert connect_calls == []  # jamais tenté de se connecter


def test_run_action_windows_does_not_require_sudo_password(monkeypatch, _get_machine):
    """Windows n'a pas de sudo: une machine authentifiée par clé seule doit
    pouvoir redémarrer sans aucun mot de passe mémorisé."""
    machine = {
        "id": "m5", "name": "Windows clé", "os": "windows", "host": "10.0.0.5",
        "credentials": {"username": "Administrateur", "private_key": "encrypted-key"},
    }
    _get_machine["m5"] = machine
    sentinel_pkey = object()
    monkeypatch.setattr(ssh_client, "load_private_key", lambda text, passphrase=None: sentinel_pkey)
    client = FakeSSHClient(exit_status=0)
    connect_calls = []
    monkeypatch.setattr(
        ssh_actions.ssh_client, "connect",
        lambda machine, user, pwd, pkey=None: connect_calls.append((user, pwd, pkey)) or client,
    )

    ssh_actions.run_action("m5", "reboot")

    assert connect_calls == [("Administrateur", None, sentinel_pkey)]
    assert client.last_command == "shutdown /r /t 0"


def test_run_action_reports_invalid_stored_key(_get_machine, monkeypatch):
    machine = {
        "id": "m6", "name": "Clé cassée", "os": "linux", "host": "10.0.0.6",
        "credentials": {"username": "root", "private_key": "encrypted-garbage"},
    }
    _get_machine["m6"] = machine

    def raise_invalid_key(text, passphrase=None):
        raise PrivateKeyError("format non reconnu")

    monkeypatch.setattr(ssh_client, "load_private_key", raise_invalid_key)

    with pytest.raises(ssh_actions.MissingCredentialsError, match="Clé SSH mémorisée invalide"):
        ssh_actions.run_action("m6", "reboot")


def test_run_action_request_password_overrides_stored_key(monkeypatch, _get_machine):
    """Un mot de passe fourni dans la requête (formulaire ad hoc) reste
    prioritaire et n'est jamais combiné à une clé mémorisée -- voir
    _resolve_credentials."""
    machine = {
        "id": "m7", "name": "Les deux", "os": "linux", "host": "10.0.0.7",
        "credentials": {"username": "root", "private_key": "encrypted-key"},
    }
    _get_machine["m7"] = machine
    connect_calls = []
    client = FakeSSHClient(exit_status=0)
    monkeypatch.setattr(
        ssh_actions.ssh_client, "connect",
        lambda machine, user, pwd, pkey=None: connect_calls.append((user, pwd, pkey)) or client,
    )

    ssh_actions.run_action("m7", "reboot", username="admin", password="ad-hoc-pass")

    assert connect_calls == [("admin", "ad-hoc-pass", None)]
    assert client.stdin.written == "ad-hoc-pass\n"


def test_run_action_reports_sudo_password_rejected(monkeypatch, _get_machine):
    _get_machine["m1"] = LINUX_MACHINE_WITH_PASSWORD
    client = FakeSSHClient(exit_status=1, stderr=b"Sorry, try again.\n")
    monkeypatch.setattr(ssh_actions.ssh_client, "connect", lambda *a, **k: client)

    with pytest.raises(ssh_actions.MissingCredentialsError, match="sudo"):
        ssh_actions.run_action("m1", "reboot")


def test_run_action_treats_connection_drop_during_reboot_as_success(monkeypatch, _get_machine):
    _get_machine["m1"] = LINUX_MACHINE_WITH_PASSWORD

    class DroppedChannel(FakeChannel):
        def recv_exit_status(self):
            raise EOFError("connexion coupée")

    client = FakeSSHClient(exit_status=0)
    dropped_stdout = FakeStream(b"")
    dropped_stdout.channel = DroppedChannel(0)
    client.exec_command = lambda command, timeout=None: (
        client.stdin, dropped_stdout, FakeStream(b""),
    )
    monkeypatch.setattr(ssh_actions.ssh_client, "connect", lambda *a, **k: client)

    ssh_actions.run_action("m1", "reboot")  # ne doit pas lever


def test_run_action_reports_authentication_failure(monkeypatch, _get_machine):
    _get_machine["m1"] = LINUX_MACHINE_WITH_PASSWORD

    def raise_auth(*a, **k):
        raise paramiko.AuthenticationException("nope")

    monkeypatch.setattr(ssh_actions.ssh_client, "connect", raise_auth)

    with pytest.raises(ssh_actions.MissingCredentialsError, match="Authentification refusée"):
        ssh_actions.run_action("m1", "reboot")


def test_run_action_reports_host_key_changed(monkeypatch, _get_machine):
    _get_machine["m1"] = LINUX_MACHINE_WITH_PASSWORD

    def raise_changed(*a, **k):
        raise HostKeyChanged(None, "la clé a changé")

    monkeypatch.setattr(ssh_actions.ssh_client, "connect", raise_changed)

    with pytest.raises(ssh_actions.ActionError, match="clé d'hôte"):
        ssh_actions.run_action("m1", "reboot")
