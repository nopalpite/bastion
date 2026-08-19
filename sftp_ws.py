"""Navigateur de fichiers dans le terminal web (colonne latérale façon
MobaXterm): liste, upload, téléchargement, suppression et création de
dossiers, via SFTP sur la même connexion SSH que le terminal.

Volontairement branché sur la session Paramiko déjà ouverte pour le
terminal (voir ssh_ws.py, dict `sessions`), pas sur une connexion à part:
ça évite de redemander des identifiants, et le navigateur de fichiers
n'est disponible que tant qu'une session SSH est active — cohérent avec
son rôle de "colonne à côté du terminal", pas d'un outil indépendant.

Transferts (upload ET download) limités à MAX_TRANSFER_BYTES, et
DÉCOUPÉS EN MORCEAUX de CHUNK_SIZE plutôt qu'envoyés en un seul message
websocket. C'est nécessaire, pas juste "plus propre": Flask-SocketIO
plafonne par défaut la taille d'un message à ~1 Mo
(max_http_buffer_size) — au-delà, la connexion se bloque silencieusement
plutôt que d'échouer proprement. Un fichier de quelques Mo en base64
dépasse largement cette limite en un seul message.
"""
import base64
import posixpath
import stat

from flask import request
from flask_socketio import emit

MAX_TRANSFER_BYTES = 15 * 1024 * 1024  # 15 Mo
MAX_EDIT_BYTES = 5 * 1024 * 1024  # 5 Mo — au-delà, un <textarea> devient peu maniable
CHUNK_SIZE = 256 * 1024  # 256 Ko par morceau (upload et download)


