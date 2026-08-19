#!/bin/sh
set -e

# 1er démarrage avec un volume vide monté sur BASTION_DATA_DIR (voir
# config.py, Dockerfile): pas de machines.yaml encore présent dedans —
# on part de l'inventaire d'exemple livré dans l'image (/app/machines.yaml)
# plutôt que de planter au premier accès. Sans effet ensuite (le fichier
# copié persiste dans le volume, ne sera jamais réécrit ici).
if [ -n "$BASTION_DATA_DIR" ] && [ ! -f "$BASTION_DATA_DIR/machines.yaml" ]; then
    mkdir -p "$BASTION_DATA_DIR"
    cp /app/machines.yaml "$BASTION_DATA_DIR/machines.yaml"
fi

# Régénère le fichier de tokens websockify à partir de machines.yaml
# à chaque démarrage du conteneur, pour toujours refléter l'inventaire
# monté (voir docker-compose.yml).
python gen_vnc_tokens.py > /app/vnc_tokens.conf

exec supervisord -c /etc/supervisor/conf.d/bastion.conf
