"""Bastion web: monitoring + accès SSH/VNC aux machines de l'inventaire.

Lancement (dev):
    python app.py

Voir README.md pour la mise en place complète (noVNC, websockify, etc).
"""
# IMPORTANT: le monkey_patch d'eventlet doit être fait avant tout autre
# import (notamment avant paramiko/socket/threading), sinon les sockets
# SSH ne sont pas coopératives et le serveur devient instable.
import eventlet
eventlet.monkey_patch()

import os

from flask import (
    Flask, render_template, redirect, url_for, request, session, abort, jsonify,
)
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename

import config
import store
import credentials
import tls
from map_image import DEFAULT_MAP_RATIO, get_image_size, validate_map_image
from monitor import start_background_monitor, get_status_snapshot
from ssh_ws import register_ssh_handlers, sessions as ssh_sessions
from sftp_ws import register_sftp_handlers
from ssh_actions import run_action, ActionError, MissingCredentialsError

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

MAPS_DIR = os.path.join(app.root_path, "static", "uploads", "maps")
ALLOWED_MAP_EXT = {"png", "jpg", "jpeg", "svg"}


def _validate_map_upload(ext, file):
    """Valide un fichier de plan de salle uploadé: extension autorisée,
    puis intégrité du fichier (voir map_image.py — aucune contrainte de
    résolution: n'importe quel ratio est accepté, voir map_view ci-dessous
    et le CSS de .map-wrap). Retourne un message d'erreur (str) si
    invalide, None sinon."""
    if ext not in ALLOWED_MAP_EXT:
        return "Format d'image non supporté (PNG, JPG ou SVG)."
    if ext == "svg":
        # Pillow ne lit pas le SVG (vectoriel, pas de pixels) — on vérifie
        # juste que le contenu ressemble à du SVG plutôt qu'à n'importe
        # quel fichier renommé avec cette extension.
        head = file.stream.read(1024)
        file.stream.seek(0)
        if b"<svg" not in head.lower():
            return "Fichier SVG illisible ou invalide."
        return None
    return validate_map_image(file.stream)


socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")
register_ssh_handlers(socketio)
register_sftp_handlers(socketio, ssh_sessions)


# --- Auth minimale (à remplacer par LDAP/SSO en prod) ------------------

def login_required(view):
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (
            request.form.get("username") == config.ADMIN_USER
            and request.form.get("password") == config.ADMIN_PASSWORD
        ):
            session["logged_in"] = True
            session["username"] = request.form.get("username")
            return redirect(request.args.get("next") or url_for("dashboard"))
        error = "Identifiants incorrects."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --- Dashboard -------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    machines = store.load_machines()
    rooms = store.load_rooms()
    statuses = get_status_snapshot()

    # Groupement par salle pour l'affichage (+ un groupe "sans salle")
    room_id_filter = request.args.get("room")
    groups = []
    for room in rooms:
        room_machines = [m for m in machines if m.get("room") == room["id"]]
        if room_id_filter and room_id_filter != room["id"]:
            continue
        groups.append({"room": room, "machines": room_machines})

    unassigned = [m for m in machines if not m.get("room")]
    if unassigned and (not room_id_filter or room_id_filter == "_none"):
        groups.append({"room": None, "machines": unassigned})

    return render_template(
        "dashboard.html",
        groups=groups,
        rooms=rooms,
        statuses=statuses,
        active_room=room_id_filter,
        total_count=len(machines),
    )


# --- SSH / VNC ---------------------------------------------------------

@app.route("/terminal/<machine_id>")
@login_required
def terminal(machine_id):
    machine = store.get_machine(machine_id)
    if not machine:
        abort(404)
    return render_template(
        "terminal.html", machine=machine,
        has_stored_creds=store.has_stored_credentials(machine),
    )


