# Bastion — dashboard + SSH/VNC web

Interface simple pour superviser un parc de machines Windows/Linux et s'y
connecter en SSH ou VNC directement depuis le navigateur.

## Stack

- **Flask** + **Flask-SocketIO** : app web + canal temps réel (statuts, terminal SSH, SFTP)
- **Paramiko** : connexion SSH côté serveur, relayée vers un terminal `xterm.js`
- **noVNC** + **websockify** : accès VNC dans le navigateur
- **YAML** (`machines.yaml`) : inventaire des machines, pas de base de données

Choix volontaire : un seul process Python (l'appli + websockify dans le
même conteneur, via `supervisord`), un seul fichier de config, aucune
dépendance lourde. Facile à lire, facile à étendre.

## Fonctionnalités

- **Dashboard de monitoring** : statut ping (vivant/injoignable) de chaque machine, plus un badge SSH indiquant si le port répond — groupées par salle.
- **Ajout d'hôtes et de salles depuis l'interface** (`+ Hôte` / `+ Salle`), en plus de l'édition directe de `machines.yaml`. Édition et suppression possibles ensuite.
- **Plan interactif par salle** (`/map/<salle>`) : importez une image de plan, placez les machines dessus par glisser-déposer (souris et tactile), cliquez sur une machine pour ouvrir un accès rapide SSH/VNC ou déclencher un reboot/shutdown.
- **Terminal SSH** dans le navigateur, avec navigateur de fichiers SFTP dans une colonne latérale (façon MobaXterm).
- **VNC** dans le navigateur via noVNC — voir la limite connue ci-dessous pour les serveurs VNC chiffrés.
- **Épinglage de la clé d'hôte SSH (TOFU)** : la première connexion à une machine mémorise sa clé publique ; si elle change ensuite, la connexion est bloquée avec une alerte explicite.
- **Identifiants mémorisés (optionnel)** : si vous configurez `BASTION_CREDENTIALS_KEY`, vous pouvez enregistrer les identifiants SSH et/ou VNC d'une machine, chiffrés. Sans cette clé, la mémorisation est simplement désactivée (rien n'est stocké en clair par erreur). Sans identifiants mémorisés, noVNC les demande en interactif à la connexion.

## ⚠️ Limite connue : VNC chiffré (VeNCrypt / RealVNC)

**À propos des identifiants VNC mémorisés** : contrairement à SSH (où
Paramiko s'authentifie côté serveur, le mot de passe ne quitte jamais le
backend), l'authentification VNC se fait **côté navigateur** (noVNC est
une lib JS). Un mot de passe VNC mémorisé est donc déchiffré côté
serveur puis transmis dans la page au moment où vous ouvrez la
connexion — visible dans le code source de cette page pour l'utilisateur
qui la consulte (ce qui est attendu, puisque c'est un utilisateur déjà
authentifié sur le bastion). Ne mémorisez pas de mot de passe VNC si
plusieurs personnes non habilitées à le connaître partagent l'accès à
l'interface du bastion.



noVNC (réimplémentation du protocole RFB en JavaScript pur, exécutée
dans le navigateur) ne sait parler que les types de sécurité RFB
"classiques" : aucune authentification, ou mot de passe VNC standard.
Il ne sait **pas** négocier VeNCrypt/TLS (types de sécurité X509*), qui
bascule en chiffrement TLS *au milieu* de la connexion TCP (façon
STARTTLS) — ce n'est pas un réglage manquant, c'est un morceau de
protocole que noVNC n'implémente pas.

**Symptôme** : `Failed when connecting: Unsupported security types
(types: XXX)` dans la console du navigateur.

