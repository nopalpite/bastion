"""Boucle de monitoring: vérifie périodiquement si chaque machine répond
au ping ICMP (statut principal, affiché comme pastille verte/rouge), et
teste séparément la disponibilité de chaque service configuré (SSH, VNC)
pour affichage sous forme de badges. Les deux sont indépendants: une
machine peut être "up" (répond au ping) avec un service down, ou
inversement.
"""
import platform
import socket
import subprocess
import threading
import time

import vnc_tls_bridge
from store import load_machines

CHECK_INTERVAL_SECONDS = 15
CHECK_TIMEOUT_SECONDS = 2

IS_WINDOWS = platform.system().lower() == "windows"

# état partagé, protégé par un lock:
# {machine_id: {"status": "up"/"down", "latency_ms": float|None,
#               "services": {"ssh": bool, "vnc": bool},
#               "checked_at": float}}
status_lock = threading.Lock()
status_store = {}


def ping_host(host, timeout=CHECK_TIMEOUT_SECONDS):
    """Ping ICMP via la commande système `ping` plutôt qu'un socket brut
    en Python: ça évite d'avoir besoin des droits root/CAP_NET_RAW côté
    processus Python (le binaire système, lui, dispose généralement déjà
    des droits nécessaires). Retourne (up: bool, latency_ms: float|None).
    """
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(int(timeout)), host]

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout + 1,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False, None

    if result.returncode == 0:
        latency_ms = (time.monotonic() - start) * 1000
        return True, round(latency_ms, 1)
    return False, None


def check_port(host, port, timeout=CHECK_TIMEOUT_SECONDS):
    """Retourne True si une connexion TCP au port aboutit."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _check_services(machine):
    """Teste les services configurés de la machine (SSH, VNC) et retourne
    un dict {"ssh": bool, "vnc": bool}.

    VNC a un historique ici: une version précédente sondait ce port en
    continu et ça avait fini par déclencher la protection anti-bruteforce
    de RealVNC Server ("TooManySecFail"), blacklistant l'IP du bastion —
    y compris pour de VRAIES tentatives de connexion juste après. D'où le
    retrait complet à l'époque. Reconfirmé depuis via la doc officielle
    RealVNC (paramètre BlacklistThreshold, help.realvnc.com): ce compteur
    ne réagit qu'à des tentatives d'AUTHENTIFICATION ratées ("ignored if
    Authentication is set to None"), pas à une connexion TCP suivie d'une
    lecture de la poignée de main — avec le recul, la cause la plus
    probable de l'incident était les nombreux essais de connexion
    manuels ratés (mauvais mot de passe) pendant les tests de l'époque,
    pas le sondage automatique lui-même. vnc_tls_bridge.probe_available()
    s'arrête donc volontairement avant tout choix de type de sécurité ou
    tentative d'authentification (voir son docstring pour le détail). À
    surveiller malgré tout en usage réel: CHECK_INTERVAL_SECONDS est le
    seul réglage à changer si un doute réapparaît."""
    services = {}

    ssh_port = machine.get("ssh_port")
    if ssh_port:
        services["ssh"] = check_port(machine["host"], ssh_port)

    vnc_port = machine.get("vnc_port")
    if vnc_port:
        services["vnc"] = vnc_tls_bridge.probe_available(machine, timeout=CHECK_TIMEOUT_SECONDS)

    return services


def _check_machine(machine):
    up, latency = ping_host(machine["host"])
    services = _check_services(machine)
    return {
        "status": "up" if up else "down",
        "latency_ms": latency,
        "services": services,
        "checked_at": time.time(),
    }


def run_checks_once():
    """Effectue un tour de vérification de toutes les machines et met à jour status_store."""
    results = {}
    for machine in load_machines():
        results[machine["id"]] = _check_machine(machine)
    with status_lock:
        status_store.update(results)
    return results


def get_status_snapshot():
    with status_lock:
        return dict(status_store)


def start_background_monitor(socketio):
    """Lance la boucle de monitoring dans un thread de fond et pousse les
    mises à jour aux clients via l'évènement Socket.IO 'status_update'."""

    def loop():
        while True:
            results = run_checks_once()
            socketio.emit("status_update", results)
            time.sleep(CHECK_INTERVAL_SECONDS)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
