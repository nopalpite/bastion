"""Génère le fichier de tokens pour websockify (VNC uniquement — le RDP ne
passe pas par websockify, voir plus bas).

Websockify, lancé en mode multiplexé avec un plugin TokenFile, accepte une
connexion websocket du type ws://host:6080/?token=<token> et la redirige
vers l'adresse correspondante.

Chaque machine avec un port VNC configuré est routée vers son pont local
(vnc_tls_bridge.py, sur 127.0.0.1:<vnc_bridge_port>) plutôt que directement
vers la machine cible — le pont sonde lui-même ce que le serveur propose à
chaque connexion et s'adapte (relais transparent pour un serveur VNC
"classique", négociation VeNCrypt/TLS complète sinon), façon vncviewer.
Pas besoin de savoir à l'avance quel type de sécurité une machine utilise.

Le RDP (rdp_bridge.py, via guacd) n'apparaît PAS ici : contrairement au VNC,
son navigateur (guacamole-common-js) ouvre son WebSocket avec le
sous-protocole "guacamole", que websockify ne connaît pas et ne relaierait
pas — rdp_bridge.py sert donc lui-même ses connexions WebSocket, sur son
propre port (BASTION_RDP_WS_PORT, voir config.py), et route chaque connexion
vers la bonne machine via le paramètre "?token=<id_machine>" de l'URL, sans
fichier de tokens séparé. Voir le docstring de rdp_bridge.py.

Ce script régénère ce fichier à partir de machines.yaml. Relancez-le
chaque fois que vous modifiez l'inventaire (le conteneur Docker le fait
automatiquement à chaque démarrage, voir docker/entrypoint.sh).

Usage:
    python gen_vnc_tokens.py > vnc_tokens.conf
    websockify --token-plugin=websockify.token_plugins.TokenFile --token-source=vnc_tokens.conf 6080
"""
from store import load_machines

for machine in load_machines():
    if machine.get("vnc_port"):
        bridge_port = machine.get("vnc_bridge_port")
        if bridge_port:
            print(f"{machine['id']}: 127.0.0.1:{bridge_port}")
