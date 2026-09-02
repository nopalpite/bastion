"""Négociation avec guacd (Apache Guacamole) — le protocole "Guacamole",
qui n'a rien à voir avec RDP lui-même. Séparé de rdp_bridge.py pour rester
importable sans déclencher eventlet.monkey_patch() (voir ce fichier):
ce module-ci n'a besoin que de socket/codecs standard, donc testable
directement, sur n'importe quelle version de Python.

Format des instructions: chaque élément est encodé "LONGUEUR.VALEUR"
(LONGUEUR en caractères Unicode, VALEUR en UTF-8), séparés par des
virgules, l'instruction se terminant par un point-virgule — ex.
b"6.select,3.rdp;". La longueur explicite permet à une valeur de contenir
absolument n'importe quel caractère (y compris des virgules ou
points-virgules) sans ambiguïté : impossible de se contenter de chercher
le prochain ";" pour trouver la fin d'un élément ou d'une instruction, il
faut suivre les longueurs annoncées. Documenté ici :
https://guacamole.apache.org/doc/gug/guacamole-protocol.html

Ouvrir une session tient en une poignée d'instructions (voir _handshake:
select -> args -> capacités client -> connect -> ready), après quoi plus
besoin de comprendre le contenu : comme pour VeNCrypt une fois
ClientInit/ServerInit passés, l'appelant peut relayer les octets bruts
sans plus les interpréter (le rendu du flux Guacamole, y compris les
mises à jour bitmap, est entièrement à la charge de guacamole-common-js
côté navigateur — voir rdp_bridge.py et templates/rdp.html)."""
import codecs
import socket

import credentials

GUACD_HOST = "127.0.0.1"
GUACD_PORT = 4822
RECV_CHUNK = 65536


class GuacamoleError(Exception):
    """Échec de négociation avec guacd (raison lisible dans le message)."""


# --- Encodage/décodage des instructions ----------------------------------

def encode_instruction(*elements):
    parts = []
    for el in elements:
        text = str(el)
        parts.append(f"{len(text)}.{text}")
    return (",".join(parts) + ";").encode("utf-8")


def _recv_more(sock, buf):
    chunk = sock.recv(RECV_CHUNK)
    if not chunk:
        raise GuacamoleError("guacd a fermé la connexion pendant la négociation.")
    return buf + chunk


def _read_element(sock, buf):
    """Lit un élément préfixé par sa longueur depuis sock (en réutilisant
    l'excédent déjà lu dans buf). Retourne (valeur_str, terminateur, buf
    restant), terminateur étant b"," (élément suivant) ou b";" (fin
    d'instruction).

    La longueur annoncée compte des CARACTÈRES Unicode, pas des octets
    UTF-8 (confirmé par la doc du protocole Guacamole) — comme l'UTF-8 est
    à taille variable, impossible de savoir d'avance combien d'octets ces
    caractères occupent sur le fil. On décode donc de façon incrémentale,
    un octet à la fois (les éléments échangés ici — noms de paramètres,
    identifiants, messages d'erreur — sont petits ; le flux "normal"
    post-négociation, potentiellement volumineux, n'est lui plus jamais
    reparsé une fois "ready" reçu, voir _handshake)."""
    while b"." not in buf:
        buf = _recv_more(sock, buf)
    dot = buf.index(b".")
    try:
        length = int(buf[:dot])
    except ValueError as exc:
        raise GuacamoleError(f"Instruction Guacamole illisible: {buf[:dot + 20]!r}") from exc

    pos = dot + 1
    decoder = codecs.getincrementaldecoder("utf-8")()
    decoded = ""
    while len(decoded) < length:
        while pos >= len(buf):
            buf = _recv_more(sock, buf)
        decoded += decoder.decode(buf[pos:pos + 1])
        pos += 1

    while pos >= len(buf):
        buf = _recv_more(sock, buf)
    terminator = buf[pos:pos + 1]
    return decoded, terminator, buf[pos + 1:]


def read_instruction(sock, buf):
    """Lit une instruction Guacamole complète. Retourne (opcode, [args], buf
    restant) — buf restant contient tout ce qui a déjà été lu en trop
    (potentiellement le début du flux "normal" qui suit la négociation)."""
    elements = []
    while True:
        value, terminator, buf = _read_element(sock, buf)
        elements.append(value)
        if terminator == b";":
            return elements[0], elements[1:], buf


