"""Certificat TLS pour servir HTTPS/WSS directement depuis Bastion, sans
dépendre d'un reverse proxy externe (voir la section TLS du README) — pour
les déploiements qui n'en ont pas.

Utilisé de façon identique par les deux serveurs réseau du projet (app.py,
le websockify du VNC via start_websockify.py) : n'activer le chiffrement
que sur l'un d'eux recréerait exactement le bug de contenu mixte déjà
rencontré avec un reverse proxy mal configuré (page en https, WebSocket
VNC resté en clair, bloqué par le navigateur).
"""
import datetime
import os

import config

TLS_DIR = os.path.join(config.DATA_DIR, "tls")
SELFSIGNED_CERT = os.path.join(TLS_DIR, "bastion.crt")
SELFSIGNED_KEY = os.path.join(TLS_DIR, "bastion.key")


def generate_self_signed(cert_path=SELFSIGNED_CERT, key_path=SELFSIGNED_KEY):
    """Génère une paire clé privée/certificat auto-signé si les fichiers
    n'existent pas déjà. Ne jamais régénérer si les deux fichiers sont déjà
    présents: une nouvelle paire à chaque démarrage changerait l'empreinte
    à chaque fois, provoquant un avertissement de sécurité navigateur (et
    un rejet TOFU côté clients VNC natifs) à répétition pour rien.
    """
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return

    # Imports différés: seule cette fonction (donc uniquement si l'auto-
    # génération est effectivement utilisée) a besoin de `cryptography`.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    os.makedirs(os.path.dirname(cert_path), exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bastion.local")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("bastion.local")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def resolve_cert_paths():
    """Retourne (cert_path, key_path) à utiliser pour servir en TLS, ou
    (None, None) si le TLS n'est pas activé (comportement historique,
    HTTP/WS en clair — reste le défaut).

    Priorité: BASTION_TLS_CERT/_KEY fournis explicitement (certificat déjà
    existant, auto-signé ou non) > génération automatique si
    BASTION_TLS_SELFSIGNED est activé > rien.
    """
    if config.TLS_CERT and config.TLS_KEY:
        return config.TLS_CERT, config.TLS_KEY
    if config.TLS_SELFSIGNED:
        generate_self_signed(SELFSIGNED_CERT, SELFSIGNED_KEY)
        return SELFSIGNED_CERT, SELFSIGNED_KEY
    return None, None
