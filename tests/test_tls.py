"""Tests pour tls.py: certificat TLS optionnel (app, VNC) sans reverse
proxy — voir son docstring."""
import os

import config
import tls


def test_generate_self_signed_creates_cert_and_key(tmp_path):
    cert_path = tmp_path / "bastion.crt"
    key_path = tmp_path / "bastion.key"

    tls.generate_self_signed(str(cert_path), str(key_path))

    assert cert_path.exists()
    assert key_path.exists()
    assert cert_path.read_text().startswith("-----BEGIN CERTIFICATE-----")
    assert "PRIVATE KEY" in key_path.read_text()


def test_generate_self_signed_creates_parent_directory(tmp_path):
    cert_path = tmp_path / "tls" / "bastion.crt"
    key_path = tmp_path / "tls" / "bastion.key"

    tls.generate_self_signed(str(cert_path), str(key_path))

    assert cert_path.exists()


def test_generate_self_signed_does_not_regenerate_existing_pair(tmp_path):
    # Régénérer à chaque démarrage changerait l'empreinte à chaque fois et
    # provoquerait un avertissement navigateur répété pour rien -- voir le
    # commentaire de generate_self_signed.
    cert_path = tmp_path / "bastion.crt"
    key_path = tmp_path / "bastion.key"
    tls.generate_self_signed(str(cert_path), str(key_path))
    original_cert = cert_path.read_bytes()
    original_key = key_path.read_bytes()

    tls.generate_self_signed(str(cert_path), str(key_path))

    assert cert_path.read_bytes() == original_cert
    assert key_path.read_bytes() == original_key


def test_resolve_cert_paths_disabled_by_default(monkeypatch):
    monkeypatch.setattr(config, "TLS_CERT", "")
    monkeypatch.setattr(config, "TLS_KEY", "")
    monkeypatch.setattr(config, "TLS_SELFSIGNED", False)

    assert tls.resolve_cert_paths() == (None, None)


def test_resolve_cert_paths_prefers_explicit_cert_over_selfsigned(monkeypatch, tmp_path):
    cert_path = tmp_path / "custom.crt"
    key_path = tmp_path / "custom.key"
    cert_path.write_text("fake-cert")
    key_path.write_text("fake-key")
    monkeypatch.setattr(config, "TLS_CERT", str(cert_path))
    monkeypatch.setattr(config, "TLS_KEY", str(key_path))
    monkeypatch.setattr(config, "TLS_SELFSIGNED", True)

    assert tls.resolve_cert_paths() == (str(cert_path), str(key_path))


def test_resolve_cert_paths_generates_selfsigned_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TLS_CERT", "")
    monkeypatch.setattr(config, "TLS_KEY", "")
    monkeypatch.setattr(config, "TLS_SELFSIGNED", True)
    monkeypatch.setattr(tls, "SELFSIGNED_CERT", str(tmp_path / "bastion.crt"))
    monkeypatch.setattr(tls, "SELFSIGNED_KEY", str(tmp_path / "bastion.key"))

    cert_path, key_path = tls.resolve_cert_paths()

    assert cert_path == str(tmp_path / "bastion.crt")
    assert os.path.exists(cert_path)
    assert os.path.exists(key_path)