C'est le comportement par défaut de **RealVNC Server** (préinstallé sur
Raspberry Pi OS) sur certaines configurations. Une piste explorée pour
contourner ça sans toucher au chiffrement de la machine cible était de
passer par [Apache Guacamole](https://guacamole.apache.org/) (`guacd`),
qui sait nativement parler VeNCrypt — mais l'intégration s'est révélée
trop instable à mettre au point (bugs difficiles à isoler sans pouvoir
tester en conditions réelles) et a été abandonnée dans cette version.
Options qui restent, si vous êtes bloqué par cette limite :
- désactiver le chiffrement RFB natif côté machine cible
  (`Encryption=AlwaysOff` dans la config RealVNC Server) — acceptable
  sur un LAN de confiance, à éviter sur une machine exposée ;
- utiliser un serveur VNC qui ne force pas VeNCrypt (TigerVNC, x11vnc) ;
- se connecter à cette machine précise avec un vrai client VNC en
  dehors du bastion, pour les cas ponctuels.

`debug_vnc_security.py` (à la racine du projet) est un petit script de
diagnostic qui affiche, sans dépendre de rien d'autre, la liste exacte
des types de sécurité qu'un serveur VNC propose — utile pour savoir
rapidement si une machine donnée est concernée par cette limite :
```bash
docker exec bastion python3 debug_vnc_security.py <host> <port>
```

## Structure

```
bastion/
  app.py               routes Flask + auth + démarrage
  config.py             constantes (secrets, ports)
  store.py              lecture/écriture de machines.yaml (salles, machines, positions, identifiants)
  credentials.py         chiffrement/déchiffrement des identifiants SSH mémorisés
  monitor.py             thread de fond: ping + test de port SSH
  ssh_ws.py               pont Socket.IO <-> Paramiko pour le terminal
  ssh_client.py            connexion SSH avec épinglage de la clé d'hôte (TOFU)
  sftp_ws.py               navigateur de fichiers (colonne latérale du terminal)
  ssh_actions.py           actions ponctuelles (reboot/shutdown) via SSH
  gen_vnc_tokens.py        génère le fichier de tokens pour websockify
  debug_vnc_security.py    diagnostic RFB autonome (types de sécurité VNC)
  machines.yaml           inventaire: salles + machines
  templates/               pages Jinja2 (dashboard, plan, formulaires, terminal, vnc)
  static/css/            style.css (thème console sombre)
  static/js/              dashboard.js, terminal.js, sftp.js, map.js, actions.js
  static/uploads/maps/    images de plan uploadées
  static/novnc/           noVNC vendoré au build (voir Dockerfile)
```

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### noVNC (pour l'accès VNC)

noVNC n'est pas distribué via pip :

```bash
git clone https://github.com/novnc/noVNC.git /tmp/noVNC
mkdir -p static/novnc
cp -r /tmp/noVNC/core static/novnc/core
cp -r /tmp/noVNC/vendor static/novnc/vendor
```

`core/` et `vendor/` doivent rester deux dossiers **frères** sous
`static/novnc/` (pas aplatis ensemble) : les décodeurs de noVNC
(`core/decoders/zrle.js`, etc.) référencent `vendor/pako/` via un chemin
relatif `../vendor/...`, et cassent silencieusement si cette structure
n'est pas respectée (erreurs 404 sur `pako` dans les logs, VNC qui ne
s'affiche pas).

### websockify

```bash
pip install websockify
python gen_vnc_tokens.py > vnc_tokens.conf
websockify --token-plugin=websockify.token_plugins.TokenFile --token-source=vnc_tokens.conf 6080 &
```

Ce proxy unique redirige chaque session vers la bonne machine grâce au
token dans l'URL (`?token=srv-linux-01` par exemple). Relancez
`gen_vnc_tokens.py` (et relisez le fichier, ou relancez websockify) après
toute modification de `machines.yaml`.

**Important** : la page VNC construit l'URL du websocket avec l'hôte que
le navigateur a réellement utilisé pour charger la page
(`window.location.hostname`), pas une valeur fixée côté serveur.

**Diagnostic** : si le VNC ne se connecte pas (`Connection closed (code:
1006)` côté navigateur), vérifiez d'abord que `websockify` tourne
vraiment dans le conteneur plutôt que d'avoir crashé au démarrage :
```bash
docker exec bastion supervisorctl status
```
Si `websockify` est en `FATAL` ou en boucle de redémarrage, regardez
`docker logs bastion` pour voir l'erreur exacte.

## Monitoring

Chaque machine est pingée toutes les 15 secondes (`monitor.py`), via la
commande système `ping` (pas de socket ICMP brut en Python — évite les
soucis de droits root/`CAP_NET_RAW`). En plus du ping, le port **SSH**
est testé séparément par ouverture de port TCP, affiché sous forme de
badge.

- 🟢 **up** (pastille) — répond au ping
- 🔴 **down** (pastille) — ne répond pas
- badge **SSH** — vert si le port répond, rouge sinon (indépendant du ping)

**⚠️ VNC et RDP ne sont volontairement PAS sondés en continu.** Une
version précédente testait aussi ces ports toutes les 15 secondes de la
même façon (ouverture TCP puis fermeture immédiate, sans authentification).
En usage réel, ça a déclenché la protection anti-bruteforce de RealVNC
Server (`TooManySecFail`), qui a blacklisté l'IP du bastion — y compris
pour de vraies tentatives de connexion légitimes juste après, en boucle
perpétuelle puisque le monitoring re-déclenchait le blacklist toutes les
15 secondes avant même qu'il ait pu retomber. Si vous voulez réactiver
un test de port VNC malgré ce risque documenté, sachez que ça peut
rendre le VNC totalement inutilisable sur les serveurs avec ce genre de
protection.

**Docker** : le ping ICMP a besoin de la capacité `NET_RAW`, qui fait
partie de l'ensemble de capacités par défaut de Docker — rien à
configurer normalement.

## Configuration

Éditez `machines.yaml` pour déclarer vos machines, ou passez par
l'interface (`+ Hôte`). Structure d'une machine :

```yaml
machines:
  - id: srv-linux-01
    name: "Serveur Web Linux"
    os: linux            # linux | windows
    host: 192.168.1.10
    ssh_port: 22
    vnc_port: 5901        # optionnel
    room: salle-a         # optionnel
    position: {x: 30, y: 45}   # optionnel, % du plan de la salle
    # credentials:                 # optionnel, chiffré
    #   username: root
    #   password: "gAAAAA...=="
```

Variables d'environnement utiles :

| Variable | Rôle | Défaut |
|---|---|---|
| `BASTION_SECRET_KEY` | clé de session Flask | à changer en prod |
| `BASTION_ADMIN_USER` / `BASTION_ADMIN_PASSWORD` | identifiants de connexion à l'interface | `admin` / `admin` |
| `BASTION_WEBSOCKIFY_PORT` | port où joindre le proxy VNC (l'hôte est déterminé automatiquement par le navigateur) | `6080` |
| `BASTION_WEBSOCKIFY_PATH` | si définie (ex: `/vnc-ws/`), route le VNC via ce chemin sur le même host:port que la page plutôt que le port direct — utile derrière un reverse proxy TLS | (vide, mode direct) |
| `BASTION_CREDENTIALS_KEY` | clé de chiffrement des identifiants SSH mémorisés. Sans elle, la mémorisation est désactivée. Générer avec `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | (aucune) |

## Lancement

```bash
python app.py
```

Puis ouvrez `http://localhost:5000`.

## Lancement avec Docker

```bash
docker compose up --build -d
```

Le conteneur tourne en `network_mode: host` : il partage directement les
interfaces et routes réseau du host — le choix le plus simple pour un
bastion, qui a besoin de joindre les mêmes réseaux/VLAN que la machine
sur laquelle il tourne.

**Pare-feu** : si le host a des règles iptables de filtrage (fréquent sur
un bastion), pensez à les mettre dans la chaîne `DOCKER-USER` plutôt que
`INPUT`/`FORWARD` — Docker peut sinon les contourner pour le trafic à
destination de ses conteneurs.

`machines.yaml` est monté en volume (lecture-écriture, nécessaire
puisque l'appli y écrit quand vous ajoutez un hôte/salle depuis
l'interface) : vous pouvez aussi le modifier à la main sans reconstruire
l'image, il suffit de redémarrer le conteneur (`docker compose restart`)
pour que websockify régénère ses tokens.

### Build sur ARM (Apple Silicon, Raspberry Pi...)

Le Dockerfile installe une toolchain complète (`build-essential`,
OpenSSL dev, Rust) pour compiler `cryptography`/`bcrypt`/`pynacl` depuis
les sources si aucune wheel précompilée n'est trouvée pour votre
architecture/version de Python — ces trois paquets s'installent d'abord
séparément avec `--only-binary`, pour échouer vite et clairement plutôt
que de rester bloqué sans retour. `requirements.txt` les laisse aussi
sur une plage de versions plutôt qu'un pin exact, pour laisser pip
choisir une version qui a une wheel disponible pour votre architecture.

Sur Raspberry Pi, si la compilation Python est malgré tout nécessaire :
- **c'est normal que ce soit long** (20-40 minutes possibles) ;
- **si le build plante ou reste bloqué**, c'est souvent un manque de RAM
  — augmentez le swap (`sudo dphys-swapfile swapoff`, monter
  `CONF_SWAPSIZE` dans `/etc/dphys-swapfile`, puis
  `sudo dphys-swapfile setup && sudo dphys-swapfile swapon`) ;
- alternative : construire l'image sur une machine plus puissante avec
  `docker buildx build --platform linux/arm64 -t bastion .`, puis
  transférer l'image sur le Pi (`docker save`/`docker load`, ou via un
  registre).

## Navigateur de fichiers SFTP

La colonne à gauche du terminal SSH (`/terminal/<machine>`) utilise SFTP
sur **la même connexion** que le terminal — pas de nouvelle
authentification, et elle n'est disponible que le temps où la session
SSH est ouverte (fermer/rafraîchir l'onglet la referme aussi).

Fonctions disponibles : navigation (double-clic sur un dossier, `↑` pour
remonter), création de dossier, suppression (fichier ou dossier vide),
téléchargement, et envoi de fichiers par glisser-déposer ou sélection.
La largeur de la colonne se redimensionne par glisser-déposer sur la
barre entre elle et le terminal (mémorisée dans le navigateur d'une
session à l'autre).

### Édition de fichier en ligne

Cliquer sur le **nom** d'un fichier (pas l'icône télécharger) ouvre son
contenu dans un éditeur en grand modal, avec coloration syntaxique
(CodeMirror 5, chargé depuis un CDN comme `xterm.js` — voir
`templates/terminal.html`). Le langage est détecté depuis l'extension du
fichier ; sans correspondance reconnue, le fichier reste affiché en texte
brut, sans erreur. `Enregistrer` réécrit le fichier sur la machine cible
via SFTP.

Limité aux fichiers texte **UTF-8** de moins de **5 Mo** (`MAX_EDIT_BYTES`
dans `sftp_ws.py`) : un fichier binaire ou trop volumineux affiche un
message clair plutôt que du charabia dans l'éditeur — téléchargez-le
dans ce cas plutôt que de l'éditer ici.

### Suivi du répertoire courant (bouton "Suivre")

Une fois activé (bouton ⚓ en haut de la colonne), le navigateur de
fichiers navigue automatiquement vers le répertoire courant du shell à
chaque changement, jusqu'à ce que vous le désactiviez.

**Fonctionne par défaut, sans rien configurer**, pour un prompt shell
standard (`user@host:/chemin$ `, le défaut sur Debian/Ubuntu et la
plupart des distros) — `terminal.js` lit simplement le texte déjà
affiché du prompt et en extrait le chemin (même principe que MobaXterm).
Limites de cette approche heuristique : elle ne fonctionne pas avec un
prompt fortement personnalisé (thème zsh/starship custom...), et ne gère
pas les chemins contenant des espaces.

**Pour un suivi plus fiable** (prompt personnalisé, chemins avec
espaces), configurez la séquence standard **OSC 7** (la même convention
qu'utilisent iTerm2, VS Code, gnome-terminal...) sur les machines
concernées — si présente, elle est prioritaire sur l'heuristique de
lecture du prompt. Ajoutez ceci au `.bashrc` (ou équivalent zsh) :

```bash
# Signale le répertoire courant au terminal (OSC 7) à chaque prompt
__bastion_osc7() {
    printf '\033]7;file://%s%s\033\\' "$HOSTNAME" "$PWD"
}
PROMPT_COMMAND="__bastion_osc7${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
```

Dans les deux cas, aucune erreur si rien n'est détecté — le bouton reste
juste actif en attendant un changement de répertoire reconnaissable.

**Limite volontaire** : les transferts (upload et download) passent en
base64 sur la connexion websocket, **découpés en morceaux de 256 Ko**
(`CHUNK_SIZE` dans `sftp_ws.py` et `static/js/sftp.js`) plutôt qu'envoyés
en un seul message — Flask-SocketIO plafonne par défaut la taille d'un
message à ~1 Mo, un fichier de quelques Mo envoyé d'un bloc dépasse
cette limite et bloque la connexion plutôt que d'échouer proprement.
Plafonnés à **15 Mo** par fichier au total (`MAX_TRANSFER_BYTES` — à
garder cohérent entre les deux fichiers si vous changez la valeur).
Au-delà, mieux vaut utiliser `scp`/`rsync` depuis un vrai terminal.

## Reboot / shutdown et sudo (Linux)

`exec_command()` (utilisé pour les actions rapides) n'alloue pas de
terminal, donc `sudo` ne peut pas demander un mot de passe de façon
interactive. Le code utilise `sudo -S`, qui lit le mot de passe depuis
l'entrée standard : **le même mot de passe que celui de la connexion SSH
est réutilisé pour sudo**. Si ce n'est pas votre cas, la commande
échouera avec un message explicite plutôt que de planter silencieusement.

**Meilleure pratique recommandée** : configurez `NOPASSWD` pour ces
commandes précises sur les machines gérées, par exemple dans
`/etc/sudoers.d/bastion` :

```
bastion_user ALL=(root) NOPASSWD: /usr/sbin/reboot, /usr/sbin/shutdown
```

## Changement de clé d'hôte SSH

La première connexion SSH à une machine enregistre sa clé publique dans
`machines.yaml` (champ `host_key`). Si la machine présente ensuite une
clé différente, la connexion est **refusée** et une alerte s'affiche
dans le terminal avec l'empreinte de la nouvelle clé — à vous de
confirmer explicitement si vous faites confiance à ce changement.

Si vous savez qu'un changement est légitime (ex: réinstallation), vous
pouvez aussi supprimer le champ `host_key` de la machine dans
`machines.yaml` à la main : la prochaine connexion se comportera comme
une première connexion (TOFU) et enregistrera la nouvelle clé.

## Derrière un reverse proxy (TLS)

L'appli elle-même ne fait pas de TLS — c'est le rôle d'un reverse proxy
devant elle (nginx, Caddy, Traefik...). Deux flux à faire suivre :
1. **L'appli Flask** (port 5000) : pages + Socket.IO (dashboard,
   terminal SSH, SFTP). Le client Socket.IO (`io()`) s'adapte tout seul
   au protocole de la page (`wss://` si servi en HTTPS), rien à
   configurer de ce côté.
2. **websockify** (port 6080, VNC) : à faire suivre séparément, car ce
   n'est pas un flux Socket.IO mais un WebSocket brut vers un process à
   part.

Deux façons de gérer le point 2 :

**Option A — exposer le port 6080 directement** (simple, mais un port de
plus à ouvrir/sécuriser en plus de 443) : ne rien changer côté config,
`vnc.html` bascule déjà tout seul en `wss://<host>:6080` dès que la page
est chargée en HTTPS.

**Option B — router via un chemin sur le même port que l'appli**
(recommandé si vous ne voulez exposer que 443) : définissez
`BASTION_WEBSOCKIFY_PATH` (ex: `/vnc-ws/`), et faites suivre ce chemin
vers `127.0.0.1:6080` dans le reverse proxy.

Exemple nginx (option B) :
```nginx
server {
    listen 443 ssl;
    server_name bastion.example.com;
    ssl_certificate     /etc/ssl/certs/bastion.crt;
    ssl_certificate_key /etc/ssl/private/bastion.key;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }

    location /vnc-ws/ {
        proxy_pass http://127.0.0.1:6080/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Exemple Caddy (option B, plus court) :
```
bastion.example.com {
    reverse_proxy /vnc-ws/* 127.0.0.1:6080
    reverse_proxy 127.0.0.1:5000
}
```

## Points d'attention avant la prod

- **Authentification** : le login actuel est un simple couple identifiant
  fixe / mot de passe fixe, à remplacer par un vrai annuaire (LDAP/AD,
  SSO, ou au minimum une table utilisateurs avec mots de passe hashés).
- **Identifiants SSH** : optionnellement mémorisés chiffrés (voir
  `BASTION_CREDENTIALS_KEY`) ; sans cette clé, saisis à chaque connexion
  et jamais stockés côté serveur. Le mot de passe VNC n'est jamais
  mémorisé, saisi à chaque connexion.
- **HTTPS/WSS** : mettez un reverse proxy (nginx/Traefik) devant l'app en
  TLS (voir section dédiée).
- **Traçabilité** : envisager de journaliser les ouvertures de session
  (qui se connecte à quelle machine et quand).

## Idées d'évolution

- Historique de disponibilité des machines (graphe uptime)
- Recherche/filtre sur le dashboard
- Enregistrement des sessions SSH (asciinema-like)
- Notifications (mail/Slack) quand une machine passe "down"
