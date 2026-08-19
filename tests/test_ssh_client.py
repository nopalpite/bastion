"""Tests pour ssh_client.py: logique TOFU (Trust On First Use) sur la clé
d'hôte SSH, sans connexion réseau réelle — paramiko.SSHClient est
entièrement remplacé par un faux client qui simule les 3 scénarios que
paramiko peut produire (1ère connexion, clé connue qui correspond, clé qui
a changé)."""
import paramiko
import pytest

import ssh_client


class FakeKey:
    def __init__(self, name="ssh-ed25519", fingerprint=b"\x01\x02\x03\x04"):
        self._name = name
        self._fingerprint = fingerprint

    def get_name(self):
        return self._name

    def get_fingerprint(self):
        return self._fingerprint

    def get_base64(self):
        return "ZmFrZWtleQ=="


class FakeSSHClient:
    """Remplace paramiko.SSHClient: pas de vrai socket, connect() simule le
    comportement que paramiko aurait selon le scénario configuré par le
    test (via new_key_on_connect / raise_mismatch)."""

    def __init__(self, new_key_on_connect=None, raise_mismatch=None):
        self.policy = None
        self._host_keys = {}
        self._new_key_on_connect = new_key_on_connect
        self._raise_mismatch = raise_mismatch
        self.closed = False

    def get_host_keys(self):
        return self._host_keys

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, hostname, port, username, password, timeout):
        if self._raise_mismatch is not None:
            raise self._raise_mismatch
        if self._new_key_on_connect is not None:
            # simule la présentation d'une clé jamais vue: paramiko
            # appelle missing_host_key() sur la policy configurée
            self.policy.missing_host_key(self, hostname, self._new_key_on_connect)

    def close(self):
        self.closed = True


def test_first_connection_accepts_and_stores_the_key(monkeypatch):
    fake_key = FakeKey()
    stored_calls = []
    monkeypatch.setattr(ssh_client, "set_machine_host_key", lambda *args: stored_calls.append(args))
    monkeypatch.setattr(
        ssh_client.paramiko, "SSHClient",
        lambda: FakeSSHClient(new_key_on_connect=fake_key),
    )

    machine = {"id": "srv-1", "host": "10.0.0.1"}  # pas de host_key mémorisée
    client = ssh_client.connect(machine, "user", "pass")

    assert isinstance(client, FakeSSHClient)
    assert stored_calls == [("srv-1", fake_key.get_name(), fake_key.get_base64())]


def test_known_matching_key_connects_without_storing_anything(monkeypatch):
    stored_calls = []
    monkeypatch.setattr(ssh_client, "set_machine_host_key", lambda *args: stored_calls.append(args))
    # type absent de KEY_CLASSES: _deserialize_key rend None sans tenter de
    # vrai parsing de clé — le test porte sur connect(), pas sur ce détail
    monkeypatch.setattr(
        ssh_client.paramiko, "SSHClient",
        lambda: FakeSSHClient(),  # connect() "réussit" sans lever d'exception
    )

    machine = {"id": "srv-1", "host": "10.0.0.1", "host_key": {"type": "ssh-fake", "key": "xx"}}
    ssh_client.connect(machine, "user", "pass")

    assert stored_calls == []  # rien de nouveau à mémoriser


def test_changed_key_raises_host_key_changed(monkeypatch):
    old_key = FakeKey(fingerprint=b"\x01\x01\x01\x01")
    new_key = FakeKey(fingerprint=b"\xff\xff\xff\xff")
    mismatch = paramiko.BadHostKeyException("10.0.0.1", new_key, old_key)
    monkeypatch.setattr(
        ssh_client.paramiko, "SSHClient",
        lambda: FakeSSHClient(raise_mismatch=mismatch),
    )

    machine = {"id": "srv-1", "host": "10.0.0.1", "host_key": {"type": "ssh-fake", "key": "xx"}}

    with pytest.raises(ssh_client.HostKeyChanged) as exc_info:
        ssh_client.connect(machine, "user", "pass")
    assert exc_info.value.new_key is new_key


def test_fingerprint_formats_bytes_as_hex_pairs():
    key = FakeKey(fingerprint=b"\xab\xcd\x01")
    assert ssh_client.fingerprint(key) == "ab:cd:01"


def test_fingerprint_none_key_returns_none():
    assert ssh_client.fingerprint(None) is None