@app.route("/vnc/<machine_id>")
@login_required
def vnc(machine_id):
    machine = store.get_machine(machine_id)
    if not machine:
        abort(404)

    # L'authentification VNC se fait côté navigateur (noVNC), pas côté
    # serveur comme pour SSH: si des identifiants sont mémorisés, il faut
    # donc les déchiffrer et les transmettre à la page pour que le client
    # JS puisse les utiliser directement, plutôt que de forcer une
    # saisie manuelle à chaque fois.
    stored_vnc_password = machine.get("vnc_password")
    vnc_password = credentials.decrypt(stored_vnc_password) if stored_vnc_password else None

    return render_template(
        "vnc.html",
        machine=machine,
        vnc_ws_port=config.VNC_WS_PORT,
        vnc_ws_path=config.VNC_WS_PATH,
        vnc_username=machine.get("vnc_username") or "",
        vnc_password=vnc_password or "",
    )


@app.route("/rdp/<machine_id>")
@login_required
def rdp(machine_id):
    machine = store.get_machine(machine_id)
    if not machine or not machine.get("rdp_port"):
        abort(404)

    # Contrairement au VNC, l'authentification RDP a lieu entièrement côté
    # serveur (rdp_bridge.py les fournit à guacd) — rien à déchiffrer ni à
    # transmettre au navigateur ici. rdp_bridge.py sert ses propres
    # WebSocket (pas websockify, voir son docstring), d'où un port/chemin
    # de config séparé de celui du VNC.
    return render_template(
        "rdp.html",
        machine=machine,
        rdp_ws_port=config.RDP_WS_PORT,
        rdp_ws_path=config.RDP_WS_PATH,
    )


# --- Gestion de l'inventaire: ajout d'hôte / salle ---------------------

@app.route("/hosts/new", methods=["GET", "POST"])
@login_required
def new_host():
    rooms = store.load_rooms()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        os_type = request.form.get("os")
        host = request.form.get("host", "").strip()
        ssh_port = request.form.get("ssh_port") or 22
        vnc_port = request.form.get("vnc_port") or None
        rdp_port = request.form.get("rdp_port") or None
        room_id = request.form.get("room") or None
        remember = request.form.get("remember") == "on"
        username = request.form.get("username") or None
        password = request.form.get("password") or None
        vnc_username = request.form.get("vnc_username") or None
        vnc_password = request.form.get("vnc_password") or None
        rdp_username = request.form.get("rdp_username") or None
        rdp_password = request.form.get("rdp_password") or None
        rdp_domain = request.form.get("rdp_domain") or None
        rdp_security = request.form.get("rdp_security") or None

        if not name or not host or os_type not in ("linux", "windows"):
            return render_template(
                "host_form.html", rooms=rooms,
                error="Nom, hôte et OS sont obligatoires.",
                credentials_enabled=credentials.credentials_enabled(),
            )

        store.add_machine(
            name=name, os_type=os_type, host=host,
            ssh_port=ssh_port, vnc_port=vnc_port, rdp_port=rdp_port,
            room_id=room_id,
            username=username if remember else None,
            password=password if remember else None,
            vnc_username=vnc_username,
            vnc_password=vnc_password,
            rdp_username=rdp_username,
            rdp_password=rdp_password,
            rdp_domain=rdp_domain,
            rdp_security=rdp_security,
        )
        return redirect(url_for("dashboard"))

    return render_template(
        "host_form.html", rooms=rooms, error=None,
        credentials_enabled=credentials.credentials_enabled(),
    )


@app.route("/rooms/new", methods=["GET", "POST"])
@login_required
def new_room():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            return render_template("room_form.html", error="Le nom de la salle est obligatoire.")

        file = request.files.get("map_image")
        ext = None
        if file and file.filename:
            ext = file.filename.rsplit(".", 1)[-1].lower()
            error = _validate_map_upload(ext, file)
            if error:
                return render_template("room_form.html", error=error)

        room_id = store.add_room(name)

        if file and file.filename:
            os.makedirs(MAPS_DIR, exist_ok=True)
            filename = secure_filename(f"{room_id}.{ext}")
            file.save(os.path.join(MAPS_DIR, filename))
            store.set_room_map_image(room_id, filename)

        return redirect(url_for("map_view", room_id=room_id))

    return render_template("room_form.html", error=None)


