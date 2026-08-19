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
