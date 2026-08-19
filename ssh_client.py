"""Connexion SSH avec épinglage de la clé d'hôte (host key pinning).

Paramiko, utilisé naïvement avec AutoAddPolicy, accepte n'importe quelle
clé d'hôte sans jamais la comparer à une connexion précédente. Cela
n'offre aucune protection: si la clé d'une machine change (MITM, ou
simplement machine réinstallée), rien ne le détecte.

Ce module implémente à la place un modèle "Trust On First Use" (TOFU):
  - 1ère connexion à une machine: la clé présentée est acceptée et
    enregistrée dans l'inventaire (machines.yaml, champ `host_key`).
  - connexions suivantes: la clé présentée doit correspondre exactement
    à celle enregistrée. Si elle diffère, HostKeyChanged est levée
    plutôt que de se connecter silencieusement.
"""
import base64

import paramiko

from store import set_machine_host_key

KEY_CLASSES = {
    "ssh-rsa": paramiko.RSAKey,
    "ssh-ed25519": paramiko.Ed25519Key,
    "ecdsa-sha2-nistp256": paramiko.ECDSAKey,
    "ecdsa-sha2-nistp384": paramiko.ECDSAKey,
    "ecdsa-sha2-nistp521": paramiko.ECDSAKey,
}


class HostKeyChanged(Exception):
    """Levée quand la clé présentée par l'hôte ne correspond pas à celle
    mémorisée. Porte la nouvelle clé pour permettre à l'appelant de
    proposer à l'utilisateur de lui faire confiance explicitement."""

    def __init__(self, new_key, message):
        super().__init__(message)
        self.new_key = new_key


class _TOFUPolicy(paramiko.MissingHostKeyPolicy):
    """Accepte une clé jamais vue (aucune clé mémorisée pour cette
    machine) et la garde en mémoire pour que connect() la persiste."""

    def __init__(self):
        self.seen_key = None

    def missing_host_key(self, client, hostname, key):
        self.seen_key = key  # accepté, à enregistrer ensuite comme référence


def fingerprint(key):
    """Empreinte lisible d'une clé, ex: 'a1:b2:c3:...'."""
    if key is None:
        return None
    return ":".join(f"{b:02x}" for b in key.get_fingerprint())


def _deserialize_key(key_type, key_b64):
    key_cls = KEY_CLASSES.get(key_type)
    if not key_cls:
        return None
    return key_cls(data=base64.b64decode(key_b64))


def connect(machine, username, password, timeout=6):
    """Ouvre une connexion SSH vers `machine` en vérifiant/mémorisant sa
    clé d'hôte. Lève HostKeyChanged si la clé présentée diffère de celle
    mémorisée précédemment."""
    client = paramiko.SSHClient()
    stored = machine.get("host_key")
    tofu = None

    if stored:
        key_obj = _deserialize_key(stored["type"], stored["key"])
        if key_obj:
            client.get_host_keys().add(machine["host"], stored["type"], key_obj)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        tofu = _TOFUPolicy()
        client.set_missing_host_key_policy(tofu)

    try:
        client.connect(
            hostname=machine["host"],
            port=machine.get("ssh_port", 22),
            username=username,
            password=password,
            timeout=timeout,
        )
    except paramiko.BadHostKeyException as exc:
        raise HostKeyChanged(
            # BadHostKeyException stocke la clé nouvellement présentée dans
            # .key (pas .got_key malgré le nom du paramètre du constructeur
            # paramiko) et l'ancienne clé mémorisée dans .expected_key.
            exc.key,
            "La clé d'hôte présentée par cette machine a changé depuis la "
            "dernière connexion mémorisée. Cela peut être normal (OS "
            "réinstallé) ou le signe d'une interception (MITM) — à "
            "vérifier avant de faire confiance à la nouvelle clé.",
        ) from exc

    if tofu and tofu.seen_key:
        set_machine_host_key(machine["id"], tofu.seen_key.get_name(), tofu.seen_key.get_base64())

    return client
