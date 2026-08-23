"""Pont RDP, via guacd (Apache Guacamole) — même esprit que vnc_tls_bridge.py:
délègue tout le travail protocolaire compliqué (ici, RDP entier — chiffrement,
NLA, canaux virtuels, rendu bitmap...) à un composant mûr et déjà éprouvé
plutôt que de le réimplémenter, et se contente de faire le pont entre lui et
le navigateur.

Pourquoi guacd et pas un client RDP maison : implémenter RDP soi-même serait
un travail d'une toute autre ampleur que le pont VeNCrypt (RDP embarque son
propre chiffrement, la négociation NLA/CredSSP, un pipeline bitmap complexe,
des canaux virtuels...). guacd (le composant natif du projet Apache
Guacamole, qui embarque FreeRDP) fait déjà tout ça, correctement, depuis des
années — image Docker officielle multi-arch (`guacamole/guacd`), aucune
dépendance C lourde à compiler dans notre propre image. On ne prend que ce
composant-là, pas la webapp complète du projet Apache Guacamole
(`guacamole-client`, Java/Tomcat + base de données) : elle gère ses
propres utilisateurs/connexions et duplique une bonne partie de ce que
cette appli fait déjà (inventaire, auth, dashboard) — une bien plus grosse
surface de choses pouvant casser pour un besoin qui se résume à "afficher
une session RDP dans une page déjà authentifiée".

La négociation protocolaire avec guacd (encodage/décodage des instructions,
handshake select/args/connect/ready) vit dans rdp_protocol.py, PAS ici — ce
module-ci ne fait volontairement aucun import eventlet, pour rester
importable/testable tel quel (voir son docstring). Ce fichier-ci n'est que
la couche transport: il sert lui-même les connexions WebSocket du
navigateur et relaie les octets bruts vers/depuis guacd sans plus les
interpréter (le rendu du flux Guacamole, y compris les mises à jour bitmap,
est entièrement à la charge de guacamole-common-js côté navigateur).

Différence notable avec le pont VNC: l'authentification a TOUJOURS lieu ici
côté serveur (ce pont fournit les identifiants à guacd dans l'instruction
"connect"), jamais dans le navigateur — pas de mode "mot de passe demandé à
la volée" possible pour RDP, contrairement au VNC non chiffré. Les
identifiants RDP mémorisés (rdp_username/rdp_password) sont donc requis.

Pourquoi ce pont accepte lui-même les connexions WebSocket du navigateur,
contrairement au VNC qui passe par `websockify` : `Guacamole.WebSocketTunnel`
(la lib JS officielle utilisée côté navigateur, voir rdp.html) ouvre son
WebSocket avec le sous-protocole "guacamole"
(`new WebSocket(url, "guacamole")`) — `websockify`, lui, négocie ses PROPRES
sous-protocoles ("binary"/"base64") et ne connaît pas "guacamole" ; la
poignée de main WebSocket échouerait côté navigateur avant même d'atteindre
ce pont. Solution: ce pont sert lui-même les WebSocket entrants (via
`eventlet.websocket`, déjà une dépendance du projet — donc aucune nouvelle
dépendance), en acquittant explicitement le sous-protocole "guacamole".

Usage :
    python3 rdp_bridge.py
Lit machines.yaml au moment de chaque connexion, écoute les WebSocket sur
BASTION_RDP_WS_PORT (voir config.py) et route selon le paramètre "token"
de l'URL (`?token=<id_machine>`, ajouté côté navigateur via
Guacamole.Client.connect(), voir rdp.html) vers la bonne machine.
"""
# IMPORTANT: comme app.py, le monkey_patch d'eventlet doit être fait avant
# tout autre import (notamment avant socket/threading, importés
# transitivement via rdp_protocol) — sinon les sockets ouvertes vers guacd
# bloqueraient tout le processus au lieu de rester coopératives avec le
# serveur WebSocket eventlet ci-dessous, qui tourne dans le même process.
import eventlet
eventlet.monkey_patch()

from urllib.parse import parse_qs

import eventlet.wsgi
from eventlet.websocket import WebSocketWSGI

import config
import rdp_protocol
import store
from rdp_protocol import GuacamoleError

RECV_CHUNK = rdp_protocol.RECV_CHUNK


# --- Relais bidirectionnel entre le WebSocket navigateur et guacd -------
#
# Deux greenthreads, un par sens: eventlet.websocket n'offre pas de moyen
# d'attendre simultanément "prochain message WebSocket" et "prochain octet
# guacd", donc chaque sens tourne dans sa propre boucle bloquante — rendu
# coopératif entre eux par le monkey_patch d'eventlet en tête de fichier.

def _pump_guacd_to_ws(ws, guacd_sock):
    try:
        while True:
            chunk = guacd_sock.recv(RECV_CHUNK)
            if not chunk:
                break
            ws.send(chunk.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        pass
    finally:
        # Débloque la boucle ws.wait() de l'autre sens si c'est guacd qui a
        # raccroché le premier (sinon le navigateur resterait "connecté" à
        # une session déjà morte côté serveur).
        ws.close()


def bridge_connection(ws, machine):
    """Gère une connexion WebSocket entrante de bout en bout: ouvre une
    session guacd vers la vraie machine, puis relaie dans les deux sens
    tant que la session dure."""
    try:
        params = rdp_protocol.rdp_params_for_machine(machine)
        if not params.get("username") or not params.get("password"):
            raise GuacamoleError(
                "Identifiants RDP requis: aucun identifiant mémorisé pour cette "
                "machine (l'authentification a lieu côté serveur pour RDP, pas "
                "de saisie interactive possible — voir le README)."
            )
        guacd_sock, leftover = rdp_protocol.connect_via_guacd("rdp", params)
    except Exception as exc:  # noqa: BLE001
        print(f"[rdp_bridge] {machine.get('id', '?')}: {exc}")
        return

    try:
        if leftover:
            ws.send(leftover.decode("utf-8", errors="replace"))

        eventlet.spawn(_pump_guacd_to_ws, ws, guacd_sock)

        while True:
            message = ws.wait()
            if message is None:
                break
            if isinstance(message, str):
                message = message.encode("utf-8")
            guacd_sock.sendall(message)
    except Exception as exc:  # noqa: BLE001
        print(f"[rdp_bridge] {machine.get('id', '?')}: {exc}")
    finally:
        guacd_sock.close()


# --- Serveur WebSocket ----------------------------------------------------

def _handle_ws(ws):
    query = parse_qs(ws.environ.get("QUERY_STRING", ""))
    machine_id = (query.get("token") or [None])[0]
    if not machine_id:
        print("[rdp_bridge] Connexion WebSocket sans paramètre 'token', refusée.")
        return
    machine = store.get_machine(machine_id)
    if not machine or not machine.get("rdp_port"):
        print(f"[rdp_bridge] Machine inconnue ou sans port RDP configuré: {machine_id!r}")
        return
    bridge_connection(ws, machine)


def main():
    handler = WebSocketWSGI.configured(_handle_ws, supported_protocols=["guacamole"])
    listener = eventlet.listen(("0.0.0.0", config.RDP_WS_PORT))
    print(f"[rdp_bridge] écoute sur 0.0.0.0:{config.RDP_WS_PORT} "
          "(WebSocket, sous-protocole 'guacamole')")
    eventlet.wsgi.server(listener, handler, log_output=False)


if __name__ == "__main__":
    main()
