"""Pont VNC pour serveurs chiffrés VeNCrypt/TLS (X509) — ce que noVNC ne
sait pas parler nativement.

Contexte (voir aussi le README, section "Limite connue: VNC chiffré") :
noVNC est une réimplémentation du protocole RFB en JavaScript pur, exécutée
dans le navigateur. Il ne sait négocier que les types de sécurité RFB
"classiques" (aucune authentification, ou mot de passe VNC standard) —
il ne peut PAS parler VeNCrypt, parce que ça bascule la connexion en TLS
*au milieu* du flux TCP (façon STARTTLS), et un navigateur ne donne à du
JS ni accès à un socket brut, ni la possibilité de renégocier TLS en
cours de connexion. Ce n'est pas un bug de noVNC, c'est une limite de la
plateforme web. RealVNC Server impose VeNCrypt par défaut sur certaines
configurations — un vrai client (RealVNC Viewer, TigerVNC, AVNC...) s'en
sort très bien, parce qu'il a accès aux vrais sockets et à une vraie lib
TLS, pas un navigateur.

Ce module fait le travail qu'un vrai client ferait : il négocie VeNCrypt +
TLS + l'authentification "interne" avec le serveur réel, exactement comme
LibVNCClient (voir les commentaires détaillés plus bas — chaque étape a été
vérifiée contre le code source de LibVNCClient, pas seulement contre la
spec en prose, qui s'est révélée ambiguë/incomplète sur plusieurs points
byte-exacts : LibVNC/libvncserver, src/libvncclient/tls_openssl.c
(HandleVeNCryptAuth, ReadVeNCryptSecurityType) et src/common/vncauth.c +
crypto_openssl.c pour le DES d'authentification VNC classique). Une fois
la session ouverte côté serveur réel, ce pont n'a plus besoin de
comprendre le protocole RFB : au-delà de ClientInit/ServerInit, les octets
sont identiques qu'ils passent par TLS ou non — donc à partir de là, il se
contente de relayer les octets bruts, dans les deux sens, sans plus les
interpréter.

Épinglage de certificat façon TOFU (même principe que ssh_client.py pour
les clés d'hôte SSH) : 1ère connexion, l'empreinte SHA-256 du certificat
présenté par le serveur est mémorisée dans machines.yaml ; connexions
suivantes, elle doit correspondre, sinon la connexion est refusée plutôt
que silencieusement acceptée. Pas de vérification par autorité de
certification : les certificats VNC de ce genre sont presque toujours
auto-signés (générés par le serveur VNC lui-même), une CA n'aurait rien à
valider de toute façon.

Limites connues (documentées, pas des oublis) :
  - Seules les variantes X509 sont supportées (X509None/X509Vnc/X509Plain)
    — pas les variantes TLS anonymes (TLSNone/TLSVnc/TLSPlain), qui
    demandent des suites de chiffrement anonymes que les bibliothèques TLS
    modernes désactivent par défaut. RealVNC Server (le cas visé ici)
    utilise X509 par défaut, donc ce n'est pas limitant en pratique.
  - L'authentification interne (mot de passe VNC standard, ou
    utilisateur/mot de passe "Plain") a lieu côté serveur (ce pont), pas
    dans le navigateur : nécessite des identifiants VNC mémorisés
    (BASTION_CREDENTIALS_KEY + machine.vnc_username/vnc_password) — pas de
    saisie interactive possible ici, contrairement au VNC non chiffré où
    noVNC peut demander le mot de passe à la volée. C'est aussi, en creux,
    une amélioration : le mot de passe ne transite plus du tout vers le
    navigateur pour ces machines (voir la limite "mot de passe VNC visible
    côté client" du README, qui ne s'applique plus ici).

Usage :
    python3 vnc_tls_bridge.py
Lit machines.yaml, ouvre un port d'écoute local par machine ayant
vnc_tls: true, et pour chaque connexion entrante (normalement de
websockify, voir gen_vnc_tokens.py) fait le pont vers le vrai serveur.
"""
import socket
import ssl
import struct
import threading
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import credentials
import store

CHALLENGE_SIZE = 16
RECV_CHUNK = 65536

# Types de sécurité RFB "externes" (avant tout basculement VeNCrypt/TLS)
SEC_TYPE_NONE = 1
SEC_TYPE_VNC_AUTH = 2
SEC_TYPE_VENCRYPT = 19

