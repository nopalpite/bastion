"""Génère le fichier de tokens pour websockify.

Websockify, lancé en mode multiplexé avec un plugin TokenFile, accepte une
connexion websocket du type ws://host:6080/?token=<id_machine> et la
redirige vers l'adresse correspondante, sans avoir à lancer un process
websockify par machine.

Chaque machine avec un port VNC configuré est routée vers son pont local
(vnc_tls_bridge.py, sur 127.0.0.1:<vnc_bridge_port>) plutôt que directement
vers la machine cible — le pont sonde lui-même ce que le serveur propose à
chaque connexion et s'adapte (relais transparent pour un serveur VNC
"classique", négociation VeNCrypt/TLS complète sinon), façon vncviewer.
Pas besoin de savoir à l'avance quel type de sécurité une machine utilise.

Ce script régénère ce fichier à partir de machines.yaml. Relancez-le
chaque fois que vous modifiez l'inventaire (le conteneur Docker le fait
automatiquement à chaque démarrage, voir docker/entrypoint.sh).

Usage:
    python gen_vnc_tokens.py > vnc_tokens.conf
    websockify --token-plugin=websockify.token_plugins.TokenFile --token-source=vnc_tokens.conf 6080
"""
from store import load_machines

for machine in load_machines():
    if not machine.get("vnc_port"):
        continue
    bridge_port = machine.get("vnc_bridge_port")
    if bridge_port:
        print(f"{machine['id']}: 127.0.0.1:{bridge_port}")
