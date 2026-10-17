"""Tests pour credentials.py: chiffrement des identifiants mémorisés."""
from cryptography.fernet import Fernet

import credentials


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("BASTION_CREDENTIALS_KEY", raising=False)
    assert not credentials.credentials_enabled()
    assert credentials.encrypt("secret") is None
    assert credentials.decrypt("anything") is None


def test_encrypt_decrypt_roundtrip(credentials_key):
    assert credentials.credentials_enabled()
    token = credentials.encrypt("hunter2")
    assert token is not None
    assert token != "hunter2"  # jamais stocké en clair
    assert credentials.decrypt(token) == "hunter2"


def test_encrypt_empty_input_returns_none(credentials_key):
    assert credentials.encrypt("") is None
    assert credentials.encrypt(None) is None


def test_decrypt_missing_token_returns_none(credentials_key):
    assert credentials.decrypt(None) is None
    assert credentials.decrypt("") is None


def test_decrypt_invalid_token_returns_none(credentials_key):
    assert credentials.decrypt("ceci-n-est-pas-un-token-valide") is None


def test_decrypt_after_key_rotation_returns_none(credentials_key, monkeypatch):
    token = credentials.encrypt("hunter2")
    # simule un changement de BASTION_CREDENTIALS_KEY entre le chiffrement
    # et le déchiffrement (ex: clé régénérée) plutôt que de planter
    monkeypatch.setenv("BASTION_CREDENTIALS_KEY", Fernet.generate_key().decode())
    assert credentials.decrypt(token) is None


# --- vnc_login: identifiants VNC effectifs (propres > défaut serveur > mot de passe SSH) ---

def _clear_vnc_defaults(monkeypatch):
    for name in ("BASTION_DEFAULT_VNC_PASSWORD", "BASTION_DEFAULT_SSH_USER"):
        monkeypatch.delenv(name, raising=False)


def test_vnc_login_machine_own_vnc_credentials_win(credentials_key, monkeypatch):
    _clear_vnc_defaults(monkeypatch)
    monkeypatch.setenv("BASTION_DEFAULT_VNC_PASSWORD", "defaut-serveur")
    machine = {
        "vnc_username": "vncuser", "vnc_password": credentials.encrypt("vnc-propre"),
        "credentials": {"username": "sshuser", "password": credentials.encrypt("ssh-pw")},
    }
    assert credentials.vnc_login(machine) == ("vncuser", "vnc-propre")


def test_vnc_login_server_default_password_beats_the_ssh_password(credentials_key, monkeypatch):
    _clear_vnc_defaults(monkeypatch)
    monkeypatch.setenv("BASTION_DEFAULT_VNC_PASSWORD", "defaut-serveur")
    machine = {"credentials": {"username": "sshuser", "password": credentials.encrypt("ssh-pw")}}
    assert credentials.vnc_login(machine) == ("sshuser", "defaut-serveur")


def test_vnc_login_falls_back_to_the_stored_ssh_account_and_password(credentials_key, monkeypatch):
    _clear_vnc_defaults(monkeypatch)
    machine = {"credentials": {"username": "sshuser", "password": credentials.encrypt("ssh-pw")}}
    assert credentials.vnc_login(machine) == ("sshuser", "ssh-pw")


def test_vnc_login_user_falls_back_to_the_default_ssh_account(monkeypatch):
    _clear_vnc_defaults(monkeypatch)
    monkeypatch.setenv("BASTION_DEFAULT_SSH_USER", "pi")
    monkeypatch.setenv("BASTION_DEFAULT_VNC_PASSWORD", "defaut-serveur")
    assert credentials.vnc_login({}) == ("pi", "defaut-serveur")


def test_vnc_login_own_password_without_own_user_still_gets_the_ssh_account(
    credentials_key, monkeypatch,
):
    _clear_vnc_defaults(monkeypatch)
    machine = {
        "vnc_password": credentials.encrypt("vnc-propre"),
        "credentials": {"username": "sshuser", "password": credentials.encrypt("ssh-pw")},
    }
    assert credentials.vnc_login(machine) == ("sshuser", "vnc-propre")


def test_vnc_login_key_only_ssh_has_no_password_to_reuse(credentials_key, monkeypatch):
    """SSH par clé seule : aucun mot de passe SSH à réutiliser, le VNC retombe sur la saisie."""
    _clear_vnc_defaults(monkeypatch)
    machine = {"credentials": {"username": "sshuser", "private_key": credentials.encrypt("pem")}}
    assert credentials.vnc_login(machine) == ("sshuser", None)


def test_vnc_login_nothing_configured(monkeypatch):
    _clear_vnc_defaults(monkeypatch)
    assert credentials.vnc_login({}) == (None, None)
