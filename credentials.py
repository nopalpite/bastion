"""Chiffrement des identifiants mémorisés.

Si BASTION_CREDENTIALS_KEY n'est pas définie, la mémorisation des mots de
passe est simplement désactivée (encrypt() renvoie None) plutôt que de
stocker quoi que ce soit en clair par erreur.

Générer une clé:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import os
from pathlib import Path

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


# --- Identifiants SSH PAR DÉFAUT ---------------------------------------------------------
# Pour un parc dont toutes les machines acceptent la même paire de clés (ex. une image
# unique flashée partout), poser la clé privée dans chaque formulaire d'hôte serait
# fastidieux et la ferait transiter par le navigateur. À la place, l'opérateur monte le
# fichier de la clé sur le serveur (lecture seule) et le désigne par variable
# d'environnement ; elle n'est JAMAIS copiée dans machines.yaml. Les identifiants propres
# à une machine restent prioritaires.
#   BASTION_DEFAULT_SSH_KEY_FILE : chemin de la clé privée (sans passphrase)
#   BASTION_DEFAULT_SSH_USER     : compte utilisé quand une machine n'en mémorise pas


def default_ssh_key_file():
    """Chemin de la clé privée par défaut, ou None si elle n'est pas configurée."""
    return os.environ.get("BASTION_DEFAULT_SSH_KEY_FILE") or None


def default_ssh_key_text():
    """Contenu de la clé privée par défaut, relu à chaque appel (le fichier peut être
    remplacé sans redémarrer). None si non configurée ou illisible."""
    path = default_ssh_key_file()
    if not path:
        return None
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    return text if text.strip() else None


def default_ssh_username():
    return os.environ.get("BASTION_DEFAULT_SSH_USER") or None
