"""Constantes de configuration de l'app (secrets, ports, chemins).

Le chargement/écriture de l'inventaire (machines + salles) vit dans
store.py, pas ici.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Dossier contenant machines.yaml. Par défaut BASE_DIR (comportement
# historique du dev local: python app.py). L'image Docker surcharge cette
# variable pour pointer vers un dossier monté en volume (voir Dockerfile /
# docker-compose.yml) — monter un DOSSIER plutôt que le fichier
# machines.yaml directement évite un piège Docker classique: si le fichier
# n'existe pas encore côté hôte au premier démarrage, Docker crée un
# dossier à sa place au lieu d'un fichier, ce qui casse le montage
# ("not a directory").
DATA_DIR = os.environ.get("BASTION_DATA_DIR", BASE_DIR)
MACHINES_FILE = os.path.join(DATA_DIR, "machines.yaml")

# Clé secrète de l'app (à surcharger via variable d'env en prod)
SECRET_KEY = os.environ.get("BASTION_SECRET_KEY", "change-moi-en-production")

# Identifiants de connexion à l'interface (à remplacer par un vrai
# système d'auth / LDAP / SSO en prod, ceci est un minimum fonctionnel)
ADMIN_USER = os.environ.get("BASTION_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("BASTION_ADMIN_PASSWORD", "admin")

# Port d'écoute du proxy websockify pour le VNC. L'hôte n'est pas
# configurable ici: le navigateur utilise directement l'hôte avec lequel
# il a accédé à la page (window.location côté client), voir
# templates/vnc.html.
#
# Nommée VNC_WS_* plutôt que WEBSOCKIFY_* : le nom de l'outil qui sert le
# VNC (websockify) est un détail d'implémentation, pas quelque chose que
# l'utilisateur de cette variable a besoin de connaître — seul le
# protocole (VNC) compte pour savoir laquelle configurer. Les anciens noms
# BASTION_WEBSOCKIFY_PORT/_PATH ne sont plus lus : mettez à jour votre
# configuration si vous les utilisiez.
VNC_WS_PORT = int(os.environ.get("BASTION_VNC_WS_PORT", "6080"))

# Optionnel: si vous êtes derrière un reverse proxy (TLS/certificat) et
# ne voulez exposer que son port (443) plutôt qu'un port brut supplémentaire
# pour websockify, définissez un chemin ici (ex: "/vnc-ws/") que votre
# reverse proxy fait suivre vers 127.0.0.1:6080. Le client utilisera alors
# ce chemin sur le même host:port que la page plutôt que VNC_WS_PORT
# directement. Voir la section "Derrière un reverse proxy (TLS)" du README.
VNC_WS_PATH = os.environ.get("BASTION_VNC_WS_PATH", "").strip()

# TLS optionnel pour servir HTTPS/WSS directement (sans reverse proxy) sur
# les deux serveurs réseau du projet (app.py, websockify) — voir tls.py et
# la section TLS du README. BASTION_TLS_CERT/_KEY: chemin vers un
# certificat déjà existant. BASTION_TLS_SELFSIGNED: si "true" et qu'aucun
# des deux ci-dessus n'est fourni, Bastion génère et gère seul un
# certificat auto-signé. Aucun des deux définis: HTTP en clair (défaut,
# comportement historique inchangé).
TLS_CERT = os.environ.get("BASTION_TLS_CERT", "").strip()
TLS_KEY = os.environ.get("BASTION_TLS_KEY", "").strip()
TLS_SELFSIGNED = os.environ.get("BASTION_TLS_SELFSIGNED", "").strip().lower() in (
    "1", "true", "yes",
)
