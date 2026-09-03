# Bastion — dashboard + SSH/VNC web

[![CI](https://github.com/nopalpite/bastion/actions/workflows/ci.yml/badge.svg)](https://github.com/nopalpite/bastion/actions/workflows/ci.yml)
[![Docker build](https://github.com/nopalpite/bastion/actions/workflows/docker-build.yml/badge.svg)](https://github.com/nopalpite/bastion/actions/workflows/docker-build.yml)

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
- **Plan interactif par salle** (`/map/<salle>`) : importez une image de plan (n'importe quelle résolution/ratio, voir plus bas), placez les machines dessus par glisser-déposer (souris et tactile), cliquez sur une machine pour ouvrir un accès rapide SSH/VNC ou déclencher un reboot/shutdown.
- **Terminal SSH** dans le navigateur, avec navigateur de fichiers SFTP dans une colonne latérale (façon MobaXterm).
- **VNC** dans le navigateur via noVNC, y compris les serveurs chiffrés (VeNCrypt/TLS, RealVNC...) — voir le pont VNC générique ci-dessous.
- **Statistiques de disponibilité** (`/stats`) : pourcentage de dispo (24h/7j/30j) et frise chronologique par machine, à partir de l'historique des vérifications de `monitor.py` — voir la section dédiée plus bas.
- **Épinglage de la clé d'hôte SSH (TOFU)** : la première connexion à une machine mémorise sa clé publique ; si elle change ensuite, la connexion est bloquée avec une alerte explicite.
- **Identifiants mémorisés (optionnel)** : si vous configurez `BASTION_CREDENTIALS_KEY`, vous pouvez enregistrer les identifiants SSH et/ou VNC d'une machine, chiffrés. Sans cette clé, la mémorisation est simplement désactivée (rien n'est stocké en clair par erreur). Sans identifiants mémorisés, noVNC les demande en interactif à la connexion.

## Plan interactif : alignement position <-> image, quel que soit l'écran

Les positions des machines sur le plan (`machine.position.x`/`.y`) sont
stockées en **pourcentage** de la taille de l'image, pas en pixels
absolus — pour qu'une machine placée sur le plan reste au même endroit
visuel quel que soit l'écran qui l'affiche ensuite (PC, tablette,
smartphone, n'importe quelle taille de fenêtre), et pas seulement sur
l'écran où elle a été positionnée.

Pour que ce `%` corresponde toujours au même pixel de l'image, le
conteneur qui l'affiche (`.map-wrap`) doit avoir **exactement** le même
ratio largeur/hauteur que l'image — sinon l'image est affichée en
`object-fit: contain`, qui ajoute des bandes vides dont la taille varie
selon l'écran, et le repère en `%` dérive par rapport à l'image. N'importe
quelle résolution/ratio d'image de plan est accepté (PNG/JPG/SVG) : le
ratio réel est lu côté serveur via Pillow dès que la page se charge (voir
`app.py:map_view`, `map_image.py`), injecté directement dans le CSS
(variables `--map-w`/`--map-h`, voir `.map-wrap` dans `style.css`) — pas
de calcul dynamique attendant le chargement de l'image côté navigateur,
donc pas de "flash" de mauvais ratio à l'affichage. Exception : le SVG,
vectoriel, n'a pas de résolution fixe que Pillow puisse lire ; son ratio
réel est déterminé côté navigateur à la place (filet de sécurité dans
`static/js/map.js`, qui recalcule `--map-w`/`--map-h` une fois l'image
chargée — inoffensif pour les autres formats, où il ne fait que
reconfirmer les mêmes valeurs).

Un plan sans image (fond neutre) utilise un ratio 16/9 par défaut.

## Pont VNC générique (`vnc_tls_bridge.py`)

Toute machine avec un port VNC configuré passe par ce pont plutôt que
d'être jointe directement — il tourne comme process séparé dans le
conteneur (voir `supervisord.conf`), un port local par machine (assigné
automatiquement, voir `store.py`). À chaque connexion, il **sonde le vrai
serveur et s'adapte**, comme le ferait un vrai client VNC (RealVNC Viewer,
TigerVNC, AVNC...) plutôt que noVNC seul :
- **Serveur VNC "classique"** (aucune authentification, ou mot de passe
  VNC standard — le cas le plus courant) : relais transparent, sans
  aucune interprétation du protocole. noVNC négocie directement avec le
  vrai serveur à travers le pont, y compris la demande interactive du mot
  de passe dans le navigateur si rien n'est mémorisé — exactement comme
  sans ce pont.
- **Serveur VeNCrypt/TLS** (chiffré — comportement par défaut de
  **RealVNC Server**, préinstallé sur Raspberry Pi OS, sur certaines
  configurations) : noVNC (réimplémentation du protocole RFB en
  JavaScript pur, exécutée dans le navigateur) ne peut fondamentalement
  pas le négocier lui-même — ce type de sécurité bascule en chiffrement
  TLS *au milieu* de la connexion TCP (façon STARTTLS), et un navigateur
  ne donne à du JS ni accès à un socket brut, ni la possibilité de
  renégocier TLS en cours de connexion. Le pont fait alors, côté serveur,
  ce qu'un vrai client ferait : négocier VeNCrypt + TLS + l'authentification
  avec la machine cible, puis exposer la session en VNC non chiffré au
  reste de la chaîne (`websockify`/noVNC).
- **Aucun des deux** (types plus exotiques — ARD, MSLogon, SASL seuls...) :
  échec propre, avec la liste des types proposés dans les logs
  (`docker logs bastion 2>&1 | grep vnc_tls_bridge`) — voir les options de
  contournement plus bas.

**Rien à configurer par machine** — pas de case à cocher, le pont
détermine lui-même à chaque connexion quel chemin prendre. Seule exigence
dans le cas VeNCrypt précisément : un mot de passe VNC mémorisé pour cet
hôte (`BASTION_CREDENTIALS_KEY` configurée, voir plus bas) — **l'authentification
a lieu côté serveur** dans ce cas (le pont s'authentifie lui-même auprès
de la machine cible), pas de saisie interactive possible, contrairement
au cas "classique" ci-dessus. Contrepartie utile : pour ces machines-là,
le mot de passe VNC **ne transite plus du tout vers le navigateur** —
l'authentification se fait entièrement côté backend (voir la note sur les
identifiants VNC mémorisés dans les fonctionnalités ci-dessus, qui ne
s'applique donc pas à ce cas précis).

**Certificat serveur (cas VeNCrypt)** : ces certificats sont presque
toujours auto-signés (générés par le serveur VNC lui-même) — il n'y a pas
d'autorité de confiance à interroger. Le pont épingle l'empreinte du
certificat à la première connexion (TOFU, même principe que pour les
clés d'hôte SSH) : s'il change ensuite, la connexion est refusée plutôt
qu'acceptée silencieusement. Pour réinitialiser après un changement
légitime (certificat régénéré, réinstallation), supprimez le champ
`vnc_tls_cert_fingerprint` de la machine dans `machines.yaml` — la
prochaine connexion se comportera comme une première connexion.

**Limite connue** : côté VeNCrypt, seules les variantes X509 sont gérées
(`X509None`/`X509Vnc`/`X509Plain`) — pas les variantes TLS anonymes
(`TLSNone`/`TLSVnc`/`TLSPlain`), qui demandent des suites de chiffrement
que les bibliothèques TLS modernes désactivent par défaut. RealVNC
Server (le cas visé ici) utilise X509 par défaut, donc pas limitant en
pratique ; `debug_vnc_security.py` (ci-dessous) permet de vérifier ce
qu'un serveur donné propose réellement.

Si malgré tout ça ne convient pas à votre cas (variante non gérée,
diagnostic difficile) :
- désactiver le chiffrement RFB natif côté machine cible
  (`Encryption=AlwaysOff` dans la config RealVNC Server) — acceptable
  sur un LAN de confiance, à éviter sur une machine exposée ;
- utiliser un serveur VNC qui ne force pas VeNCrypt (TigerVNC, x11vnc) ;
- se connecter à cette machine précise avec un vrai client VNC en
  dehors du bastion, pour les cas ponctuels.

`debug_vnc_security.py` (à la racine du projet) est un petit script de
diagnostic qui affiche, sans dépendre de rien d'autre, la liste exacte
des types de sécurité qu'un serveur VNC propose — utile pour savoir
rapidement si une machine donnée est concernée par cette limite, ou pour
vérifier après coup ce que `vnc_tls_bridge.py` a réellement négocié :
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
  map_image.py            validation + lecture du ratio des images de plan (voir plus haut)
  monitor.py             thread de fond: ping + test de port SSH
  history.py               historique de disponibilité (SQLite, page /stats)
  ssh_ws.py               pont Socket.IO <-> Paramiko pour le terminal
  ssh_client.py            connexion SSH avec épinglage de la clé d'hôte (TOFU)
  sftp_ws.py               navigateur de fichiers (colonne latérale du terminal)
  ssh_actions.py           actions ponctuelles (reboot/shutdown) via SSH
  gen_vnc_tokens.py        génère le fichier de tokens pour websockify (VNC uniquement)
  vnc_tls_bridge.py        pont VNC générique (relais transparent, ou VeNCrypt/TLS si besoin)
  debug_vnc_security.py    diagnostic RFB autonome (types de sécurité VNC)
  machines.yaml           inventaire: salles + machines
  templates/               pages Jinja2 (dashboard, plan, formulaires, terminal, vnc, stats)
  static/css/            style.css (thème console sombre)
  static/js/              dashboard.js, terminal.js, sftp.js, map.js, actions.js, stats.js
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
token dans l'URL (`?token=srv-linux-01` par exemple). `vnc_tokens.conf`
est régénéré automatiquement par l'appli à chaque ajout/modification/
suppression de machine depuis l'interface (voir `store.py`) — websockify
relit ce fichier à chaque connexion sans le mettre en cache, donc aucun
redémarrage n'est nécessaire. Seule une modification **directe** de
`machines.yaml` (en dehors de l'interface) demande de relancer
`gen_vnc_tokens.py` vous-même :
```bash
python gen_vnc_tokens.py > vnc_tokens.conf
```

Ça ne suffirait pas à lui seul : `vnc_tls_bridge.py` (le pont réel entre
websockify et chaque machine, voir plus haut) doit lui aussi savoir
qu'une machine existe pour ouvrir un port d'écoute local pour elle. Il
sonde donc l'inventaire toutes les `POLL_INTERVAL` secondes (5 par défaut)
et ouvre les nouveaux ports au fur et à mesure — une machine ajoutée
depuis l'interface devient donc joignable en VNC en quelques secondes,
sans redémarrage. Les infos d'une machine existante (hôte, identifiants…)
sont elles aussi relues à chaque connexion, pas mises en cache : modifier
une machine prend effet immédiatement, dès la connexion suivante.

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
est testé séparément par ouverture de port TCP, et le service **VNC**
(si configuré) par une sonde RFB minimale (`vnc_tls_bridge.probe_available`)
— tous deux affichés sous forme de badges.

- 🟢 **up** (pastille) — répond au ping
- 🔴 **down** (pastille) — ne répond pas
- badge **SSH** — vert si le port répond, rouge sinon (indépendant du ping)
- badge **VNC** (si un port VNC est configuré) — vert si le service répond, rouge sinon

**⚠️ Historique VNC** : une version précédente de ce monitoring sondait
ce port toutes les 15 secondes par une simple ouverture/fermeture TCP,
sans même lire la réponse du serveur. En usage réel, ça avait coïncidé
avec un déclenchement de la protection anti-bruteforce de RealVNC Server
(`TooManySecFail`), qui avait blacklisté l'IP du bastion. Le test avait
alors été retiré entièrement par précaution.

En reconsidérant la question : la doc officielle RealVNC (paramètre
`BlacklistThreshold`) est explicite — ce compteur ne réagit qu'à des
**tentatives d'authentification** ratées (*"ignored if Authentication is
set to None"*), pas à une connexion TCP suivie d'une lecture de la
poignée de main. Avec le recul, la cause la plus probable de l'incident
était les nombreuses tentatives de connexion manuelles ratées (mauvais
mot de passe) faites pendant les tests de l'époque, pas le sondage
automatique. Le badge VNC est donc de retour, mais via une sonde qui
s'arrête volontairement à la lecture de la poignée de main RFB (version
+ types de sécurité proposés) — elle ne choisit jamais de type de
sécurité et ne tente donc jamais d'authentification (voir le docstring
de `probe_available` dans `vnc_tls_bridge.py`, et le test
`test_probe_available_never_chooses_a_security_type` qui vérifie cette
invariante). Reste un point à surveiller en usage réel : si un doute
réapparaît malgré tout, `CHECK_INTERVAL_SECONDS` dans `monitor.py` est le
seul réglage à changer, ou retirez l'appel à `probe_available` dans
`_check_services`.

**Docker** : le ping ICMP a besoin de la capacité `NET_RAW`, qui fait
partie de l'ensemble de capacités par défaut de Docker — rien à
configurer normalement.

## Statistiques de disponibilité (`/stats`)

Chaque vérification faite par `monitor.py` (toutes les 15 secondes, voir
ci-dessus) est aussi enregistrée dans un historique — pourcentage de
disponibilité (24h/7j/30j) et frise chronologique par machine, affichés
sur la page `/stats`.

**Stockage** : un fichier **SQLite séparé** (`history.py`, module
`sqlite3` de la stdlib Python — aucune nouvelle dépendance),
`<BASTION_DATA_DIR>/history.db`, donc dans le même volume que
`machines.yaml` (persistant, voir la section Docker). Choisi plutôt que
la mémoire (perdu à chaque redémarrage du conteneur, peu utile pour un
historique) ou un fichier plat (SQLite gère nativement les requêtes par
plage de temps, ce qu'un CSV/JSON demanderait de réimplémenter à la main).

**Rétention** : les enregistrements plus vieux que la rétention
configurée sont purgés automatiquement une fois par heure (pour ne pas
grossir indéfiniment), et à la demande via le bouton **Purger
maintenant** sur la page `/stats`. Rétention par défaut : **30 jours**,
via `BASTION_HISTORY_RETENTION_DAYS` — n'amorce la valeur qu'au tout
premier démarrage (aucun réglage encore enregistré) ; changez-la ensuite
directement depuis la page `/stats`, c'est cette valeur-là qui fait foi
par la suite (la variable d'env n'est plus relue).

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
| `BASTION_VNC_WS_PORT` | port où joindre le proxy VNC (l'hôte est déterminé automatiquement par le navigateur) | `6080` |
| `BASTION_VNC_WS_PATH` | si définie (ex: `/vnc-ws/`), route le VNC via ce chemin sur le même host:port que la page plutôt que le port direct — utile derrière un reverse proxy TLS | (vide, mode direct) |
| `BASTION_CREDENTIALS_KEY` | clé de chiffrement des identifiants SSH mémorisés. Sans elle, la mémorisation est désactivée. Générer avec `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | (aucune) |
| `BASTION_DATA_DIR` | dossier contenant `machines.yaml`. L'image Docker la définit à `/app/config` (voir la section Docker) ; sans intérêt à changer hors Docker | dossier de l'appli |
| `BASTION_TLS_SELFSIGNED` | si `true`, sert HTTPS/WSS directement (app + VNC) avec un certificat auto-signé généré et géré par Bastion — voir la section "TLS sans reverse proxy" | (vide, HTTP) |
| `BASTION_TLS_CERT` / `BASTION_TLS_KEY` | chemins vers un certificat déjà existant, prioritaire sur `BASTION_TLS_SELFSIGNED` si les deux sont définis | (aucun) |
| `BASTION_HISTORY_RETENTION_DAYS` | rétention de l'historique de disponibilité (page `/stats`) — n'amorce le réglage qu'au premier démarrage, modifiable ensuite depuis l'interface | `30` |

## Lancement

```bash
python app.py
```

Puis ouvrez `http://localhost:5000`.

## Lancement avec Docker

Le fichier `docker-compose.yml` est déjà fourni à la racine du dépôt.
Contenu à adapter (au minimum les variables
`BASTION_SECRET_KEY` / `BASTION_ADMIN_PASSWORD`, et `BASTION_CREDENTIALS_KEY`
si vous voulez mémoriser des identifiants SSH) :

```yaml
services:
  bastion:
    image: ghcr.io/nopalpite/bastion:latest   # ou un tag de version, ex: 1.2.0
    container_name: bastion
    # network_mode: host = pas de NAT, le conteneur voit exactement les
    # mêmes interfaces/routes que le host. Recommandé pour un bastion:
    # évite les surprises de routage vers les réseaux cibles, quelle que
    # soit la topologie (mono ou multi-interfaces/VLAN).
    network_mode: host
    restart: unless-stopped
    environment:
      - BASTION_SECRET_KEY=change-moi-en-production
      - BASTION_ADMIN_USER=admin
      - BASTION_ADMIN_PASSWORD=change-moi
      - BASTION_VNC_WS_PORT=6080
      # Vide par défaut (connexion VNC directe sur le port ci-dessus).
      # À définir (ex: /vnc-ws/) UNIQUEMENT si vous passez par un reverse
      # proxy TLS qui fait suivre ce chemin vers 127.0.0.1:6080 — voir la
      # section "Derrière un reverse proxy (TLS)" du README. Piège: si vous
      # utilisez BASTION_TLS_SELFSIGNED/_CERT ci-dessous (pas de reverse
      # proxy), laissez cette variable VIDE — sinon vnc.html tente de se
      # connecter sur ce chemin via le port 5000, que rien ne sait servir,
      # et la connexion échoue silencieusement.
      - BASTION_VNC_WS_PATH=
      # Sans cette clé, la mémorisation des identifiants SSH/VNC est
      # désactivée. Générer avec:
      # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
      - BASTION_CREDENTIALS_KEY=
      # Optionnel: sert HTTPS/WSS directement (app, VNC) sans passer par
      # un reverse proxy — voir la section "TLS sans reverse proxy" du
      # README. Décommenter pour que Bastion génère et gère seul un
      # certificat auto-signé (stocké dans ./config/tls, persistant) :
      # - BASTION_TLS_SELFSIGNED=true
      # Rétention de l'historique de disponibilité (page /stats) —
      # n'amorce ce réglage qu'au 1er démarrage, modifiable ensuite
      # directement depuis l'interface (voir la section dédiée du README).
      - BASTION_HISTORY_RETENTION_DAYS=30
    volumes:
      # Monter l'inventaire (machines.yaml) en externe pour le modifier sans
      # rebuild. Un DOSSIER, pas le fichier directement — voir la note
      # ci-dessous. C'est aussi là qu'un certificat TLS auto-signé
      # (BASTION_TLS_SELFSIGNED ci-dessus) et l'historique de
      # disponibilité (history.db) sont stockés, pour persister au
      # rebuild.
      - ./config:/app/config
      # Persister les plans de salle uploadés (sinon perdus au rebuild)
      - ./data/maps:/app/static/uploads/maps
```

```bash
docker compose up -d
```

L'image est publiée sur GHCR (`linux/amd64` + `linux/arm64`) à chaque push
sur `main` et à chaque tag `vX.Y.Z` (voir
`.github/workflows/docker-build.yml`). Pour builder depuis les sources à la
place (si vous modifiez le code, ou pour `armv7`/32-bit — non publié,
voir la section ARM ci-dessous), remplacez la ligne `image:` par `build: .`
et lancez `docker compose up --build -d`.

Le conteneur tourne en `network_mode: host` : il partage directement les
interfaces et routes réseau du host — le choix le plus simple pour un
bastion, qui a besoin de joindre les mêmes réseaux/VLAN que la machine
sur laquelle il tourne.

**Pare-feu** : si le host a des règles iptables de filtrage (fréquent sur
un bastion), pensez à les mettre dans la chaîne `DOCKER-USER` plutôt que
`INPUT`/`FORWARD` — Docker peut sinon les contourner pour le trafic à
destination de ses conteneurs.

`machines.yaml` (dans `./config`, monté en volume) est en lecture-écriture,
nécessaire puisque l'appli y écrit quand vous ajoutez un hôte/salle depuis
l'interface : dans ce cas, `vnc_tokens.conf` est régénéré automatiquement
(voir la section websockify plus haut), pas besoin de redémarrer. Vous
pouvez aussi modifier `machines.yaml` à la main sans reconstruire l'image
— dans ce cas uniquement, redémarrez le conteneur (`docker compose
restart`) pour que `vnc_tokens.conf` soit régénéré à partir de vos
changements (ça se fait au démarrage, voir `docker/entrypoint.sh`).

**Pourquoi monter `./config` (un dossier) plutôt que `machines.yaml`
directement (un fichier)** : si le fichier n'existe pas encore côté hôte au
tout premier démarrage, Docker crée un **dossier** à cet emplacement au
lieu d'un fichier — le montage échoue alors avec une erreur du type
`... not a directory: Are you trying to mount a directory onto a file?`.
Monter un dossier n'a pas ce problème (Docker crée un dossier des deux
côtés si besoin) ; `./config/machines.yaml` est amorcé automatiquement
avec un inventaire d'exemple si le dossier est vide au démarrage (voir
`docker/entrypoint.sh`).

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
brut, sans erreur. `Enregistrer` (ou **Ctrl+S** / **Cmd+S**) réécrit le
fichier sur la machine cible via SFTP.

Quelques raccourcis/repères en plus du strict minimum :
- **Ctrl+F** (rechercher) / **Ctrl+H** (remplacer) — addon `search` de
  CodeMirror, stylé pour rester lisible sur le thème sombre du site.
- Position du curseur affichée dans l'en-tête (`L12:C4`), avec le nombre
  de caractères sélectionnés le cas échéant.
- Fermer l'onglet ou recharger la page avec des modifications non
  enregistrées déclenche l'avertissement natif du navigateur (déjà le
  cas pour le bouton "Fermer" de la modale, mais pas pour ces deux
  chemins de sortie avant cet ajout).

Limité aux fichiers texte **UTF-8** de moins de **5 Mo** (`MAX_EDIT_BYTES`
dans `sftp_ws.py`) : un fichier binaire ou trop volumineux affiche un
message clair plutôt que du charabia dans l'éditeur — téléchargez-le
dans ce cas plutôt que de l'éditer ici.

**Limite volontaire** : pas d'ouverture dans un éditeur de texte natif de
votre PC (façon MobaXterm) — un navigateur ne peut pas lancer un
programme local pour des raisons de sécurité de la plateforme, c'est une
limite du web, pas de cet éditeur en particulier. Téléchargez le fichier
puis re-uploadez-le après modification si vous avez besoin de votre
éditeur habituel.

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

## TLS sans reverse proxy (certificat auto-signé)

Pas de reverse proxy ni de certificat officiel disponible (LAN isolé, accès
via IP plutôt que nom de domaine...) mais besoin quand même de servir en
HTTPS ? Bastion peut gérer lui-même un certificat TLS, sur ses deux
serveurs réseau à la fois (l'appli Flask, `websockify` pour le VNC — voir
`tls.py`), plutôt que de passer par la section suivante.

Deux variables d'environnement, au choix :
- **`BASTION_TLS_SELFSIGNED=true`** : Bastion génère seul un certificat
  auto-signé au premier démarrage (stocké dans `<BASTION_DATA_DIR>/tls/`,
  donc persistant si ce dossier est monté en volume — voir
  `docker-compose.yml`) et le réutilise ensuite sans le régénérer (une
  nouvelle paire à chaque démarrage changerait son empreinte à chaque
  fois, et redéclencherait l'avertissement de sécurité du navigateur).
  Le certificat n'étant signé par aucune autorité reconnue, le navigateur
  affichera un avertissement à la première visite (normal, à accepter
  manuellement) — c'est la contrepartie de ne pas dépendre d'une autorité
  de certification externe.
- **`BASTION_TLS_CERT` / `BASTION_TLS_KEY`** : chemins vers un certificat
  déjà existant (auto-signé fait main, ou même un vrai certificat déjà en
  votre possession) — prioritaire sur `BASTION_TLS_SELFSIGNED` si les deux
  sont définis.

Sans aucune des deux : HTTP en clair (comportement par défaut, inchangé).
Une fois l'une des deux activée, tout se sert automatiquement en HTTPS/WSS
sur les mêmes ports qu'aujourd'hui (5000, 6080) — `vnc.html` bascule déjà
tout seul en `wss://` dès que la page est chargée en HTTPS (voir plus
bas), donc aucune autre configuration n'est nécessaire.

**Piège à éviter — ne définissez PAS `BASTION_VNC_WS_PATH` en même temps
que `BASTION_TLS_SELFSIGNED`/`BASTION_TLS_CERT`** : cette variable de
chemin existe pour la section suivante (reverse proxy), où c'est LUI qui
fait suivre ce chemin vers le port du pont VNC. Sans reverse proxy, rien
ne sait servir ce chemin — `vnc.html` tenterait alors de se connecter en
WebSocket sur `wss://<host>:5000/vnc-ws/`, une route que l'appli Flask ne
connaît pas, et la connexion échouerait avec une erreur générique
("Impossible de joindre le pont VNC") sans lien apparent avec la vraie
cause. Avec `BASTION_TLS_SELFSIGNED`/`BASTION_TLS_CERT`, laissez cette
variable **vide** : le pont VNC parle TLS lui-même directement sur son
port (6080), pas besoin de chemin.

## Derrière un reverse proxy (TLS)

Si vous avez déjà un reverse proxy avec un certificat officiel devant
Bastion (nginx, Caddy, Traefik...), c'est lui qui doit terminer le TLS —
inutile d'activer aussi `BASTION_TLS_SELFSIGNED` ci-dessus (les deux
approches sont indépendantes et ne se combinent pas). Deux flux à faire
suivre :
1. **L'appli Flask** (port 5000) : pages + Socket.IO (dashboard,
   terminal SSH, SFTP). Le client Socket.IO (`io()`) s'adapte tout seul
   au protocole de la page (`wss://` si servi en HTTPS), rien à
   configurer de ce côté.
2. **websockify** (port 6080, VNC) : à faire suivre séparément, car ce
   n'est pas un flux Socket.IO mais un WebSocket brut vers un process à
   part.

Pour le point 2, deux façons de faire — mais une seule fonctionne
réellement une fois l'appli servie en HTTPS (le sujet même de cette
section) :

**Option A — exposer le port directement** (6080) : **ne fonctionne PAS
si l'appli est servie en HTTPS, sauf à activer aussi le TLS de Bastion
lui-même** (voir la section précédente, `BASTION_TLS_CERT`/`_KEY`/
`_SELFSIGNED`) sur `websockify` — auquel cas autant s'en servir aussi pour
l'appli et se passer entièrement du reverse proxy pour ce flux. Sans ça :
`vnc.html` bascule bien automatiquement en `wss://<host>:6080` dès que la
page est chargée en HTTPS (le navigateur l'exige — contenu mixte sinon
bloqué), mais si `websockify` ne parle pas TLS, la connexion `wss://`
échoue alors avant même d'atteindre le pont, sans quasiment aucune
information exploitable côté navigateur (`Firefox ne peut établir de
connexion avec le serveur`, `Connection closed` côté noVNC...). Cette
option n'a donc de sens que si l'appli elle-même reste en HTTP (accès
direct sur un LAN, sans reverse proxy TLS devant) — dans le contexte de
cette section, ce n'est pas le cas : passez par l'option B.

**Option B — router via un chemin sur le même port que l'appli**
(la seule qui fonctionne une fois l'appli en HTTPS) : définissez
`BASTION_VNC_WS_PATH` (ex: `/vnc-ws/`), et faites suivre ce chemin vers
`127.0.0.1:6080` dans le reverse proxy — c'est lui qui termine le TLS
pour ce flux aussi, exactement comme pour l'appli Flask au point 1.

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
- **Identifiants SSH/VNC** : optionnellement mémorisés chiffrés (voir
  `BASTION_CREDENTIALS_KEY`) ; sans cette clé (ou sans les mémoriser),
  saisis à chaque connexion et jamais stockés côté serveur — sauf pour un
  serveur VNC chiffré (VeNCrypt/TLS), où ils sont nécessairement requis
  (voir le pont VNC générique).
- **HTTPS/WSS** : mettez un reverse proxy (nginx/Traefik) devant l'app en
  TLS (voir section dédiée).
- **Traçabilité** : envisager de journaliser les ouvertures de session
  (qui se connecte à quelle machine et quand).

## Idées d'évolution

- Recherche/filtre sur le dashboard
- Enregistrement des sessions SSH (asciinema-like)
- Notifications (mail/Slack) quand une machine passe "down"
