#!/bin/sh
set -e

# Régénère le fichier de tokens websockify à partir de machines.yaml
# à chaque démarrage du conteneur, pour toujours refléter l'inventaire
# monté (voir docker-compose.yml).
python gen_vnc_tokens.py > /app/vnc_tokens.conf

exec supervisord -c /etc/supervisor/conf.d/bastion.conf