@app.route("/hosts/<machine_id>/edit", methods=["GET", "POST"])
@login_required
def edit_host(machine_id):
    machine = store.get_machine(machine_id)
    if not machine:
        abort(404)
    rooms = store.load_rooms()

    if request.method == "POST":
        if request.form.get("delete") == "1":
            store.delete_machine(machine_id)
            return redirect(url_for("dashboard"))

        name = request.form.get("name", "").strip()
        os_type = request.form.get("os")
        host = request.form.get("host", "").strip()
        ssh_port = request.form.get("ssh_port") or 22
        vnc_port = request.form.get("vnc_port") or None
        rdp_port = request.form.get("rdp_port") or None
        room_id = request.form.get("room") or None
        remember = request.form.get("remember") == "on"
        clear_credentials = request.form.get("clear_credentials") == "on"
        username = request.form.get("username") or None
        password = request.form.get("password") or None
        vnc_username = request.form.get("vnc_username") or None
        vnc_password = request.form.get("vnc_password") or None
        clear_vnc_password = request.form.get("clear_vnc_password") == "on"
        rdp_username = request.form.get("rdp_username") or None
        rdp_password = request.form.get("rdp_password") or None
        rdp_domain = request.form.get("rdp_domain") or None
        clear_rdp_password = request.form.get("clear_rdp_password") == "on"
        rdp_security = request.form.get("rdp_security") or None

        if not name or not host or os_type not in ("linux", "windows"):
            return render_template(
                "host_form.html", rooms=rooms, machine=machine,
                error="Nom, hôte et OS sont obligatoires.",
                credentials_enabled=credentials.credentials_enabled(),
            )

        store.update_machine(
            machine_id, name=name, os_type=os_type, host=host,
            ssh_port=ssh_port, vnc_port=vnc_port, rdp_port=rdp_port,
            room_id=room_id,
            username=username if remember else None,
            password=password if remember else None,
            clear_credentials=clear_credentials,
            vnc_username=vnc_username,
            vnc_password=vnc_password,
            clear_vnc_password=clear_vnc_password,
            rdp_username=rdp_username,
            rdp_password=rdp_password,
            rdp_domain=rdp_domain,
            clear_rdp_password=clear_rdp_password,
            rdp_security=rdp_security,
        )
        return redirect(url_for("dashboard"))

    return render_template(
        "host_form.html", rooms=rooms, machine=machine, error=None,
        credentials_enabled=credentials.credentials_enabled(),
    )


@app.route("/rooms/<room_id>/edit", methods=["GET", "POST"])
@login_required
def edit_room(room_id):
    room = store.get_room(room_id)
    if not room:
        abort(404)

    if request.method == "POST":
        if request.form.get("delete") == "1":
            store.delete_room(room_id)
            return redirect(url_for("dashboard"))

        name = request.form.get("name", "").strip()
        if not name:
            return render_template(
                "room_form.html", room=room, error="Le nom de la salle est obligatoire.",
            )

        file = request.files.get("map_image")
        ext = None
        if file and file.filename:
            ext = file.filename.rsplit(".", 1)[-1].lower()
            error = _validate_map_upload(ext, file)
            if error:
                return render_template("room_form.html", room=room, error=error)

        store.update_room(room_id, name)

        if file and file.filename:
            os.makedirs(MAPS_DIR, exist_ok=True)
            filename = secure_filename(f"{room_id}.{ext}")
            # nettoie l'ancien fichier si le format d'image a changé
            if room.get("map_image") and room["map_image"] != filename:
                old_path = os.path.join(MAPS_DIR, room["map_image"])
                if os.path.exists(old_path):
                    os.remove(old_path)
            file.save(os.path.join(MAPS_DIR, filename))
            store.set_room_map_image(room_id, filename)

        return redirect(url_for("map_view", room_id=room_id))

    return render_template("room_form.html", room=room, error=None)


