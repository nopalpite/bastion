"""Génère le fichier de tokens pour websockify.

Websockify, lancé en mode multiplexé avec un plugin TokenFile, accepte une
connexion websocket du type ws://host:6080/?token=<id_machine> et la
redirige vers le host:port VNC réel correspondant, sans avoir à lancer un
process websockify par machine.

Ce script régénère ce fichier à partir de machines.yaml. Relancez-le
chaque fois que vous modifiez l'inventaire (le conteneur Docker le fait
automatiquement à chaque démarrage, voir docker/entrypoint.sh).

Usage:
    python gen_vnc_tokens.py > vnc_tokens.conf
    websockify --token-plugin=websockify.token_plugins.TokenFile --token-source=vnc_tokens.conf 6080
"""
from store import load_machines

for machine in load_machines():
    vnc_port = machine.get("vnc_port")
    if vnc_port:
        print(f"{machine['id']}: {machine['host']}:{vnc_port}")
