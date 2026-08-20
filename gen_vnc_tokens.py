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
    if not vnc_port:
        continue
    if machine.get("vnc_tls"):
        # Serveur chiffré VeNCrypt/TLS: noVNC ne peut pas lui parler
        # directement (voir vnc_tls_bridge.py). On route vers le pont
        # local plutôt que vers la machine cible - c'est lui qui fait la
        # vraie connexion chiffrée de l'autre côté.
        local_port = machine.get("vnc_tls_local_port")
        if local_port:
            print(f"{machine['id']}: 127.0.0.1:{local_port}")
        continue
    print(f"{machine['id']}: {machine['host']}:{vnc_port}")