# --- Plan interactif (WYSIWYG) -----------------------------------------

@app.route("/map/<room_id>")
@login_required
def map_view(room_id):
    room = store.get_room(room_id)
    if not room:
        abort(404)
    machines = store.machines_in_room(room_id)
    unplaced = [m for m in machines if not m.get("position")]
    statuses = get_status_snapshot()

    # Ratio exact de l'image, injecté directement dans le CSS (voir
    # map.html) plutôt que d'attendre le chargement de l'image côté
    # navigateur pour le calculer — évite un "flash" de mauvais ratio à
    # l'affichage. Non disponible pour le SVG (voir map_image.py) : le
    # ratio par défaut s'applique le temps que le filet de sécurité JS
    # (static/js/map.js) le corrige une fois l'image chargée.
    map_ratio = DEFAULT_MAP_RATIO
    if room.get("map_image"):
        size = get_image_size(os.path.join(MAPS_DIR, room["map_image"]))
        if size:
            map_ratio = size

    return render_template(
        "map.html",
        room=room, rooms=store.load_rooms(),
        machines=machines, unplaced=unplaced,
        statuses=statuses,
        map_ratio_w=map_ratio[0], map_ratio_h=map_ratio[1],
    )


@app.route("/api/machines/<machine_id>/position", methods=["POST"])
@login_required
def api_update_position(machine_id):
    data = request.get_json(force=True) or {}
    if "x" not in data or "y" not in data:
        return jsonify({"ok": False, "error": "x/y manquants"}), 400
    store.update_machine_position(machine_id, data["x"], data["y"])
    return jsonify({"ok": True})


@app.route("/api/machines/<machine_id>/action", methods=["POST"])
@login_required
def api_machine_action(machine_id):
    data = request.get_json(force=True) or {}
    action = data.get("action")
    try:
        run_action(
            machine_id, action,
            username=data.get("username"), password=data.get("password"),
        )
        return jsonify({"ok": True})
    except MissingCredentialsError as exc:
        return jsonify({"ok": False, "error": str(exc), "needs_credentials": True}), 400
    except ActionError as exc:
        return jsonify({"ok": False, "error": str(exc), "needs_credentials": False}), 400


@app.route("/api/machines/<machine_id>/status")
@login_required
def api_machine_status(machine_id):
    """Utilisé par la page plan pour savoir si des identifiants sont
    mémorisés pour cette machine (sans jamais renvoyer le mot de passe)."""
    machine = store.get_machine(machine_id)
    if not machine:
        abort(404)
    return jsonify({"has_stored_creds": store.has_stored_credentials(machine)})


if __name__ == "__main__":
    start_background_monitor(socketio)
    # use_reloader=False: le reloader de Werkzeug relance le script dans un
    # sous-processus, ce qui pose problème avec eventlet sur Windows
    # (le port reste "already in use"). On garde debug=True pour les
    # messages d'erreur détaillés, sans le reloader.
    run_kwargs = {"host": "0.0.0.0", "port": 5000, "debug": True, "use_reloader": False}
    cert_path, key_path = tls.resolve_cert_paths()
    if cert_path:
        # Flask-SocketIO (mode eventlet) accepte directement certfile/keyfile
        # et enveloppe le socket d'écoute avec eventlet.wrap_ssl() — voir
        # tls.py pour la cohérence avec les deux autres serveurs réseau
        # (websockify, rdp_bridge.py).
        run_kwargs["certfile"] = cert_path
        run_kwargs["keyfile"] = key_path
    socketio.run(app, **run_kwargs)
