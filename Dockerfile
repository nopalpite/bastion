FROM python:3.12-slim

# Dépendances système: git pour récupérer noVNC, build-essential (gcc +
# libc6-dev + make) et libffi-dev/libssl-dev/cargo+rustc pour compiler
# cffi/cryptography depuis les sources si aucune wheel précompilée ne
# correspond à l'architecture de build (ex: ARM64 / Apple Silicon /
# Raspberry Pi), supervisor pour lancer l'appli + websockify dans le
# même conteneur, iputils-ping pour le monitoring ICMP
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential pkg-config libffi-dev libssl-dev \
        cargo rustc supervisor iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# piwheels fournit des wheels précompilées pour Raspberry Pi, surtout
# utiles en 32 bits (armv7) où cryptography n'a pas de wheel officielle
# sur PyPI. En 64 bits (aarch64), PyPI a normalement déjà des wheels
# précompilées adaptées — cette ligne ne sert alors que de filet de
# sécurité, sans effet si aucune wheel piwheels ne correspond.
#
# cryptography/bcrypt/pynacl ont une extension native (Rust ou C) et
# peuvent, faute de wheel correspondant à l'architecture/version de
# Python, se rabattre sur une compilation depuis les sources — laquelle
# peut prendre des dizaines de minutes sur un Raspberry Pi, voire plus,
# sans qu'on sache ce qui bloque. On les installe donc d'abord à part
# avec --only-binary: ça échoue en quelques secondes avec un message
# clair ("no matching distribution") si aucune wheel ne convient, plutôt
# que de rester bloqué sans retour. Si ça échoue, voir la section
# Raspberry Pi du README pour les options (piwheels, build sur une autre
# machine, etc).
RUN pip install --no-cache-dir --only-binary=cryptography,bcrypt,pynacl \
        --extra-index-url https://www.piwheels.org/simple \
        "cryptography>=42.0.5,<44.0.0" "bcrypt>=4.1.2,<5.0.0" "PyNaCl>=1.5.0,<2.0.0"

# CARGO_BUILD_JOBS=1: filet de sécurité si un autre paquet devait quand
# même compiler une extension Rust — limite le parallélisme pour réduire
# le risque d'OOM sur un Pi avec peu de RAM (au prix d'un build plus lent).
ENV CARGO_BUILD_JOBS=1
RUN pip install --no-cache-dir \
        --extra-index-url https://www.piwheels.org/simple \
        -r requirements.txt websockify

# Récupération de noVNC (pas un paquet pip). On garde core/ et vendor/
# comme dossiers frères sous static/novnc/, car les décodeurs de noVNC
# (ex: decoders/zrle.js) référencent vendor/pako/ via un chemin relatif
# "../vendor/..." depuis core/ — les aplatir ensemble casse ces imports.
RUN git clone --depth 1 https://github.com/novnc/noVNC.git /tmp/noVNC \
    && mkdir -p static/novnc \
    && cp -r /tmp/noVNC/core static/novnc/core \
    && cp -r /tmp/noVNC/vendor static/novnc/vendor \
    && rm -rf /tmp/noVNC

COPY . .

# Génère le fichier de tokens websockify au démarrage (voir entrypoint.sh)
# pour prendre en compte machines.yaml à chaque lancement du conteneur.
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/supervisord.conf /etc/supervisor/conf.d/bastion.conf
RUN chmod +x /entrypoint.sh

EXPOSE 5000 6080

ENTRYPOINT ["/entrypoint.sh"]