# --- Poignée de main avec guacd ------------------------------------------
#
# Séquence documentée par le protocole Guacamole: le CLIENT (ici,
# rdp_bridge.py — côté navigateur, guacamole-common-js n'a lui-même pas
# connaissance de cette négociation) choisit le protocole cible, guacd
# répond avec sa version de protocole ET la liste des paramètres qu'il
# attend pour ce protocole, le client annonce ses capacités (taille
# d'écran, formats audio/vidéo/image supportés — vides ici, on ne gère
# pas ces extensions), puis envoie "connect" avec CETTE MÊME VERSION en
# première valeur, suivie d'une valeur pour CHAQUE paramètre demandé,
# DANS L'ORDRE où guacd les a listés (positionnel, pas nommé) — un
# paramètre non pertinent est envoyé comme chaîne vide plutôt qu'omis.
#
# Le renvoi de la version dans "connect" est facile à manquer en lisant
# la spec en prose (elle dit surtout "une valeur par paramètre"), mais
# est explicite dans l'exemple donné par la doc officielle du protocole
# (guacamole-protocol.html): la version PRÉCÈDE les valeurs de paramètres
# dans "connect", exactement comme dans "args" — l'omettre fait que
# "connect" a une valeur de MOINS que ce que guacd attend, et guacd
# refuse alors la connexion avec "Client did not return the expected
# number of arguments" (confirmé en le prenant en défaut contre un vrai
# guacd 1.6.0, pas seulement contre un faux serveur local — voir
# tests/test_rdp_protocol.py::test_connect_includes_protocol_version).

def _handshake(sock, protocol, params):
    buf = b""
    sock.sendall(encode_instruction("select", protocol))

    opcode, args, buf = read_instruction(sock, buf)
    if opcode != "args":
        raise GuacamoleError(f"guacd: attendu 'args', reçu {opcode!r} ({args!r}).")
    protocol_version = args[0]
    param_names = args[1:]

    sock.sendall(encode_instruction("size", 1024, 768, 96))
    sock.sendall(encode_instruction("audio"))
    sock.sendall(encode_instruction("video"))
    sock.sendall(encode_instruction("image"))

    connect_values = [str(params.get(name, "")) for name in param_names]
    sock.sendall(encode_instruction("connect", protocol_version, *connect_values))

    opcode, args, buf = read_instruction(sock, buf)
    if opcode == "error":
        message = args[0] if args else "raison inconnue"
        raise GuacamoleError(f"guacd refuse la connexion: {message}")
    if opcode != "ready":
        raise GuacamoleError(f"guacd: attendu 'ready', reçu {opcode!r} ({args!r}).")

    return buf  # tout ce qui a déjà été lu en trop doit être relayé aussi


def connect_via_guacd(protocol, params, timeout=10):
    """Ouvre une session guacd complète pour le protocole donné (params
    positionnés selon ce que guacd demande — voir _handshake). Retourne le
    socket, prêt pour un relais bidirectionnel brut, et les octets déjà
    lus en trop (à envoyer en premier lors du relais)."""
    sock = socket.create_connection((GUACD_HOST, GUACD_PORT), timeout=timeout)
    sock.settimeout(timeout)
    try:
        leftover = _handshake(sock, protocol, params)
        sock.settimeout(None)
        return sock, leftover
    except Exception:
        sock.close()
        raise


def rdp_params_for_machine(machine):
    """Construit le dict de paramètres RDP à partir d'une machine de
    l'inventaire — voir la doc Guacamole pour la liste complète des noms
    de paramètres reconnus pour le protocole "rdp"."""
    stored_password = machine.get("rdp_password")
    password = credentials.decrypt(stored_password) if stored_password else None
    params = {
        "hostname": machine["host"],
        "port": machine.get("rdp_port", 3389),
        "ignore-cert": "true",  # cert auto-signé typique sur un LAN interne
        # "any" (négociation automatique) semble raisonnable en théorie,
        # mais c'est un problème connu et documenté de FreeRDP/guacd: face
        # à un serveur qui IMPOSE NLA (le cas par défaut sur les Windows
        # modernes, exactement comme le fait mstsc), "any" échoue souvent
        # avec "Server refused connection (wrong security type?)" côté
        # guacd plutôt que de retomber correctement sur NLA — confirmé en
        # conditions réelles (voir tests/test_rdp_protocol.py). "nla" en
        # dur ici couvre le cas de très loin le plus courant ; si jamais
        # une machine plus ancienne sans NLA doit être supportée, il
        # faudra rendre ce champ configurable par machine plutôt que de
        # repasser à "any".
        "security": "nla",
    }
    if machine.get("rdp_username"):
        params["username"] = machine["rdp_username"]
    if password:
        params["password"] = password
    if machine.get("rdp_domain"):
        params["domain"] = machine["rdp_domain"]
    return params
