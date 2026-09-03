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

import eventlet

import monitor
import vnc_tls_bridge

MAX_HOSTS = 254
VNC_PORTS_TO_CHECK = (5900, 5901)
POOL_SIZE = 32


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


def _reverse_dns(ip):
    try:
        hostname, _aliases, _addrs = socket.gethostbyaddr(ip)
        return hostname
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
