"""Terminal SSH dans le navigateur.

Le flux est simple:
1. Le client (xterm.js) ouvre une connexion Socket.IO et émet 'ssh_connect'
   avec l'id de la machine + les identifiants de connexion.
2. Le serveur ouvre une session Paramiko (invoke_shell) vers la machine
   cible et relaie tout ce qui en sort vers le client via 'ssh_output'.
3. Tout ce que l'utilisateur tape dans xterm.js est envoyé au serveur via
   'ssh_input', qui l'écrit directement dans le canal SSH.
"""
import threading

from flask import request
from flask_socketio import emit

import credentials
import ssh_client
from ssh_client import HostKeyChanged
from store import get_machine

# une session paramiko active par sid de socket.io
sessions = {}

# connexions en attente de confirmation suite à une alerte de clé d'hôte
# changée: {sid: {"machine_id":..., "username":..., "password":..., "new_key": <PKey>}}
pending_key_confirmation = {}


def register_ssh_handlers(socketio):

    def _attempt_connect(sid, machine, username, password):
        try:
            client = ssh_client.connect(machine, username, password)
        except HostKeyChanged as exc:
            pending_key_confirmation[sid] = {
                "machine_id": machine["id"],
                "username": username,
                "password": password,
                "new_key": exc.new_key,
            }
            socketio.emit(
                "ssh_key_mismatch",
                {
                    "message": str(exc),
                    "fingerprint": ssh_client.fingerprint(exc.new_key),
                    "key_type": exc.new_key.get_name(),
                },
                room=sid,
            )
            return
        except Exception as exc:  # noqa: BLE001
            socketio.emit("ssh_error", {"message": f"Connexion impossible: {exc}"}, room=sid)
            return

        channel = client.invoke_shell(term="xterm")
        sessions[sid] = {"client": client, "channel": channel, "sftp": None}

        def stream_output():
            try:
                while True:
                    data_out = channel.recv(4096)
                    if not data_out:
                        break
                    socketio.emit(
                        "ssh_output",
                        {"data": data_out.decode(errors="ignore")},
                        room=sid,
                    )
            except Exception:  # noqa: BLE001
                pass
            finally:
                socketio.emit("ssh_closed", {}, room=sid)

        threading.Thread(target=stream_output, daemon=True).start()
        socketio.emit("ssh_ready", {}, room=sid)

    @socketio.on("ssh_connect")
    def handle_ssh_connect(data):
        sid = request.sid
        machine_id = data.get("machine_id")

        machine = get_machine(machine_id)
        if not machine:
            emit("ssh_error", {"message": "Machine inconnue."})
            return

        # Si l'utilisateur n'a rien saisi (formulaire sauté côté client
        # car des identifiants sont mémorisés), on retombe sur les
        # identifiants stockés/chiffrés pour cette machine.
        stored = machine.get("credentials") or {}
        username = data.get("username") or stored.get("username")
        password = data.get("password")
        if not password and stored.get("password"):
            password = credentials.decrypt(stored["password"])

        if not username or not password:
            emit("ssh_error", {"message": "Identifiants requis."})
            return

        _attempt_connect(sid, machine, username, password)

    @socketio.on("ssh_trust_new_key")
    def handle_trust_new_key(_data):
        """L'utilisateur a explicitement confirmé qu'il fait confiance à
        la nouvelle clé d'hôte présentée. On la mémorise puis on relance
        la connexion (qui devrait réussir cette fois)."""
        sid = request.sid
        pending = pending_key_confirmation.pop(sid, None)
        if not pending:
            emit("ssh_error", {"message": "Rien à confirmer."})
            return

        from store import set_machine_host_key
        set_machine_host_key(
            pending["machine_id"],
            pending["new_key"].get_name(),
            pending["new_key"].get_base64(),
        )

        machine = get_machine(pending["machine_id"])
        _attempt_connect(sid, machine, pending["username"], pending["password"])

    @socketio.on("ssh_input")
    def handle_ssh_input(data):
        sid = request.sid
        session = sessions.get(sid)
        if session:
            session["channel"].send(data.get("data", ""))

    @socketio.on("ssh_resize")
    def handle_ssh_resize(data):
        sid = request.sid
        session = sessions.get(sid)
        if session:
            session["channel"].resize_pty(
                width=data.get("cols", 80), height=data.get("rows", 24)
            )

    @socketio.on("disconnect")
    def handle_disconnect():
        sid = request.sid
        pending_key_confirmation.pop(sid, None)
        session = sessions.pop(sid, None)
        if session:
            try:
                for upload in session.get("uploads", {}).values():
                    try:
                        upload["handle"].close()
                    except Exception:  # noqa: BLE001
                        pass
                if session.get("sftp"):
                    session["sftp"].close()
                session["channel"].close()
                session["client"].close()
            except Exception:  # noqa: BLE001
                pass