# Sous-types VeNCrypt (protocole 0.2 — U32), voir RFB proto section VeNCrypt.
VENCRYPT_PLAIN = 256
VENCRYPT_TLS_NONE = 257
VENCRYPT_TLS_VNC = 258
VENCRYPT_TLS_PLAIN = 259
VENCRYPT_X509_NONE = 260
VENCRYPT_X509_VNC = 261
VENCRYPT_X509_PLAIN = 262

# Sous-types qu'on sait gérer, dans l'ordre de préférence (chiffré +
# authentifié d'abord). Les variantes TLS anonymes ne sont volontairement
# pas listées ici (voir limites connues plus haut).
SUPPORTED_SUBTYPES = [
    VENCRYPT_X509_VNC,
    VENCRYPT_X509_PLAIN,
    VENCRYPT_X509_NONE,
]

SECURITY_RESULT_OK = 0
SECURITY_RESULT_FAILED = 1
SECURITY_RESULT_TOO_MANY = 2


class VncBridgeError(Exception):
    """Échec de négociation avec le serveur VNC réel (raison lisible dans
    le message)."""


class CertificateChanged(VncBridgeError):
    """Le certificat présenté par le serveur ne correspond pas à celui
    mémorisé (voir set_machine_vnc_cert_fingerprint) — MITM potentiel ou
    certificat régénéré côté serveur (OS réinstallé, etc.)."""


# --- E/S bas niveau ---------------------------------------------------

def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise VncBridgeError("Connexion fermée par le serveur pendant la négociation.")
        buf += chunk
    return buf


def _send_all(sock, data):
    sock.sendall(data)


# --- DES "à la VNC" pour l'authentification VNC standard ---------------
#
# Algorithme historique de RealVNC (voir crypto_openssl.c::encrypt_rfbdes) :
# la clé (mot de passe tronqué/complété à 8 octets) a chacun de ses octets
# inversé bit à bit avant usage — une bizarrerie propre à VNC, pas du DES
# standard. Le "Triple DES" avec 3 fois la même clé (E(K,D(K,E(K,x)))=E(K,x))
# donne exactement du DES simple : cryptography n'expose plus de DES seul
# (retiré comme algorithme obsolète), ce détour est le moyen propre de
# l'obtenir quand même — le protocole VNC, lui, ne nous laisse pas le choix.

def _reverse_bits(b):
    b = (b & 0xF0) >> 4 | (b & 0x0F) << 4
    b = (b & 0xCC) >> 2 | (b & 0x33) << 2
    b = (b & 0xAA) >> 1 | (b & 0x55) << 1
    return b


def _vnc_des_key(password):
    key8 = (password.encode("latin-1", errors="replace") + b"\x00" * 8)[:8]
    return bytes(_reverse_bits(b) for b in key8)


