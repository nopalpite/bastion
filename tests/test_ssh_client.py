"""Tests pour ssh_client.py: logique TOFU (Trust On First Use) sur la clé
d'hôte SSH, sans connexion réseau réelle — paramiko.SSHClient est
entièrement remplacé par un faux client qui simule les 3 scénarios que
paramiko peut produire (1ère connexion, clé connue qui correspond, clé qui
a changé)."""
import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

import credentials
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

    def connect(self, hostname, port, username, password, pkey, look_for_keys,
                allow_agent, timeout):
        self.connect_kwargs = {
            "pkey": pkey, "look_for_keys": look_for_keys, "allow_agent": allow_agent,
        }
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


def test_connect_disables_agent_and_key_lookup(monkeypatch):
    """Sans ça, paramiko essaierait aussi, de façon implicite, un agent SSH
    ou des clés dans ~/.ssh/ sur la machine hébergeant Bastion elle-même
    avant le mot de passe/la clé mémorisée — voir le docstring de connect()."""
    created = {}

    def make_fake():
        created["client"] = FakeSSHClient()
        return created["client"]

    monkeypatch.setattr(ssh_client.paramiko, "SSHClient", make_fake)

    ssh_client.connect({"id": "srv-1", "host": "10.0.0.1"}, "user", "pass")

    assert created["client"].connect_kwargs["look_for_keys"] is False
    assert created["client"].connect_kwargs["allow_agent"] is False


def test_connect_passes_pkey_through(monkeypatch):
    created = {}
    monkeypatch.setattr(
        ssh_client.paramiko, "SSHClient",
        lambda: created.setdefault("client", FakeSSHClient()) or created["client"],
    )
    sentinel_pkey = object()

    ssh_client.connect({"id": "srv-1", "host": "10.0.0.1"}, "user", pkey=sentinel_pkey)

    assert created["client"].connect_kwargs["pkey"] is sentinel_pkey


# --- load_private_key: pas de chargeur générique dans paramiko, on essaie
# chaque classe concrète dans l'ordre (voir le docstring de la fonction). --

def _ed25519_pem(passphrase=None):
    key = ed25519.Ed25519PrivateKey.generate()
    encryption = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase else serialization.NoEncryption()
    )
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=encryption,
    ).decode()


def _rsa_pem(passphrase=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encryption = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase else serialization.NoEncryption()
    )
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=encryption,
    ).decode()


def test_load_private_key_ed25519_without_passphrase():
    key = ssh_client.load_private_key(_ed25519_pem())
    assert isinstance(key, paramiko.Ed25519Key)


def test_load_private_key_rsa_with_correct_passphrase():
    key = ssh_client.load_private_key(_rsa_pem("hunter2"), "hunter2")
    assert isinstance(key, paramiko.RSAKey)


def test_load_private_key_encrypted_without_passphrase_raises_clear_error():
    with pytest.raises(ssh_client.PrivateKeyError, match="passphrase"):
        ssh_client.load_private_key(_rsa_pem("hunter2"))


def test_load_private_key_wrong_passphrase_raises():
    with pytest.raises(ssh_client.PrivateKeyError):
        ssh_client.load_private_key(_rsa_pem("hunter2"), "WRONG")


def test_load_private_key_garbage_text_raises():
    with pytest.raises(ssh_client.PrivateKeyError):
        ssh_client.load_private_key("pas une clé du tout")


# --- resolve_stored_auth: centralise le déchiffrement des identifiants
# mémorisés (mot de passe, clé, mot de passe sudo) pour les 3 appelants
# (ssh_ws.py, macro_runner.py, ssh_actions.py). ------------------------

def test_resolve_stored_auth_with_password_only(credentials_key):
    machine = {
        "credentials": {
            "username": "root",
            "password": credentials.encrypt("hunter2"),
        },
    }
    username, password, pkey, sudo_password = ssh_client.resolve_stored_auth(machine)
    assert (username, password, pkey, sudo_password) == ("root", "hunter2", None, None)


def test_resolve_stored_auth_with_key_and_passphrase(credentials_key):
    key_text = _ed25519_pem()
    machine = {
        "credentials": {
            "username": "root",
            "private_key": credentials.encrypt(key_text),
        },
    }
    username, password, pkey, sudo_password = ssh_client.resolve_stored_auth(machine)
    assert username == "root"
    assert password is None
    assert isinstance(pkey, paramiko.Ed25519Key)
    assert sudo_password is None


def test_resolve_stored_auth_decrypts_sudo_password_independently(credentials_key):
    machine = {
        "credentials": {
            "username": "root",
            "private_key": credentials.encrypt(_ed25519_pem()),
            "sudo_password": credentials.encrypt("sudopass"),
        },
    }
    _u, _p, pkey, sudo_password = ssh_client.resolve_stored_auth(machine)
    assert pkey is not None
    assert sudo_password == "sudopass"


def test_resolve_stored_auth_raises_on_invalid_stored_key(credentials_key):
    machine = {
        "credentials": {
            "username": "root",
            "private_key": credentials.encrypt("pas une clé du tout"),
        },
    }
    with pytest.raises(ssh_client.PrivateKeyError):
        ssh_client.resolve_stored_auth(machine)


def test_resolve_stored_auth_with_no_stored_credentials():
    assert ssh_client.resolve_stored_auth({}) == (None, None, None, None)
