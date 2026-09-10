"""Exécution d'une macro (commande SSH prédéfinie, voir macro_store.py)
sur un pool de machines choisi par l'utilisateur (page /macros/<id>/run).

Utilise directement les identifiants SSH déjà mémorisés pour chaque
machine (voir store.py/credentials.py) — aucune saisie de mot de passe
au lancement : avec potentiellement des dizaines de machines dans le
pool, souvent avec des identifiants différents les uns des autres, un
formulaire "un seul mot de passe pour tout le pool" n'aurait pas de
sens. Une machine sans identifiants mémorisés est donc simplement
signalée en échec pour cette exécution (pas bloquant pour les autres).

Contrairement à ssh_actions.py (reboot/shutdown : commandes fixes,
connues, gérées spécifiquement par OS avec sudo -S), une macro est une
commande arbitraire tapée par l'utilisateur, envoyée telle quelle via
exec_command — pas de gestion sudo automatique, pas de distinction
Linux/Windows : si le parc ciblé est mixte, prévoir des macros séparées
par OS.

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

    client = None
    try:
        client = ssh_client.connect(machine, username, password, timeout=timeout)
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_status = stdout.channel.recv_exit_status()
        output = (
            stdout.read().decode(errors="ignore") + stderr.read().decode(errors="ignore")
        ).strip()
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