def vnc_challenge_response(challenge, password):
    if len(challenge) != CHALLENGE_SIZE:
        raise ValueError("challenge doit faire 16 octets")
    key8 = _vnc_des_key(password)
    cipher = Cipher(algorithms.TripleDES(key8 * 3), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(challenge) + encryptor.finalize()


# --- Négociation RFB "externe" (avant VeNCrypt) -------------------------

def _read_server_version(sock):
    version = _recv_exact(sock, 12)
    _send_all(sock, version)  # on répond avec la même version, comme un vrai client
    text = version.decode(errors="ignore").strip()
    try:
        _, ver = text.split(" ")
        major, minor = (int(x) for x in ver.split("."))
    except Exception as exc:  # noqa: BLE001
        raise VncBridgeError(f"Version RFB illisible: {text!r}") from exc
    return major, minor


def _select_vencrypt(sock):
    """Lit la liste des types de sécurité RFB "externes" et choisit
    VeNCrypt. Lève VncBridgeError si le serveur ne le propose pas — ce
    module n'a de raison d'être que pour ce cas précis, un serveur qui
    propose déjà un type "classique" (None/VncAuth) fonctionne directement
    avec noVNC, pas besoin de ce pont."""
    count = _recv_exact(sock, 1)[0]
    if count == 0:
        reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
        reason = _recv_exact(sock, reason_len).decode(errors="ignore")
        raise VncBridgeError(f"Le serveur refuse la connexion: {reason}")
    types = list(_recv_exact(sock, count))
    if SEC_TYPE_VENCRYPT not in types:
        raise VncBridgeError(
            f"Le serveur ne propose pas VeNCrypt (types proposés: {types}) — "
            "ce pont ne sert à rien ici, connectez-vous directement."
        )
    _send_all(sock, bytes([SEC_TYPE_VENCRYPT]))


# --- Négociation VeNCrypt -----------------------------------------------
#
# Chaque étape ci-dessous a été vérifiée contre HandleVeNCryptAuth() et
# ReadVeNCryptSecurityType() dans LibVNCClient (src/libvncclient/
# tls_openssl.c) — notamment un piège précis: il y a DEUX octets
# d'acquittement dans cette négociation, avec des polarités OPPOSÉES.
# Le premier (version VeNCrypt) suit la convention RFB habituelle
# (0 = succès). Le second (après le choix du sous-type, uniquement pour
# les sous-types chiffrés) utilise 1 = succès — l'inverse. Se tromper sur
# l'un des deux désynchronise silencieusement tout le reste de la
# négociation, avec une erreur qui n'a a priori aucun rapport (échec de
# handshake TLS, ou pire, un flux qui semble avancer mais est corrompu).

def _negotiate_vencrypt_subtype(sock):
    major, minor = _recv_exact(sock, 2)
    _send_all(sock, bytes([major, minor]))  # on réécho exactement ce que le serveur a envoyé
    status = _recv_exact(sock, 1)[0]
    if status != 0:  # convention RFB standard: 0 = succès
        raise VncBridgeError(f"Serveur: version VeNCrypt {major}.{minor} refusée.")

    count = _recv_exact(sock, 1)[0]
    if count == 0:
        raise VncBridgeError("Le serveur ne propose aucun sous-type VeNCrypt.")
    raw_subtypes = [_recv_exact(sock, 4) for _ in range(count)]
    offered = [struct.unpack(">I", b)[0] for b in raw_subtypes]

    chosen = None
    for preferred in SUPPORTED_SUBTYPES:
        if preferred in offered:
            chosen = preferred
            break
    if chosen is None:
        raise VncBridgeError(
            f"Aucun sous-type VeNCrypt supporté parmi ceux proposés: {offered} "
            "(seuls X509None/X509Vnc/X509Plain sont gérés — voir les limites "
            "connues en tête de ce fichier)."
        )

    # On renvoie les 4 octets EXACTEMENT comme reçus (même ordre d'octets),
    # pas une reconstruction depuis l'entier — c'est ce que fait le vrai
    # client, et rien ne garantit qu'un serveur soit strict sur ce point,
    # autant ne pas prendre le risque.
    _send_all(sock, raw_subtypes[offered.index(chosen)])

    if chosen != VENCRYPT_PLAIN:  # "Plain" (non chiffré) n'a pas cet acquittement
        ack = _recv_exact(sock, 1)[0]
        if ack != 1:  # polarité INVERSÉE ici: 1 = succès (voir commentaire ci-dessus)
            raise VncBridgeError(f"Serveur: sous-type VeNCrypt {chosen} refusé (ack={ack}).")

    return chosen


def _wrap_tls(raw_sock, machine, pin_certificate):
    """Bascule le socket en TLS et vérifie/mémorise le certificat serveur
    en TOFU (voir CertificateChanged). CERT_NONE côté vérification: ces
    certificats sont auto-signés, il n'y a pas d'autorité de confiance à
    interroger — l'épinglage TOFU est la seule protection possible ici,
    exactement le même compromis que pour les clés d'hôte SSH."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    tls_sock = ctx.wrap_socket(raw_sock)

    der_cert = tls_sock.getpeercert(binary_form=True)
    if pin_certificate:
        pin_certificate(machine, der_cert)

    return tls_sock


# --- Authentification "interne" (après TLS le cas échéant) -------------

def _read_security_result(sock):
    result = struct.unpack(">I", _recv_exact(sock, 4))[0]
    if result == SECURITY_RESULT_OK:
        return
    if result == SECURITY_RESULT_TOO_MANY:
        raise VncBridgeError("Authentification refusée: trop de tentatives côté serveur.")
    # SECURITY_RESULT_FAILED (ou toute valeur inattendue): RFB >= 3.8
    # fournit une raison textuelle à la suite.
    try:
        reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
        reason = _recv_exact(sock, reason_len).decode(errors="ignore")
    except VncBridgeError:
        reason = ""
    suffix = f" ({reason})" if reason else ""
    raise VncBridgeError("Authentification refusée par le serveur VNC." + suffix)


def _do_inner_auth(sock, subtype, username, password):
    if subtype in (VENCRYPT_X509_NONE, VENCRYPT_TLS_NONE):
        pass  # rien à envoyer, juste le SecurityResult
    elif subtype in (VENCRYPT_X509_VNC, VENCRYPT_TLS_VNC):
        if not password:
            raise VncBridgeError(
                "Ce serveur demande un mot de passe VNC — aucun identifiant "
                "mémorisé pour cette machine (requis: le pont s'authentifie "
                "lui-même, pas de saisie interactive possible ici)."
            )
        challenge = _recv_exact(sock, CHALLENGE_SIZE)
        _send_all(sock, vnc_challenge_response(challenge, password))
    elif subtype in (VENCRYPT_X509_PLAIN, VENCRYPT_TLS_PLAIN, VENCRYPT_PLAIN):
        if not password:
            raise VncBridgeError(
                "Ce serveur demande des identifiants (VeNCrypt Plain) — "
                "aucun identifiant mémorisé pour cette machine."
            )
        user_bytes = (username or "").encode("utf-8")
        pass_bytes = password.encode("utf-8")
        lengths = struct.pack(">II", len(user_bytes), len(pass_bytes))
        _send_all(sock, lengths + user_bytes + pass_bytes)
    else:
        raise VncBridgeError(f"Sous-type VeNCrypt non géré: {subtype}")

    _read_security_result(sock)


# --- ClientInit / ServerInit --------------------------------------------

def _client_init_server_init(sock):
    _send_all(sock, b"\x01")  # ClientInit: shared-flag=1 (partage la session)
    # width(2) + height(2) + pixel-format(16) + name-length(4) = 24 octets
    # fixes, AVANT le nom lui-même (longueur variable).
    header = _recv_exact(sock, 24)
    name_len = struct.unpack(">I", header[20:24])[0]
    name = _recv_exact(sock, name_len)
    return header + name  # ServerInit complet, tel quel


# --- Point d'entrée: connexion complète vers le vrai serveur ------------

def connect_to_real_server(machine, timeout=8, pin_certificate=None):
    """Ouvre une connexion complète (négociation RFB + VeNCrypt + TLS +
    authentification interne) vers le vrai serveur VNC de `machine`.
    Retourne (socket_pret_pour_relais, server_init_bytes)."""
    stored_password = machine.get("vnc_password")
    password = credentials.decrypt(stored_password) if stored_password else None
    username = machine.get("vnc_username")

    raw_sock = socket.create_connection((machine["host"], machine["vnc_port"]), timeout=timeout)
    raw_sock.settimeout(timeout)
    try:
        _read_server_version(raw_sock)
        _select_vencrypt(raw_sock)
        subtype = _negotiate_vencrypt_subtype(raw_sock)
        tls_sock = _wrap_tls(raw_sock, machine, pin_certificate)
        _do_inner_auth(tls_sock, subtype, username, password)
        server_init = _client_init_server_init(tls_sock)
        tls_sock.settimeout(None)  # le relais bidirectionnel n'a plus besoin de timeout
        return tls_sock, server_init
    except Exception:
        raw_sock.close()
        raise


# --- Épinglage TOFU du certificat (machines.yaml) -----------------------

def make_cert_pin_checker():
    """Retourne une fonction pin_certificate(machine, der_cert) qui
    épingle le certificat en TOFU via store.py — même principe que
    set_machine_host_key pour SSH (voir ssh_client.py)."""
    import hashlib

    def pin_certificate(machine, der_cert):
        fingerprint = hashlib.sha256(der_cert).hexdigest()
        stored = machine.get("vnc_tls_cert_fingerprint")
        if stored is None:
            store.set_machine_vnc_cert_fingerprint(machine["id"], fingerprint)
            return
        if stored != fingerprint:
            raise CertificateChanged(
                f"Le certificat TLS présenté par « {machine.get('name', machine['id'])} » a "
                "changé depuis la dernière connexion mémorisée (empreinte différente). "
                "Cela peut être normal (certificat régénéré, OS réinstallé) ou le signe "
                "d'une interception — vérifiez avant de faire confiance à ce changement. "
                "Pour réinitialiser: supprimez le champ vnc_tls_cert_fingerprint de cette "
                "machine dans machines.yaml (voir README)."
            )

    return pin_certificate


# --- Côté noVNC: mini serveur RFB en clair -------------------------------
#
# noVNC ne sait pas qu'il parle à un pont plutôt qu'à un vrai serveur — il
# lui faut donc une négociation RFB normale, minimale, qui n'offre qu'un
# type de sécurité "None": l'authentification a déjà eu lieu côté pont vers
# le vrai serveur (voir connect_to_real_server), il n'y a plus besoin d'en
# redemander une ici. L'accès à cette étape est de toute façon déjà
# protégé (connexion Bastion authentifiée + réseau interne).

def _serve_plain_handshake(sock):
    _send_all(sock, b"RFB 003.008\n")
    _recv_exact(sock, 12)  # version du client, ignorée (noVNC ne renégocie pas)
    _send_all(sock, bytes([1, SEC_TYPE_NONE]))  # 1 type proposé: None
    chosen = _recv_exact(sock, 1)[0]
    if chosen != SEC_TYPE_NONE:
        raise VncBridgeError(f"Client a choisi un type de sécurité inattendu: {chosen}")
    _send_all(sock, struct.pack(">I", SECURITY_RESULT_OK))
    _recv_exact(sock, 1)  # ClientInit du client, ignoré (partage forcé ci-dessous)


# --- Relais bidirectionnel -----------------------------------------------

def _relay(a, b):
    """Copie a -> b jusqu'à fermeture ou erreur. Destiné à tourner dans son
    propre thread, avec un jumeau pour le sens b -> a."""
    try:
        while True:
            chunk = a.recv(RECV_CHUNK)
            if not chunk:
                break
            b.sendall(chunk)
    except OSError:
        pass
    finally:
        for sock in (a, b):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def bridge_connection(client_sock, machine, pin_certificate):
    """Gère une connexion entrante de websockify de bout en bout: poignée
    de main côté noVNC, négociation complète côté vrai serveur, puis
    relais tant que la session dure."""
    try:
        _serve_plain_handshake(client_sock)
        server_sock, server_init = connect_to_real_server(machine, pin_certificate=pin_certificate)
    except Exception as exc:  # noqa: BLE001
        # Pas de manière propre de faire remonter le détail à noVNC à ce
        # stade (il n'a pas encore reçu de ServerInit) — on log côté
        # serveur (voir logs supervisord/docker) et on ferme.
        print(f"[vnc_tls_bridge] {machine.get('id', '?')}: {exc}")
        client_sock.close()
        return

    try:
        _send_all(client_sock, server_init)
    except OSError:
        server_sock.close()
        client_sock.close()
        return

    t = threading.Thread(target=_relay, args=(client_sock, server_sock), daemon=True)
    t.start()
    _relay(server_sock, client_sock)
    t.join(timeout=5)


# --- Service: un listener par machine vnc_tls: true ----------------------

def _serve_machine(machine, port, pin_certificate):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(8)
    print(f"[vnc_tls_bridge] {machine['id']}: écoute sur 127.0.0.1:{port} -> "
          f"{machine['host']}:{machine['vnc_port']} (VeNCrypt/TLS)")
    while True:
        client_sock, _ = listener.accept()
        threading.Thread(
            target=bridge_connection, args=(client_sock, machine, pin_certificate), daemon=True,
        ).start()


def main():
    pin_certificate = make_cert_pin_checker()
    threads = []
    for machine in store.load_machines():
        if not machine.get("vnc_tls"):
            continue
        port = machine.get("vnc_tls_local_port")
        if not machine.get("vnc_port") or not port:
            print(f"[vnc_tls_bridge] {machine.get('id')}: vnc_tls activé mais "
                  "vnc_port/vnc_tls_local_port manquant, ignoré.")
            continue
        t = threading.Thread(
            target=_serve_machine, args=(machine, port, pin_certificate), daemon=True,
        )
        t.start()
        threads.append(t)

    if not threads:
        print("[vnc_tls_bridge] Aucune machine avec vnc_tls: true — rien à faire, veille.")

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
