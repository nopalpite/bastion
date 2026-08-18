"""Boucle de monitoring: vérifie périodiquement si chaque machine répond
au ping ICMP (statut principal, affiché comme pastille verte/rouge), et
teste séparément la disponibilité de chaque service configuré (SSH, VNC,
RDP) pour affichage sous forme de badges. Les deux sont indépendants:
une machine peut être "up" (répond au ping) avec un service down, ou
inversement.
"""
import platform
import socket
import subprocess
import time
import threading

from store import load_machines

CHECK_INTERVAL_SECONDS = 15
CHECK_TIMEOUT_SECONDS = 2

IS_WINDOWS = platform.system().lower() == "windows"

# état partagé, protégé par un lock:
# {machine_id: {"status": "up"/"down", "latency_ms": float|None,
#               "services": {"ssh": bool, "vnc": bool, "rdp": bool},
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
    """Teste le port SSH de la machine et retourne un dict {"ssh": bool}.

    VNC et RDP ne sont VOLONTAIREMENT PAS testés ici, contrairement à une
    version précédente de ce code. Preuve concrète en usage réel: sonder
    un port VNC RealVNC toutes les 15 secondes en continu (juste ouvrir
    puis fermer la connexion TCP, sans même tenter l'authentification)
    peut déclencher la protection anti-bruteforce de RealVNC
    ("TooManySecFail"), qui blackliste alors l'IP du bastion — y compris
    pour de VRAIES tentatives de connexion légitimes juste après. Le
    monitoring finissait par se bloquer lui-même en boucle. SSH ne
    présente pas ce risque connu (une simple connexion TCP sans
    authentification n'est pas traitée comme un échec de sécurité par
    OpenSSH)."""
    services = {}

    ssh_port = machine.get("ssh_port")
    if ssh_port:
        services["ssh"] = check_port(machine["host"], ssh_port)

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
