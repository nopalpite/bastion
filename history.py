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
    """Supprime les enregistrements plus vieux que la rétention configurée
    (ou explicitement fournie) et retourne le nombre de lignes supprimées
    — utilisée à la fois par la purge périodique automatique (monitor.py)
    et le bouton "Purger maintenant" de la page /stats."""
    days = retention_days if retention_days is not None else get_retention_days()
    cutoff = time.time() - days * 86400
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM checks WHERE checked_at < ?", (cutoff,))
        return cursor.rowcount


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
