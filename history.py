"""Historique de disponibilité des machines (page /stats) : un
enregistrement par machine à chaque passage de monitor.py (~15s), stocké
dans un fichier **SQLite séparé** de machines.yaml (module `sqlite3` de la
stdlib, aucune nouvelle dépendance) — un usage très différent d'un
inventaire édité à la main : ce fichier grossit en continu puis se purge
automatiquement (voir purge_old_entries), alors que machines.yaml est
petit et réécrit intégralement à chaque modification (voir store.py).

La rétention (nombre de jours conservés) est elle-même stockée dans ce
fichier (table "settings", clé "retention_days") plutôt que dans
machines.yaml : c'est un réglage qui ne concerne que cet historique,
modifiable depuis la page /stats — pas une caractéristique de
l'inventaire.
"""
import os
import sqlite3
import time

import config

DB_FILE = os.path.join(config.DATA_DIR, "history.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    machine_id TEXT NOT NULL,
    checked_at REAL NOT NULL,
    status TEXT NOT NULL,
    latency_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_checks_machine_time ON checks(machine_id, checked_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL,
    protocol TEXT NOT NULL,
    source_ip TEXT,
    started_at REAL NOT NULL,
    ended_at REAL
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
"""


def _connect():
    os.makedirs(os.path.dirname(DB_FILE) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.executescript(_SCHEMA)
    return conn


def record_check(machine_id, status, latency_ms, checked_at=None):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO checks (machine_id, checked_at, status, latency_ms) "
            "VALUES (?, ?, ?, ?)",
            (machine_id, checked_at if checked_at is not None else time.time(),
             status, latency_ms),
        )


def get_retention_days():
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'retention_days'"
        ).fetchone()
    return int(row[0]) if row else config.HISTORY_RETENTION_DAYS_DEFAULT


def set_retention_days(days):
    days = int(days)
    if days < 1:
        raise ValueError("La rétention doit être d'au moins 1 jour.")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('retention_days', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(days),),
        )
    return days


def purge_old_entries(retention_days=None):
    """Supprime les enregistrements (vérifications ET sessions, même
    rétention pour les deux plutôt qu'un second réglage séparé) plus
    vieux que la rétention configurée (ou explicitement fournie), et
    retourne le nombre total de lignes supprimées — utilisée à la fois
    par la purge périodique automatique (monitor.py) et le bouton
    "Purger maintenant" de la page /stats."""
    days = retention_days if retention_days is not None else get_retention_days()
    cutoff = time.time() - days * 86400
    with _connect() as conn:
        checks_cursor = conn.execute("DELETE FROM checks WHERE checked_at < ?", (cutoff,))
        sessions_cursor = conn.execute("DELETE FROM sessions WHERE started_at < ?", (cutoff,))
        return checks_cursor.rowcount + sessions_cursor.rowcount


def get_uptime_percentage(machine_id, since_seconds):
    """Pourcentage de vérifications "up" sur les `since_seconds` dernières
    secondes, ou None si aucune donnée sur cette période (plutôt que 0%,
    qui laisserait croire à une vraie période d'indisponibilité)."""
    cutoff = time.time() - since_seconds
    with _connect() as conn:
        total, up = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status = 'up' THEN 1 ELSE 0 END) "
            "FROM checks WHERE machine_id = ? AND checked_at >= ?",
            (machine_id, cutoff),
        ).fetchone()
    if not total:
        return None
    return round(100 * up / total, 1)


def get_timeline(machine_id, since_seconds, buckets=60):
    """Découpe les `since_seconds` dernières secondes en `buckets`
    segments égaux et renvoie, pour chacun, le taux de disponibilité
    observé (None si aucune donnée dans ce segment) — pour la frise
    visuelle de la page /stats. Agrégation faite en SQL (GROUP BY) plutôt
    que de ramener potentiellement des dizaines de milliers de lignes en
    Python pour les faire soi-même."""
    since = time.time() - since_seconds
    bucket_seconds = since_seconds / buckets

    with _connect() as conn:
        rows = conn.execute(
            "SELECT CAST((checked_at - ?) / ? AS INTEGER) AS bucket, "
            "COUNT(*), SUM(CASE WHEN status = 'up' THEN 1 ELSE 0 END) "
            "FROM checks WHERE machine_id = ? AND checked_at >= ? "
            "GROUP BY bucket",
            (since, bucket_seconds, machine_id, since),
        ).fetchall()

    # CAST(...) peut renvoyer `buckets` pile (arrondi flottant sur un
    # contrôle très récent) au lieu de buckets-1 : on regroupe ce
    # débordement dans le dernier segment plutôt que de le perdre.
    by_bucket = {}
    for bucket, total, up in rows:
        idx = min(int(bucket), buckets - 1)
        prev_total, prev_up = by_bucket.get(idx, (0, 0))
        by_bucket[idx] = (prev_total + total, prev_up + up)

    timeline = []
    for i in range(buckets):
        if i in by_bucket:
            total, up = by_bucket[i]
            timeline.append(round(100 * up / total, 1))
        else:
            timeline.append(None)
    return timeline


def get_latency_timeline(machine_id, since_seconds, buckets=60):
    """Comme get_timeline() ci-dessus, mais pour la latence moyenne (ms)
    plutôt que le taux de disponibilité — courbe de latence de la page
    /stats. AVG(latency_ms) ignore nativement les lignes NULL
    (vérifications "down", sans latence) : un segment sans aucune
    vérification "up" est None (pas 0, qui laisserait croire à une
    latence nulle plutôt qu'à une absence de donnée)."""
    since = time.time() - since_seconds
    bucket_seconds = since_seconds / buckets

    with _connect() as conn:
        rows = conn.execute(
            "SELECT CAST((checked_at - ?) / ? AS INTEGER) AS bucket, "
            "AVG(latency_ms), COUNT(latency_ms) "
            "FROM checks WHERE machine_id = ? AND checked_at >= ? "
            "GROUP BY bucket",
            (since, bucket_seconds, machine_id, since),
        ).fetchall()

    # Même raison de regroupement que get_timeline() ci-dessus — moyenne
    # pondérée par le nombre de mesures si deux buckets SQL fusionnent
    # dans le même segment final.
    by_bucket = {}
    for bucket, avg_latency, count in rows:
        if not count:
            continue
        idx = min(int(bucket), buckets - 1)
        prev_sum, prev_count = by_bucket.get(idx, (0.0, 0))
        by_bucket[idx] = (prev_sum + avg_latency * count, prev_count + count)

    timeline = []
    for i in range(buckets):
        if i in by_bucket:
            total, count = by_bucket[i]
            timeline.append(round(total / count, 1))
        else:
            timeline.append(None)
    return timeline


def start_session(machine_id, protocol, source_ip=None):
    """Enregistre le début d'une session (SSH ou VNC, voir ssh_ws.py et
    vnc_tls_bridge.py) et retourne son id, à repasser à end_session() une
    fois la connexion terminée — pour le journal des connexions de la
    page /sessions. source_ip vient de request.remote_addr côté SSH
    (connexion WebSocket directe) ; côté VNC, le pont ne voit que
    l'adresse locale de websockify (127.0.0.1), pas le vrai client
    distant — laissé à None dans ce cas plutôt que d'afficher une IP
    trompeuse."""
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO sessions (machine_id, protocol, source_ip, started_at) "
            "VALUES (?, ?, ?, ?)",
            (machine_id, protocol, source_ip, time.time()),
        )
        return cursor.lastrowid


def end_session(session_id):
    if session_id is None:
        return
    with _connect() as conn:
        conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (time.time(), session_id))


def get_recent_sessions(limit=200):
    """Retourne les sessions les plus récentes (les plus récentes en
    premier) pour la page /sessions — une session sans ended_at est
    encore ouverte (voir le rendu du template)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, machine_id, protocol, source_ip, started_at, ended_at "
            "FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r[0], "machine_id": r[1], "protocol": r[2], "source_ip": r[3],
            "started_at": r[4], "ended_at": r[5],
        }
        for r in rows
    ]
