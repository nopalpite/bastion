"""Tests pour macro_runner.py: exécution d'une macro sur un pool de
machines — pas de vraie connexion SSH (ssh_client.connect est
monkeypatché, même approche que tests/test_ssh_ws.py)."""
import eventlet
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


class FakeStdin:
    """Capture ce qui est écrit sur stdin (voir _prepare_sudo_command /
    _run_on_machine, qui y écrit le mot de passe sudo)."""

    def __init__(self):
        self.written = ""
        self.flushed = False
        self.shutdown = False
        self.channel = self

    def write(self, data):
        self.written += data

    def flush(self):
        self.flushed = True

    def shutdown_write(self):
        self.shutdown = True


class FakeSSHClient:
    def __init__(self, exit_status=0, stdout=b"ok\n", stderr=b""):
        self._exit_status = exit_status
        self._stdout = stdout
        self._stderr = stderr
        self.closed = False
        self.last_command = None
        self.stdin = FakeStdin()

    def exec_command(self, command, timeout=None):
        self.last_command = command
        return self.stdin, FakeStream(self._stdout, self._exit_status), FakeStream(self._stderr)

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


def test_run_on_machine_fails_gracefully_when_decrypt_raises(monkeypatch):
    # credentials.decrypt() ne rattrape que InvalidToken en interne -- une
    # BASTION_CREDENTIALS_KEY malformée fait lever autre chose (ValueError
    # depuis Fernet(...)) : ne doit jamais remonter hors de cette machine.
    def raise_value_error(blob):
        raise ValueError("clé de chiffrement invalide")

    monkeypatch.setattr(macro_runner.credentials, "decrypt", raise_value_error)

    result = macro_runner._run_on_machine(MACHINE_WITH_CREDS, "uptime", timeout=5)

    assert result["ok"] is False
    assert "déchiffrement" in result["output"].lower()


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


class _HangingChannel(FakeChannel):
    """Simule recv_exit_status() qui ne revient jamais dans le délai
    imparti (ex: la commande produit assez de sortie pour remplir la
    fenêtre de flux SSH avant de se terminer, voir le commentaire dans
    macro_runner._run_on_machine) -- eventlet.sleep() est interruptible
    par eventlet.Timeout exactement comme le serait un vrai
    threading.Event.wait() sous eventlet.monkey_patch() en production."""

    def recv_exit_status(self):
        eventlet.sleep(5)
        return super().recv_exit_status()


def test_run_on_machine_times_out_when_command_never_finishes(monkeypatch):
    client = FakeSSHClient()
    hanging_stdout = FakeStream(b"")
    hanging_stdout.channel = _HangingChannel(0)
    client.exec_command = lambda command, timeout=None: (
        client.stdin, hanging_stdout, FakeStream(b""),
    )
    monkeypatch.setattr(macro_runner.ssh_client, "connect", lambda *a, **k: client)

    result = macro_runner._run_on_machine(MACHINE_WITH_CREDS, "uptime", timeout=0.2)

    assert result["ok"] is False
    assert "délai imparti" in result["output"]
    assert client.closed  # la connexion est bien refermée malgré le timeout


# --- sudo: exec_command n'alloue pas de pty, donc "sudo" ne peut pas
# demander son mot de passe interactivement -- voir _prepare_sudo_command,
# même technique que ssh_actions.py pour reboot/shutdown -------------------

def test_prepare_sudo_command_inserts_password_flags():
    command, needs_password = macro_runner._prepare_sudo_command("sudo apt update")

    assert command == "sudo -S -p '' apt update"
    assert needs_password is True


def test_prepare_sudo_command_handles_bare_sudo():
    command, needs_password = macro_runner._prepare_sudo_command("sudo")

    assert command == "sudo -S -p ''"
    assert needs_password is True


def test_prepare_sudo_command_leaves_non_sudo_command_untouched():
    command, needs_password = macro_runner._prepare_sudo_command("uptime")

    assert command == "uptime"
    assert needs_password is False


def test_prepare_sudo_command_does_not_detect_sudo_mid_command():
    # Limite documentée: seul un "sudo" en tête de commande est détecté.
    command, needs_password = macro_runner._prepare_sudo_command("cd /tmp && sudo ls")

    assert command == "cd /tmp && sudo ls"
    assert needs_password is False


def test_run_on_machine_sends_password_on_stdin_for_sudo_command(monkeypatch):
    client = FakeSSHClient(exit_status=0)
    monkeypatch.setattr(macro_runner.ssh_client, "connect", lambda *a, **k: client)

    macro_runner._run_on_machine(MACHINE_WITH_CREDS, "sudo apt update", timeout=5)

    assert client.last_command == "sudo -S -p '' apt update"
    assert client.stdin.written == "hunter2\n"
    assert client.stdin.flushed
    assert client.stdin.shutdown


def test_run_on_machine_does_not_write_stdin_for_non_sudo_command(monkeypatch):
    client = FakeSSHClient(exit_status=0)
    monkeypatch.setattr(macro_runner.ssh_client, "connect", lambda *a, **k: client)

    macro_runner._run_on_machine(MACHINE_WITH_CREDS, "uptime", timeout=5)

    assert client.last_command == "uptime"
    assert client.stdin.written == ""


def test_run_on_machine_hints_at_rejected_sudo_password(monkeypatch):
    client = FakeSSHClient(exit_status=1, stderr=b"Sorry, try again.\n")
    monkeypatch.setattr(macro_runner.ssh_client, "connect", lambda *a, **k: client)

    result = macro_runner._run_on_machine(MACHINE_WITH_CREDS, "sudo apt update", timeout=5)

    assert result["ok"] is False
    assert "mot de passe sudo probablement refusé" in result["output"]


def test_run_on_machine_does_not_hint_sudo_for_non_sudo_failure(monkeypatch):
    client = FakeSSHClient(exit_status=1, stderr=b"Sorry, command not found.\n")
    monkeypatch.setattr(macro_runner.ssh_client, "connect", lambda *a, **k: client)

    result = macro_runner._run_on_machine(MACHINE_WITH_CREDS, "nope", timeout=5)

    assert "mot de passe sudo" not in result["output"]


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
