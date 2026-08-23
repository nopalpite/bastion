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
WEBSOCKIFY_PORT = int(os.environ.get("BASTION_WEBSOCKIFY_PORT", "6080"))

# Optionnel: si vous êtes derrière un reverse proxy (TLS/certificat) et
# ne voulez exposer que son port (443) plutôt qu'un port brut supplémentaire
# pour websockify, définissez un chemin ici (ex: "/vnc-ws/") que votre
# reverse proxy fait suivre vers 127.0.0.1:6080. Le client utilisera alors
# ce chemin sur le même host:port que la page plutôt que WEBSOCKIFY_PORT
# directement. Voir la section "Derrière un reverse proxy (TLS)" du README.
WEBSOCKIFY_PATH = os.environ.get("BASTION_WEBSOCKIFY_PATH", "").strip()

# Port d'écoute de rdp_bridge.py (RDP via guacd) — service séparé de
# websockify, pas le même sous-protocole WebSocket (voir le docstring de
# rdp_bridge.py). Même logique de reverse-proxy que WEBSOCKIFY_PATH
# ci-dessus si RDP_WS_PATH est définie.
RDP_WS_PORT = int(os.environ.get("BASTION_RDP_WS_PORT", "6081"))
RDP_WS_PATH = os.environ.get("BASTION_RDP_WS_PATH", "").strip()
