"""Génère le fichier de tokens pour websockify (VNC).

Websockify, lancé en mode multiplexé avec un plugin TokenFile, accepte une
connexion websocket du type ws://host:6080/?token=<token> et la redirige
vers l'adresse correspondante.

Chaque machine avec un port VNC configuré est routée vers son pont local
(vnc_tls_bridge.py, sur 127.0.0.1:<vnc_bridge_port>) plutôt que directement
vers la machine cible — le pont sonde lui-même ce que le serveur propose à
chaque connexion et s'adapte (relais transparent pour un serveur VNC
"classique", négociation VeNCrypt/TLS complète sinon), façon vncviewer.
Pas besoin de savoir à l'avance quel type de sécurité une machine utilise.

Ce fichier est régénéré à partir de machines.yaml (a) au démarrage du
conteneur Docker (voir docker/entrypoint.sh) et (b) automatiquement par
store.py à chaque ajout/modification/suppression de machine (voir
store._regenerate_vnc_tokens) — websockify relit ce fichier à chaque
connexion sans le mettre en cache (websockify.token_plugins.TokenFile),
donc le régénérer suffit pour qu'un changement prenne effet, aucun
redémarrage n'est nécessaire.

Usage en ligne de commande (régénération manuelle, ex. après une
modification directe de machines.yaml sans passer par l'interface):
    python gen_vnc_tokens.py > vnc_tokens.conf
    websockify --token-plugin=websockify.token_plugins.TokenFile --token-source=vnc_tokens.conf 6080
"""
import os

import config
from store import load_machines

TOKENS_FILE = os.path.join(config.BASE_DIR, "vnc_tokens.conf")


def render_tokens():
    """Construit le contenu du fichier de tokens à partir de l'inventaire
    actuel (une ligne par machine avec un port VNC ET un pont local
    assigné — voir vnc_tls_bridge.py)."""
    lines = [
        f"{machine['id']}: 127.0.0.1:{machine['vnc_bridge_port']}"
        for machine in load_machines()
        if machine.get("vnc_port") and machine.get("vnc_bridge_port")
    ]
    return "".join(f"{line}\n" for line in lines)


def write_tokens(path=None):
    """Régénère le fichier de tokens sur disque (TOKENS_FILE par défaut,
    le même chemin que websockify surveille — voir docker/supervisord.conf)."""
    with open(path or TOKENS_FILE, "w", encoding="utf-8") as f:
        f.write(render_tokens())


if __name__ == "__main__":
    print(render_tokens(), end="")
