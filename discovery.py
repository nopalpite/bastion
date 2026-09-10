"""Découverte réseau (page /discover) : trouve les machines déjà présentes
sur une plage IP pour les ajouter facilement à l'inventaire, plutôt que de
les saisir une par une à la main.

Conçu pour rester **discret côté réseau**, à la demande explicite de
l'utilisateur après un incident réel documenté dans ce projet (voir le
docstring de vnc_tls_bridge.probe_available et monitor._check_services) :
- Un simple ping (même mécanisme que monitor.ping_host, rien de nouveau).
- Seulement 3 ports vérifiés par machine (22, 5900, 5901) — jamais un
  scan de plage de ports.
- Plage bornée à 254 adresses (/24) — refusée au-delà plutôt que de
  laisser scanner un /16 par erreur.
- Concurrence bornée (eventlet.GreenPool) plutôt que tout lancer d'un
  coup — voir run_discovery.
- Déclenché uniquement à la demande depuis /discover, jamais en tâche de
  fond ni automatiquement (contrairement à monitor.py).

Réutilise les primitives déjà existantes et déjà jugées sûres
(monitor.ping_host/check_port, vnc_tls_bridge.probe_available) plutôt que
d'en réinventer — mêmes garanties, pas de nouveau code réseau à auditer.
"""
import ipaddress
import socket
import struct

import eventlet

import monitor
import vnc_tls_bridge

MAX_HOSTS = 254
VNC_PORTS_TO_CHECK = (5900, 5901)
POOL_SIZE = 32

# Résolution mDNS (voir _mdns_reverse_lookup) — adresse de groupe standard
# du protocole (RFC 6762). Nom de module plutôt que constante en dur dans
# la fonction pour rester monkeypatchable par les tests (voir
# tests/test_discovery.py, qui pointe ceci vers un faux répondeur local
# plutôt que le vrai groupe multicast).
_MDNS_ADDR = ("224.0.0.251", 5353)
_MDNS_TIMEOUT = 0.6
_DNS_TYPE_PTR = 12
_DNS_CLASS_IN = 1


class DiscoveryError(Exception):
    """Plage IP invalide ou trop grande (voir parse_hosts)."""


def guess_local_cidr():
    """Devine un point de départ raisonnable pour le champ de plage IP du
    formulaire : l'IP locale principale de l'hôte (celle utilisée pour le
    trafic sortant — avec network_mode: host, c'est la vraie IP de la
    machine physique, pas celle d'un conteneur isolé), complétée par un
    masque /24 (le découpage de loin le plus courant sur un réseau
    domestique/PME). Une valeur de départ à corriger à la main si le vrai
    découpage diffère, pas une détection garantie exacte — retourne None
    si indétectable plutôt que de lever une erreur (champ juste vide).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # connect() sur UDP ne fait qu'une résolution de route locale, il
        # n'envoie aucun paquet — 8.8.8.8 n'est qu'une destination
        # arbitraire, inutile qu'elle soit réellement joignable.
        sock.connect(("8.8.8.8", 80))
        local_ip = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()

    try:
        network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    except ValueError:
        return None
    return str(network)


def parse_hosts(cidr):
    """Valide la plage et retourne la liste des adresses hôte (str) à
    scanner. Lève DiscoveryError (pas une exception réseau) sur une plage
    invalide ou trop grande — à afficher tel quel à l'utilisateur."""
    try:
        network = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError as exc:
        raise DiscoveryError(f"Plage invalide : {exc}") from exc

    hosts = [str(h) for h in network.hosts()]
    if not hosts:
        raise DiscoveryError("Cette plage ne contient aucune adresse hôte.")
    if len(hosts) > MAX_HOSTS:
        raise DiscoveryError(
            f"Plage trop grande ({len(hosts)} adresses, {MAX_HOSTS} maximum "
            "— un /24 ou plus petit). Scannez par morceaux si besoin."
        )
    return hosts


def _read_dns_name(data, pos):
    """Lit un nom DNS encodé (labels préfixés par leur longueur, terminé
    par un octet nul) à partir de `pos`, avec gestion de la compression
    par pointeur (RFC 1035 §4.1.4 — les deux bits de poids fort de
    l'octet de "longueur" à 1 indiquent un pointeur vers une position
    antérieure du paquet plutôt qu'un label littéral). Très utilisée dans
    les réponses mDNS réelles pour ne pas répéter le nom de la question.

    Retourne (nom_complet, position juste après cette occurrence dans le
    paquet) — cette position s'arrête au premier pointeur rencontré
    (2 octets), PAS à la fin du nom pointé, puisque le paquet continue
    juste après le pointeur, pas après la cible du pointeur."""
    labels = []
    pos_after = None
    hops = 0
    while True:
        if pos >= len(data):
            raise ValueError("Nom DNS tronqué")
        length = data[pos]
        if length == 0:
            pos += 1
            if pos_after is None:
                pos_after = pos
            break
        if length & 0xC0 == 0xC0:
            if pos + 2 > len(data):
                raise ValueError("Pointeur de compression DNS tronqué")
            pointer = struct.unpack(">H", data[pos:pos + 2])[0] & 0x3FFF
            if pos_after is None:
                pos_after = pos + 2
            hops += 1
            if hops > 20:  # boucle de pointeurs corrompue/malveillante
                raise ValueError("Trop de sauts de compression DNS")
            pos = pointer
            continue
        pos += 1
        labels.append(data[pos:pos + length].decode("ascii", errors="replace"))
        pos += length
    return ".".join(labels), pos_after


