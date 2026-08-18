"""Couche d'accès à l'inventaire (machines.yaml).

Structure du fichier:

    rooms:
      - id: salle-a
        name: "Salle serveurs A"
        map_image: salle-a.png   # optionnel, fichier dans static/uploads/maps/

    machines:
      - id: srv-linux-01
        name: "Serveur Web Linux"
        os: linux
        host: 192.168.1.10
        ssh_port: 22
        vnc_port: 5901
        room: salle-a            # optionnel
        position: {x: 30, y: 45} # optionnel, % de la largeur/hauteur du plan
        credentials:             # optionnel, mot de passe chiffré
          username: root
          password: "gAAAAA...="

Toutes les fonctions relisent/réécrivent le fichier entier: c'est
volontairement simple (pas de base de données) et suffisant pour un
inventaire de quelques dizaines/centaines de machines.
"""
import re
import threading
import unicodedata

import yaml

from config import MACHINES_FILE
import credentials

_lock = threading.Lock()


def _load():
    with open(MACHINES_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("rooms", [])
    data.setdefault("machines", [])
    return data


def _save(data):
    with open(MACHINES_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def _slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "item"


def _unique_id(base, existing_ids):
    candidate = base
    i = 2
    while candidate in existing_ids:
        candidate = f"{base}-{i}"
        i += 1
    return candidate


def _next_grid_position(data, room_id):
    """Position par défaut en grille légère pour éviter que plusieurs
    hôtes ajoutés à la suite dans la même salle se superposent tous
    exactement au même endroit. Purement indicatif: à ajuster ensuite
    par glisser-déposer."""
    count = sum(
        1 for m in data["machines"]
        if m.get("room") == room_id and m.get("position")
    )
    col = count % 4
    row = (count // 4) % 4
    return {"x": 20 + col * 20, "y": 20 + row * 20}


# --- Lecture ---------------------------------------------------------

def load_machines():
    return _load()["machines"]


def load_rooms():
    return _load()["rooms"]


def get_machine(machine_id):
    for m in load_machines():
        if m["id"] == machine_id:
            return m
    return None


def get_room(room_id):
    for r in load_rooms():
        if r["id"] == room_id:
            return r
    return None


def machines_in_room(room_id):
    return [m for m in load_machines() if m.get("room") == room_id]


def machines_without_room():
    return [m for m in load_machines() if not m.get("room")]


def has_stored_credentials(machine):
    creds = machine.get("credentials") or {}
    return bool(creds.get("username") and creds.get("password"))


# --- Écriture ---------------------------------------------------------

def add_room(name, map_image=None):
    with _lock:
        data = _load()
        existing = {r["id"] for r in data["rooms"]}
        room_id = _unique_id(_slugify(name), existing)
        data["rooms"].append({"id": room_id, "name": name, "map_image": map_image})
        _save(data)
        return room_id


def set_room_map_image(room_id, filename):
    with _lock:
        data = _load()
        for r in data["rooms"]:
            if r["id"] == room_id:
                r["map_image"] = filename
        _save(data)


def add_machine(name, os_type, host, ssh_port=22, vnc_port=None, rdp_port=None,
                 room_id=None, username=None, password=None,
                 vnc_username=None, vnc_password=None):
    with _lock:
        data = _load()
        existing = {m["id"] for m in data["machines"]}
        machine_id = _unique_id(_slugify(name), existing)

        entry = {
            "id": machine_id,
            "name": name,
            "os": os_type,
            "host": host,
            "ssh_port": int(ssh_port) if ssh_port else 22,
        }
        if vnc_port:
            entry["vnc_port"] = int(vnc_port)
            if vnc_username:
                entry["vnc_username"] = vnc_username
            encrypted_vnc_password = credentials.encrypt(vnc_password) if vnc_password else None
            if encrypted_vnc_password:
                entry["vnc_password"] = encrypted_vnc_password
        if os_type == "windows":
            entry["rdp_port"] = int(rdp_port) if rdp_port else 3389
        if room_id:
            entry["room"] = room_id
            # positionné en grille par défaut, à déplacer ensuite par
            # glisser-déposer depuis la page plan de la salle
            entry["position"] = _next_grid_position(data, room_id)

        encrypted_password = credentials.encrypt(password) if password else None
        if username and encrypted_password:
            entry["credentials"] = {"username": username, "password": encrypted_password}

        data["machines"].append(entry)
        _save(data)
        return machine_id


def update_machine_position(machine_id, x, y):
    with _lock:
        data = _load()
        for m in data["machines"]:
            if m["id"] == machine_id:
                m["position"] = {"x": round(float(x), 2), "y": round(float(y), 2)}
        _save(data)


def set_machine_room(machine_id, room_id):
    with _lock:
        data = _load()
        for m in data["machines"]:
            if m["id"] == machine_id:
                if room_id:
                    m["room"] = room_id
                else:
                    m.pop("room", None)
                m.pop("position", None)
        _save(data)


def set_machine_host_key(machine_id, key_type, key_b64):
    """Mémorise (ou remplace) la clé d'hôte SSH de référence pour une
    machine, utilisée pour détecter un changement de clé aux connexions
    suivantes (voir ssh_client.py)."""
    with _lock:
        data = _load()
        for m in data["machines"]:
            if m["id"] == machine_id:
                m["host_key"] = {"type": key_type, "key": key_b64}
        _save(data)


def update_machine(machine_id, name, os_type, host, ssh_port=22, vnc_port=None,
                    rdp_port=None, room_id=None, username=None, password=None,
                    clear_credentials=False, vnc_username=None, vnc_password=None,
                    clear_vnc_password=False):
    """Met à jour une machine existante en place (id inchangé même si le
    nom change). Les identifiants ne sont modifiés que si username+password
    sont fournis, ou effacés si clear_credentials est vrai — sinon ils
    restent tels quels. Même logique pour vnc_password/clear_vnc_password."""
    with _lock:
        data = _load()
        for m in data["machines"]:
            if m["id"] != machine_id:
                continue

            m["name"] = name
            m["os"] = os_type
            m["host"] = host
            m["ssh_port"] = int(ssh_port) if ssh_port else 22

            if vnc_port:
                m["vnc_port"] = int(vnc_port)
                if vnc_username:
                    m["vnc_username"] = vnc_username
                else:
                    m.pop("vnc_username", None)
                if clear_vnc_password:
                    m.pop("vnc_password", None)
                elif vnc_password:
                    encrypted_vnc_password = credentials.encrypt(vnc_password)
                    if encrypted_vnc_password:
                        m["vnc_password"] = encrypted_vnc_password
            else:
                m.pop("vnc_port", None)
                m.pop("vnc_username", None)
                m.pop("vnc_password", None)

            if os_type == "windows":
                m["rdp_port"] = int(rdp_port) if rdp_port else 3389
            else:
                m.pop("rdp_port", None)

            # la position n'a plus de sens si on change la machine de salle
            room_changed = room_id != m.get("room")
            if room_changed:
                m.pop("position", None)
            if room_id:
                m["room"] = room_id
                if room_changed:
                    # positionné en grille par défaut, à déplacer ensuite
                    # par glisser-déposer depuis la page plan de la salle
                    m["position"] = _next_grid_position(data, room_id)
            else:
                m.pop("room", None)

            if clear_credentials:
                m.pop("credentials", None)
            elif username and password:
                encrypted_password = credentials.encrypt(password)
                if encrypted_password:
                    m["credentials"] = {"username": username, "password": encrypted_password}
        _save(data)


def delete_machine(machine_id):
    with _lock:
        data = _load()
        data["machines"] = [m for m in data["machines"] if m["id"] != machine_id]
        _save(data)


def update_room(room_id, name):
    with _lock:
        data = _load()
        for r in data["rooms"]:
            if r["id"] == room_id:
                r["name"] = name
        _save(data)


def delete_room(room_id):
    with _lock:
        data = _load()
        data["rooms"] = [r for r in data["rooms"] if r["id"] != room_id]
        # les machines de cette salle sont détachées, pas supprimées
        for m in data["machines"]:
            if m.get("room") == room_id:
                m.pop("room", None)
                m.pop("position", None)
        _save(data)

