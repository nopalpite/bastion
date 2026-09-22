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

import ssh_client
from ssh_actions import SUDO_REJECTED_MARKERS
from ssh_client import HostKeyChanged, PrivateKeyError

POOL_SIZE = 16
COMMAND_TIMEOUT_SECONDS = 30


class _MacroTimeout(Exception):
    """Levée par le eventlet.Timeout de _run_on_machine quand une
    commande ne termine pas dans le délai imparti — une classe dédiée
    plutôt que le TimeoutError natif : ce dernier est une sous-classe
    d'OSError (depuis Python 3.3) et serait donc absorbé par erreur par
    le `except OSError` existant plus bas (écriture du mot de passe
    sudo sur stdin), masquant le timeout au lieu de le laisser remonter."""


def _prepare_sudo_command(command):
    """Si `command` commence par "sudo", insère les options -S -p ''
    juste après pour pouvoir lui fournir le mot de passe via stdin
    plutôt que via un terminal interactif (qu'exec_command n'alloue
    pas) — même technique que ssh_actions.py pour reboot/shutdown. Le
    mot de passe fourni est le mot de passe sudo dédié s'il est
    mémorisé pour cette machine, sinon le mot de passe SSH (cas le plus
    courant quand il n'y a pas d'authentification par clé ; configurez
    NOPASSWD côté cible sinon — voir _run_on_machine).

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
    try:
        username, password, pkey, sudo_password = ssh_client.resolve_stored_auth(machine)
    except PrivateKeyError as exc:
        return {
            "machine_id": machine["id"], "machine_name": machine["name"], "ok": False,
            "output": f"Clé SSH mémorisée invalide : {exc}",
        }
    except Exception as exc:  # noqa: BLE001
        # decrypt() ne rattrape que InvalidToken en interne -- une clé
        # BASTION_CREDENTIALS_KEY malformée peut lever autre chose
        # (Fernet(...) lève ValueError) : à traiter comme un échec de
        # cette seule machine, pas laisser planter tout le lancement.
        return {
            "machine_id": machine["id"], "machine_name": machine["name"], "ok": False,
            "output": f"Échec du déchiffrement des identifiants mémorisés : {exc}",
        }
    if not username or not (password or pkey):
        return {
            "machine_id": machine["id"], "machine_name": machine["name"], "ok": False,
            "output": "Aucun identifiant SSH mémorisé pour cette machine.",
        }

    prepared_command, needs_sudo_password = _prepare_sudo_command(command)
    # Le mot de passe sudo dédié prime s'il est mémorisé ; à défaut, on
    # retombe sur le mot de passe SSH (comportement historique, toujours
    # valable pour une machine sans clé — voir le docstring du module).
    effective_sudo_password = sudo_password or password
    if needs_sudo_password and not effective_sudo_password:
        return {
            "machine_id": machine["id"], "machine_name": machine["name"], "ok": False,
            "output": "Commande sudo mais aucun mot de passe sudo ni mot de passe SSH "
                      "mémorisé pour cette machine (authentification par clé seule) — "
                      "mémorisez un mot de passe sudo depuis le formulaire de l'hôte.",
        }

    client = None
    try:
        with eventlet.Timeout(timeout, _MacroTimeout):
            client = ssh_client.connect(machine, username, password, pkey=pkey, timeout=timeout)
            stdin, stdout, stderr = client.exec_command(prepared_command, timeout=timeout)
            if needs_sudo_password:
                try:
                    stdin.write(effective_sudo_password + "\n")
                    stdin.flush()
                    stdin.channel.shutdown_write()
                except OSError:
                    pass  # la commande a peut-être déjà terminé/coupé la connexion

            # Lus en parallèle de l'attente du code de sortie (deux
            # greenthreads) plutôt qu'après coup : ne lire qu'une fois
            # recv_exit_status() revenu peut bloquer indéfiniment si la
            # commande produit assez de sortie pour remplir la fenêtre de
            # flux SSH avant de se terminer -- le processus distant reste
            # alors bloqué en écriture, personne ne le lisant encore.
            # recv_exit_status() lui-même n'a aucun timeout propre (le
            # timeout de exec_command ne protège que les lectures socket,
            # pas cette attente) : le eventlet.Timeout englobant borne le
            # tout à `timeout` secondes au total.
            stdout_greenlet = eventlet.spawn(stdout.read)
            stderr_greenlet = eventlet.spawn(stderr.read)
            exit_status = stdout.channel.recv_exit_status()
            output = (
                stdout_greenlet.wait().decode(errors="ignore")
                + stderr_greenlet.wait().decode(errors="ignore")
            ).strip()

        if (
            exit_status != 0 and needs_sudo_password
            and any(marker in output.lower() for marker in SUDO_REJECTED_MARKERS)
        ):
            output += (
                "\n(mot de passe sudo probablement refusé — "
                + ("mot de passe sudo dédié mémorisé " if sudo_password
                   else "le mot de passe SSH sert aussi de mot de passe sudo ")
                + "; configurez NOPASSWD sur la machine cible si ce n'est pas le bon)"
            )
        return {
            "machine_id": machine["id"], "machine_name": machine["name"],
            "ok": exit_status == 0, "output": output,
        }
    except _MacroTimeout:
        return {
            "machine_id": machine["id"], "machine_name": machine["name"], "ok": False,
            "output": f"La commande n'a pas terminé dans le délai imparti ({timeout}s).",
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