def _build_ptr_query(ip):
    """Construit une requête DNS "PTR" pour l'adresse inversée
    (ex: 20.1.168.192.in-addr.arpa pour 192.168.1.20), format standard
    aussi utilisé par mDNS (RFC 6762). QCLASS avec le bit "QU" (0x8000,
    RFC 6762 §5.4) demande une réponse unicast directement vers nous —
    pas besoin de rejoindre le groupe multicast pour l'écouter."""
    labels = ip.split(".")[::-1] + ["in-addr", "arpa"]
    qname = b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels) + b"\x00"
    header = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)  # 1 question, rien d'autre
    question = qname + struct.pack(">HH", _DNS_TYPE_PTR, 0x8000 | _DNS_CLASS_IN)
    return header + question


def _parse_ptr_response(data):
    """Extrait le nom cible du premier enregistrement PTR d'une réponse
    DNS/mDNS, ou None si le paquet est trop court, malformé, ou ne
    contient aucun PTR (ex: type de requête différent, hôte inconnu)."""
    if len(data) < 12:
        return None
    try:
        _id, _flags, qdcount, ancount, _nscount, _arcount = struct.unpack(">HHHHHH", data[:12])
        pos = 12
        for _ in range(qdcount):
            _name, pos = _read_dns_name(data, pos)
            pos += 4  # QTYPE + QCLASS de la question
        for _ in range(ancount):
            _name, pos = _read_dns_name(data, pos)
            if pos + 10 > len(data):
                return None
            rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", data[pos:pos + 10])
            pos += 10
            if rtype == _DNS_TYPE_PTR:
                target, _ = _read_dns_name(data, pos)
                return target.rstrip(".") or None
            pos += rdlength
    except (struct.error, ValueError, IndexError):
        return None
    return None


def _mdns_reverse_lookup(ip, timeout=_MDNS_TIMEOUT):
    """Interroge directement l'appareil (mDNS/Avahi/Bonjour, RFC 6762)
    pour son vrai nom d'hôte configuré, plutôt que de dépendre du DNS/
    DHCP du routeur — celui-ci n'a souvent qu'un nom générique attribué
    automatiquement pour la même IP, pas le vrai nom de la machine (cas
    réel observé : une Raspberry Pi nommée "rpi-3-bureau" ressortait
    comme "ah-ade980" via le DNS du routeur). Implémenté à la main
    (paquet DNS brut sur UDP 5353) plutôt que d'ajouter une dépendance
    comme zeroconf — même esprit que le protocole RFB dans
    vnc_tls_bridge.py. Retourne None si l'appareil ne répond pas (mDNS
    désactivé, Windows sans Bonjour...), pas une erreur : le signal pour
    retomber sur le DNS classique (voir _reverse_dns)."""
    packet = _build_ptr_query(ip)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, _MDNS_ADDR)
        data, _addr = sock.recvfrom(4096)
    except OSError:
        return None
    finally:
        sock.close()
    return _parse_ptr_response(data)


def _strip_local_suffix(hostname):
    """Les noms mDNS (RFC 6762 §3) se terminent systématiquement par
    ".local" — un détail du protocole, pas une information utile pour
    nommer la machine dans l'inventaire, donc retiré ici plutôt que de le
    laisser fuiter jusqu'au nom pré-rempli dans le formulaire d'ajout."""
    if hostname and hostname.lower().endswith(".local"):
        return hostname[: -len(".local")]
    return hostname


def _reverse_dns(ip):
    hostname = _mdns_reverse_lookup(ip)
    if hostname:
        return _strip_local_suffix(hostname)
    try:
        hostname, _aliases, _addrs = socket.gethostbyaddr(ip)
        return _strip_local_suffix(hostname)
    except (socket.herror, socket.gaierror, OSError):
        return None


def discover_host(ip, timeout=monitor.CHECK_TIMEOUT_SECONDS):
    """Sonde une seule IP : ping, puis (si elle répond) hostname + SSH +
    VNC. Retourne None si l'hôte ne répond pas au ping — inutile
    d'encombrer les résultats avec des adresses silencieuses."""
    up, _latency = monitor.ping_host(ip, timeout=timeout)
    if not up:
        return None

    ssh = monitor.check_port(ip, 22, timeout=timeout)

    vnc_port = None
    for port in VNC_PORTS_TO_CHECK:
        if not monitor.check_port(ip, port, timeout=timeout):
            continue
        if vnc_tls_bridge.probe_available({"host": ip, "vnc_port": port}, timeout=timeout):
            vnc_port = port
            break

    return {"ip": ip, "hostname": _reverse_dns(ip), "ssh": ssh, "vnc_port": vnc_port}


def run_discovery(cidr, pool_size=POOL_SIZE):
    """Scanne toute la plage et retourne la liste des machines qui ont
    répondu, triée par IP. Concurrence bornée par pool_size (eventlet
    déjà rendu coopératif pour subprocess/socket par app.py, voir le
    docstring du module) plutôt que de lancer les 254 sondes d'un coup."""
    hosts = parse_hosts(cidr)
    pool = eventlet.GreenPool(pool_size)
    results = [r for r in pool.imap(discover_host, hosts) if r is not None]
    results.sort(key=lambda h: ipaddress.ip_address(h["ip"]))
    return results
