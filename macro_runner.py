"""Exécution d'une macro (commande SSH prédéfinie, voir macro_store.py)
sur un pool de machines choisi par l'utilisateur (page /macros/<id>/run).

Utilise directement les identifiants SSH déjà mémorisés pour chaque
machine (voir store.py/credentials.py) — aucune saisie de mot de passe
au lancement : avec potentiellement des dizaines de machines dans le
pool, souvent avec des identifiants différents les uns des autres, un
formulaire "un seul mot de passe pour tout le pool" n'aurait pas de
sens. Une machine sans identifiants mémorisés est donc simplement
signalée en échec pour cette exécution (pas bloquant pour les autres).

Contrairement à ssh_actions.py (reboot/shutdown : commandes fixes et
connues), une macro est une commande arbitraire tapée par l'utilisateur
— pas de distinction Linux/Windows : si le parc ciblé est mixte,
prévoir des macros séparées par OS. Une commande commençant par "sudo"
bénéficie en revanche de la même technique que ssh_actions.py (voir
_prepare_sudo_command) : sudo ne peut pas demander son mot de passe de
façon interactive via exec_command (pas de pty alloué), donc on le lui
fournit nous-même sur stdin.

Concurrence bornée via eventlet.GreenPool (même mécanisme que
discovery.py) plutôt qu'une exécution séquentielle ou un fan-out
illimité.
"""
import eventlet
import paramiko

import credentials
import ssh_client
from ssh_client import HostKeyChanged

POOL_SIZE = 16
COMMAND_TIMEOUT_SECONDS = 30

# Sous-chaînes typiques d'un refus sudo (mot de passe incorrect ou compte
# non autorisé) dans stderr — voir ssh_actions._run_linux_action, même
# liste, pour transformer un simple "code de sortie != 0" en message
# compréhensible plutôt que de laisser deviner.
_SUDO_REJECTED_MARKERS = ("password", "sorry", "incorrect")


def _prepare_sudo_command(command):
    """Si `command` commence par "sudo", insère les options -S -p ''
    juste après pour pouvoir lui fournir le mot de passe via stdin
    plutôt que via un terminal interactif (qu'exec_command n'alloue
    pas) — même technique et même hypothèse que ssh_actions.py pour
    reboot/shutdown : le mot de passe SSH sert aussi de mot de passe
    sudo (cas le plus courant ; configurez NOPASSWD côté cible sinon).

    Un "sudo" plus loin dans la commande (après un &&, par exemple)
    n'est pas détecté — dans ce cas, écrivez plutôt des macros séparées
    commençant chacune par sudo.

    Retourne (commande_préparée, mot_de_passe_a_fournir: bool)."""
    stripped = command.strip()
    if stripped == "sudo":
        return "sudo -S -p ''", True
    if stripped.startswith("sudo "):
        return "sudo -S -p '' " + stripped[len("sudo "):], True
    return command, False


def _run_on_machine(machine, command, timeout):
    """Exécute `command` sur une machine et retourne un résultat — ne
    lève jamais : toute erreur (identifiants manquants, connexion
    refusée, clé d'hôte changée...) est retournée comme un résultat en
    échec, pour ne jamais interrompre l'exécution des autres machines
    du pool."""
    stored = machine.get("credentials") or {}
    username = stored.get("username")
    password = credentials.decrypt(stored["password"]) if stored.get("password") else None
    if not username or not password:
        return {
            "machine_id": machine["id"], "machine_name": machine["name"], "ok": False,
            "output": "Aucun identifiant SSH mémorisé pour cette machine.",
        }

    prepared_command, needs_sudo_password = _prepare_sudo_command(command)

    client = None
    try:
        client = ssh_client.connect(machine, username, password, timeout=timeout)
        stdin, stdout, stderr = client.exec_command(prepared_command, timeout=timeout)
        if needs_sudo_password:
            try:
                stdin.write(password + "\n")
                stdin.flush()
                stdin.channel.shutdown_write()
            except OSError:
                pass  # la commande a peut-être déjà terminé/coupé la connexion
        exit_status = stdout.channel.recv_exit_status()
        output = (
            stdout.read().decode(errors="ignore") + stderr.read().decode(errors="ignore")
        ).strip()
        if (
            exit_status != 0 and needs_sudo_password
            and any(marker in output.lower() for marker in _SUDO_REJECTED_MARKERS)
        ):
            output += (
                "\n(mot de passe sudo probablement refusé — le mot de passe SSH "
                "sert aussi de mot de passe sudo ; configurez NOPASSWD sur la "
                "machine cible si ce n'est pas le bon)"
            )
        return {
            "machine_id": machine["id"], "machine_name": machine["name"],
            "ok": exit_status == 0, "output": output,
        }
    except paramiko.AuthenticationException:
        return {
            "machine_id": machine["id"], "machine_name": machine["name"], "ok": False,
            "output": "Authentification refusée par l'hôte (identifiants incorrects).",
        }
    except HostKeyChanged:
        return {
            "machine_id": machine["id"], "machine_name": machine["name"], "ok": False,
            "output": "La clé d'hôte a changé — vérifiez/confirmez-la depuis le "
                      "terminal SSH avant de relancer la macro.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "machine_id": machine["id"], "machine_name": machine["name"], "ok": False,
            "output": f"Échec : {exc}",
        }
    finally:
        if client:
            client.close()


def run_macro(command, machines, timeout=COMMAND_TIMEOUT_SECONDS, pool_size=POOL_SIZE):
    """Exécute `command` sur chaque machine de `machines` (liste de dicts,
    voir store.py), en parallèle borné. Retourne les résultats dans le
    même ordre que `machines`."""
    pool = eventlet.GreenPool(pool_size)
    return list(pool.imap(lambda m: _run_on_machine(m, command, timeout), machines))
