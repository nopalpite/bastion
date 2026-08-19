"""Chiffrement des identifiants mémorisés.

Si BASTION_CREDENTIALS_KEY n'est pas définie, la mémorisation des mots de
passe est simplement désactivée (encrypt() renvoie None) plutôt que de
stocker quoi que ce soit en clair par erreur.

Générer une clé:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import os

from cryptography.fernet import Fernet, InvalidToken


def credentials_enabled():
    return bool(os.environ.get("BASTION_CREDENTIALS_KEY"))


def _get_fernet():
    key = os.environ.get("BASTION_CREDENTIALS_KEY")
    if not key:
        return None
    return Fernet(key.encode())


def encrypt(plain_text):
    """Chiffre une chaîne. Renvoie None si aucune clé n'est configurée
    ou si l'entrée est vide (rien à stocker)."""
    if not plain_text:
        return None
    fernet = _get_fernet()
    if not fernet:
        return None
    return fernet.encrypt(plain_text.encode()).decode()


def decrypt(token):
    """Déchiffre une valeur stockée. Renvoie None si pas de clé configurée,
    pas de token, ou token invalide (ex: clé changée entre-temps)."""
    if not token:
        return None
    fernet = _get_fernet()
    if not fernet:
        return None
    try:
        return fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        return None
