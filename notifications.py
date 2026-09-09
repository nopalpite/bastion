"""Notification de changement d'état (voir monitor.py) : envoie un message
via un webhook générique (Slack/Discord/Mattermost...) quand une machine
passe "up" <-> "down" — module stdlib `urllib.request` uniquement, aucune
nouvelle dépendance pour un simple POST JSON.

Un seul format de webhook à gérer plutôt qu'une intégration par service :
le payload envoie à la fois "text" (Slack/Mattermost) et "content"
(Discord) — chaque service ignore simplement le champ qu'il ne reconnaît
pas, ça évite de détecter/configurer un "type" de webhook séparément.
"""
import json
import urllib.error
import urllib.request

import config

TIMEOUT_SECONDS = 5


def send(message):
    """Envoie `message` au webhook configuré (BASTION_NOTIFY_WEBHOOK_URL) —
    ne fait rien si aucun n'est configuré. Échec non bloquant : un webhook
    injoignable ou une URL mal configurée ne doit jamais faire planter
    l'appelant (voir monitor.py, qui ne doit pas interrompre sa boucle de
    surveillance pour ça)."""
    url = config.NOTIFY_WEBHOOK_URL
    if not url:
        return

    body = json.dumps({"text": message, "content": message}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[notifications] Échec de l'envoi du webhook: {exc}")
