"""Actions rapides sur une machine (reboot, shutdown) via une commande SSH
ponctuelle (exec_command), séparée du terminal interactif.

Gestion de sudo (Linux): exec_command() n'alloue pas de terminal (pty),
donc si `sudo` doit demander un mot de passe interactivement, ça échoue
immédiatement côté machine cible. La solution retenue ici: `sudo -S`, qui
lit le mot de passe depuis l'entrée standard plutôt que depuis un
terminal — on lui envoie donc le même mot de passe que celui utilisé pour
la connexion SSH (hypothèse: c'est aussi le mot de passe sudo de cet
utilisateur, ce qui est le cas le plus courant).

Si ce n'est pas votre cas (mot de passe sudo différent, ou pas de mot de
passe sudo du tout), la bonne pratique est de configurer NOPASSWD pour
ces commandes précises côté machine cible plutôt que de dépendre de ce
mécanisme, voir le README.

Note Windows: nécessite le serveur OpenSSH activé côté machine cible
(fonctionnalité optionnelle Windows 10/Server 2019+), avec un compte
disposant des droits nécessaires pour arrêter/redémarrer.
"""
import paramiko

import credentials
import ssh_client
from ssh_client import HostKeyChanged
from store import get_machine

# Commandes Linux SANS le préfixe sudo: il est ajouté séparément par
# _run_linux_action() pour pouvoir lui fournir le mot de passe via -S.
LINUX_COMMANDS = {
    "reboot": "reboot",
    "shutdown": "shutdown -h now",
}
WINDOWS_COMMANDS = {
    "reboot": "shutdown /r /t 0",
    "shutdown": "shutdown /s /t 0",
}

EXEC_TIMEOUT = 15


class ActionError(Exception):
    pass


class MissingCredentialsError(ActionError):
    """Cas précis où aucun identifiant valide n'est disponible (pour la
    connexion SSH, ou pour sudo côté machine cible): le client doit
    alors proposer un formulaire de saisie, contrairement aux autres
    erreurs (hôte injoignable, clé changée...) qui ne se résolvent pas
    en redemandant un mot de passe."""
    pass


def _resolve_credentials(machine, username, password):
    """Priorité: identifiants fournis dans la requête > identifiants
    mémorisés (déchiffrés) pour la machine."""
    stored = machine.get("credentials") or {}
    resolved_username = username or stored.get("username")
    resolved_password = password
    if not resolved_password and stored.get("password"):
        resolved_password = credentials.decrypt(stored["password"])
    return resolved_username, resolved_password


def _run_linux_action(client, base_command, password):
    """Exécute `base_command` via `sudo -S` en lui fournissant le mot de
    passe sur stdin. Vérifie réellement le code de sortie plutôt que de
    supposer que la commande a réussi.

    Cas particulier: pour reboot/shutdown, la machine peut couper la
    session SSH avant d'avoir eu le temps de renvoyer un code de sortie
    (c'est elle-même en train de s'éteindre). Dans ce cas précis, on
    considère que c'est un succès probable plutôt qu'une erreur.
    """
    stdin, stdout, stderr = client.exec_command(
        f"sudo -S -p '' {base_command}", timeout=EXEC_TIMEOUT
    )
    try:
        stdin.write(password + "\n")
        stdin.flush()
        stdin.channel.shutdown_write()
    except OSError:
        pass  # la commande a peut-être déjà terminé/coupé la connexion

    try:
        exit_status = stdout.channel.recv_exit_status()
        err_text = stderr.read().decode(errors="ignore").strip()
    except (EOFError, OSError):
        return  # connexion coupée avant réponse: succès probable (reboot)

    if exit_status != 0:
        lowered = err_text.lower()
        if "password" in lowered or "sorry" in lowered or "incorrect" in lowered:
            raise MissingCredentialsError(
                "Mot de passe sudo refusé sur la machine cible (le mot de "
                "passe SSH est aussi utilisé pour sudo — configurez "
                "NOPASSWD si ce n'est pas le bon mot de passe côté cible)."
            )
        raise ActionError(
            f"La commande a échoué (code {exit_status})"
            + (f": {err_text}" if err_text else ".")
        )


def _run_windows_action(client, command):
    stdin, stdout, stderr = client.exec_command(command, timeout=EXEC_TIMEOUT)
    try:
        exit_status = stdout.channel.recv_exit_status()
        err_text = stderr.read().decode(errors="ignore").strip()
    except (EOFError, OSError):
        return  # connexion coupée avant réponse: succès probable

    if exit_status != 0:
        raise ActionError(
            f"La commande a échoué (code {exit_status})"
            + (f": {err_text}" if err_text else ".")
        )


def run_action(machine_id, action, username=None, password=None):
    machine = get_machine(machine_id)
    if not machine:
        raise ActionError("Machine inconnue.")

    if action not in ("reboot", "shutdown"):
        raise ActionError(f"Action inconnue: {action}")

    if machine["os"] == "linux":
        base_command = LINUX_COMMANDS[action]
    elif machine["os"] == "windows":
        base_command = WINDOWS_COMMANDS[action]
    else:
        raise ActionError(f"OS non supporté: {machine['os']}")

    user, pwd = _resolve_credentials(machine, username, password)
    if not user or not pwd:
        raise MissingCredentialsError(
            "Identifiants requis: aucun identifiant mémorisé pour cette machine."
        )

    client = None
    try:
        client = ssh_client.connect(machine, user, pwd)
        if machine["os"] == "linux":
            _run_linux_action(client, base_command, pwd)
        else:
            _run_windows_action(client, base_command)
    except paramiko.AuthenticationException as exc:
        raise MissingCredentialsError(
            "Authentification refusée par l'hôte: identifiants incorrects."
        ) from exc
    except HostKeyChanged as exc:
        raise ActionError(
            "La clé d'hôte de cette machine a changé et n'est plus "
            "reconnue (voir message détaillé ci-dessous). Ouvrez un "
            "terminal SSH vers cette machine pour vérifier et confirmer "
            "la nouvelle clé, puis relancez cette action.\n"
            f"Détail: {exc}"
        ) from exc
    except (MissingCredentialsError, ActionError):
        raise
    except Exception as exc:  # noqa: BLE001
        raise ActionError(f"Échec de connexion: {exc}") from exc
    finally:
        if client:
            client.close()
