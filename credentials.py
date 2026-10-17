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


# Le fichier est relu seulement s'il a changé (has_stored_credentials est appelé une fois par
# machine sur certaines pages) ; un fichier absent, illisible ou vide est signalé UNE fois.
_default_key_cache = {}
_default_key_warned = set()


def warn_default_key_once(path, reason):
    if (path, reason) not in _default_key_warned:
        _default_key_warned.add((path, reason))
        print(f"[credentials] Clé SSH par défaut {path} ignorée : {reason}")


def default_ssh_key_text():
    """Contenu de la clé privée par défaut (relu quand le fichier change, sans redémarrer).
    None si non configurée, absente, illisible ou vide -- ces trois derniers cas sont
    signalés dans les journaux."""
    path = default_ssh_key_file()
    if not path:
        return None
    try:
        stat = os.stat(path)
        cached = _default_key_cache.get(path)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2]
        text = Path(path).read_text()
    except OSError as exc:
        warn_default_key_once(path, f"fichier illisible ({exc.strerror or exc})")
        return None
    if not text.strip():
        warn_default_key_once(path, "fichier vide")
        text = None
    _default_key_cache[path] = (stat.st_mtime_ns, stat.st_size, text)
    return text


def default_ssh_username():
    return os.environ.get("BASTION_DEFAULT_SSH_USER") or None


# --- Identifiants VNC PAR DÉFAUT ---------------------------------------------------------
# Même idée que la clé SSH par défaut ci-dessus, pour un parc dont les machines partagent le
# même compte (le compte système sert aussi à l'authentification VNC, ex. RealVNC Server).
#   BASTION_DEFAULT_VNC_PASSWORD : mot de passe VNC utilisé quand une machine n'en a pas un
#                                  propre (le compte reprend le compte SSH, voir vnc_login)


def default_vnc_password():
    return os.environ.get("BASTION_DEFAULT_VNC_PASSWORD") or None


def vnc_login(machine):
    """(utilisateur, mot de passe) VNC à utiliser pour `machine` -- chacun peut être None.
    Un seul endroit, partagé par la page VNC (app.py) et le pont TLS (vnc_tls_bridge.py).

    Mot de passe, du plus au moins spécifique :
      1. le mot de passe VNC propre à la machine ;
      2. BASTION_DEFAULT_VNC_PASSWORD (défaut du serveur) ;
      3. à défaut, le mot de passe SSH mémorisé de la machine (« même que SSH »).
    Utilisateur : celui propre au VNC de la machine, sinon son compte SSH mémorisé, sinon
    BASTION_DEFAULT_SSH_USER. Un serveur VNC qui ne demande pas d'utilisateur l'ignore."""
    stored = machine.get("credentials") or {}

    password = decrypt(machine["vnc_password"]) if machine.get("vnc_password") else None
    if not password:
        password = default_vnc_password()
    if not password and stored.get("password"):
        password = decrypt(stored["password"])

    username = machine.get("vnc_username") or stored.get("username") or default_ssh_username()
    return username, password