def register_sftp_handlers(socketio, sessions):
    """`sessions` est le dict partagé {sid: {"client":..., "channel":...}}
    géré par ssh_ws.py — on y ajoute des entrées "sftp" et "uploads" à la
    demande."""

    def _get_sftp(sid):
        session = sessions.get(sid)
        if not session:
            return None
        if session.get("sftp") is None:
            session["sftp"] = session["client"].open_sftp()
        return session["sftp"]

    def _get_uploads(sid):
        session = sessions.get(sid)
        if session is None:
            return None
        if "uploads" not in session:
            session["uploads"] = {}
        return session["uploads"]

    @socketio.on("sftp_list")
    def handle_sftp_list(data):
        sftp = _get_sftp(request.sid)
        if not sftp:
            emit("sftp_error", {"message": "Aucune session SSH active."})
            return

        path = data.get("path") or None
        try:
            if not path:
                path = sftp.normalize(".")
            entries = []
            for attr in sftp.listdir_attr(path):
                entries.append({
                    "name": attr.filename,
                    "is_dir": bool(attr.st_mode) and stat.S_ISDIR(attr.st_mode),
                    "size": attr.st_size,
                    "mtime": attr.st_mtime,
                })
            entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
            emit("sftp_listing", {"path": path, "entries": entries})
        except Exception as exc:  # noqa: BLE001
            emit("sftp_error", {"message": f"Impossible de lister « {path} » : {exc}"})

    @socketio.on("sftp_mkdir")
    def handle_sftp_mkdir(data):
        sftp = _get_sftp(request.sid)
        if not sftp:
            emit("sftp_error", {"message": "Aucune session SSH active."})
            return

        path = data.get("path")
        try:
            sftp.mkdir(path)
            emit("sftp_created", {"path": path})
        except Exception as exc:  # noqa: BLE001
            emit("sftp_error", {"message": f"Création du dossier impossible : {exc}"})

    @socketio.on("sftp_delete")
    def handle_sftp_delete(data):
        sftp = _get_sftp(request.sid)
        if not sftp:
            emit("sftp_error", {"message": "Aucune session SSH active."})
            return

        path = data.get("path")
        try:
            if data.get("is_dir"):
                sftp.rmdir(path)
            else:
                sftp.remove(path)
            emit("sftp_deleted", {"path": path})
        except Exception as exc:  # noqa: BLE001
            emit("sftp_error", {"message": f"Suppression impossible pour « {path} » : {exc}"})

    # --- Upload, en morceaux ------------------------------------------
    #
    # Le client envoie une série d'évènements "sftp_upload_chunk" (un par
    # morceau du fichier), chacun accusé réception via le mécanisme d'ack
    # de Socket.IO (la valeur de retour du handler devient la réponse du
    # callback côté client) — ça donne naturellement du contrôle de flux:
    # le client n'envoie le morceau suivant qu'une fois le précédent
    # confirmé écrit. Le fichier SFTP reste ouvert entre les morceaux
    # (stocké dans sessions[sid]["uploads"][upload_id]) pour écrire au
    # fil de l'eau plutôt que de tout garder en mémoire côté serveur.

    @socketio.on("sftp_upload_chunk")
    def handle_sftp_upload_chunk(data):
        sid = request.sid
        sftp = _get_sftp(sid)
        uploads = _get_uploads(sid)
        if not sftp or uploads is None:
            return {"ok": False, "error": "Aucune session SSH active."}

        upload_id = data.get("upload_id")
        path = data.get("path")
        chunk_index = data.get("chunk_index", 0)

        try:
            raw = base64.b64decode(data.get("content_base64", ""))
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "Contenu de fragment invalide."}

        upload = uploads.get(upload_id)
        if upload is None:
            if chunk_index != 0:
                return {
                    "ok": False,
                    "error": "Envoi désynchronisé (relancez l'envoi de ce fichier).",
                }
            try:
                handle = sftp.open(path, "wb")
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"Impossible d'ouvrir « {path} » en écriture : {exc}"}
            upload = {"handle": handle, "written": 0}
            uploads[upload_id] = upload

        upload["written"] += len(raw)
        if upload["written"] > MAX_TRANSFER_BYTES:
            upload["handle"].close()
            uploads.pop(upload_id, None)
            return {
                "ok": False,
                "error": f"Fichier trop volumineux (max {MAX_TRANSFER_BYTES // (1024 * 1024)} Mo).",
            }

        try:
            upload["handle"].write(raw)
        except Exception as exc:  # noqa: BLE001
            upload["handle"].close()
            uploads.pop(upload_id, None)
            return {"ok": False, "error": f"Écriture impossible : {exc}"}

        return {"ok": True}

    @socketio.on("sftp_upload_end")
    def handle_sftp_upload_end(data):
        sid = request.sid
        uploads = _get_uploads(sid)
        if uploads is None:
            return {"ok": False, "error": "Aucune session SSH active."}

        upload_id = data.get("upload_id")
        path = data.get("path")
        upload = uploads.pop(upload_id, None)
        if upload is None:
            return {"ok": False, "error": "Envoi introuvable (déjà terminé, ou expiré)."}

        try:
            upload["handle"].close()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Erreur à la finalisation de l'envoi : {exc}"}

        emit("sftp_uploaded", {"path": path})
        return {"ok": True}

    # --- Download, en morceaux -----------------------------------------
    #
    # Le serveur lit et émet le fichier morceau par morceau
    # ("sftp_download_chunk", même charge que pour l'upload) puis termine
    # par "sftp_download_end" — le client réassemble et déclenche le
    # téléchargement navigateur une fois tous les morceaux reçus.

    @socketio.on("sftp_download")
    def handle_sftp_download(data):
        sftp = _get_sftp(request.sid)
        if not sftp:
            emit("sftp_error", {"message": "Aucune session SSH active."})
            return

        path = data.get("path")
        download_id = data.get("download_id") or path
        try:
            attrs = sftp.stat(path)
            if attrs.st_size and attrs.st_size > MAX_TRANSFER_BYTES:
                emit("sftp_error", {
                    "message": f"Fichier trop volumineux pour ce mode de "
                               f"téléchargement (max {MAX_TRANSFER_BYTES // (1024 * 1024)} Mo) — "
                               f"utilisez scp/rsync depuis un terminal pour les gros fichiers.",
                })
                return

            name = posixpath.basename(path) or path
            with sftp.open(path, "rb") as f:
                chunk_index = 0
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    emit("sftp_download_chunk", {
                        "download_id": download_id,
                        "chunk_index": chunk_index,
                        "content_base64": base64.b64encode(chunk).decode(),
                    })
                    chunk_index += 1
                    socketio.sleep(0)  # laisse respirer les autres sessions entre chaque morceau

            emit("sftp_download_end", {"download_id": download_id, "path": path, "name": name})
        except Exception as exc:  # noqa: BLE001
            emit("sftp_error", {"message": f"Téléchargement impossible pour « {path} » : {exc}"})

    @socketio.on("sftp_read_file")
    def handle_sftp_read_file(data):
        """Lit un fichier pour l'éditeur en ligne. Contrairement au
        téléchargement, celui-ci reste en un seul message (l'édition est
        déjà plafonnée à MAX_EDIT_BYTES, plus petit, et il faut de toute
        façon le texte complet côté client pour l'afficher d'un coup dans
        le <textarea> — pas de bénéfice à le fragmenter ici). On exige du
        texte UTF-8 valide — un fichier binaire échoue proprement avec un
        message clair plutôt que d'afficher du charabia."""
        sftp = _get_sftp(request.sid)
        if not sftp:
            emit("sftp_error", {"message": "Aucune session SSH active."})
            return

        path = data.get("path")
        try:
            attrs = sftp.stat(path)
            if attrs.st_size and attrs.st_size > MAX_EDIT_BYTES:
                emit("sftp_error", {
                    "message": f"Fichier trop volumineux pour l'édition en ligne "
                               f"(max {MAX_EDIT_BYTES // (1024 * 1024)} Mo) — "
                               f"téléchargez-le pour l'éditer localement.",
                })
                return

            with sftp.open(path, "rb") as f:
                raw = f.read()

            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                emit("sftp_error", {
                    "message": "Ce fichier ne semble pas être du texte UTF-8 "
                               "(binaire ?) — pas éditable ici, téléchargez-le plutôt.",
                })
                return

            emit("sftp_file_text", {"path": path, "content": text})
        except Exception as exc:  # noqa: BLE001
            emit("sftp_error", {"message": f"Lecture impossible pour « {path} » : {exc}"})

    @socketio.on("sftp_write_file")
    def handle_sftp_write_file(data):
        sftp = _get_sftp(request.sid)
        if not sftp:
            emit("sftp_error", {"message": "Aucune session SSH active."})
            return

        path = data.get("path")
        content = data.get("content", "")
        try:
            raw = content.encode("utf-8")
        except Exception:  # noqa: BLE001
            emit("sftp_error", {"message": "Contenu invalide (encodage)."})
            return

        if len(raw) > MAX_EDIT_BYTES:
            emit("sftp_error", {
                "message": f"Contenu trop volumineux (max {MAX_EDIT_BYTES // (1024 * 1024)} Mo).",
            })
            return

        try:
            with sftp.open(path, "wb") as f:
                f.write(raw)
            emit("sftp_file_saved", {"path": path})
        except Exception as exc:  # noqa: BLE001
            emit("sftp_error", {"message": f"Enregistrement impossible pour « {path} » : {exc}"})
