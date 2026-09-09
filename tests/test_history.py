"""Tests pour history.py: historique de disponibilité (SQLite) utilisé par
la page /stats — fixture history_db (voir conftest.py), aucun accès au
fichier réel du projet."""
import time

import history


def test_record_and_uptime_percentage_all_up(history_db):
    history.record_check("m1", "up", 10.0)
    history.record_check("m1", "up", 12.0)
    assert history.get_uptime_percentage("m1", since_seconds=3600) == 100.0


def test_uptime_percentage_mixed(history_db):
    history.record_check("m1", "up", 10.0)
    history.record_check("m1", "down", None)
    history.record_check("m1", "up", 10.0)
    history.record_check("m1", "down", None)
    assert history.get_uptime_percentage("m1", since_seconds=3600) == 50.0


def test_uptime_percentage_none_without_data(history_db):
    assert history.get_uptime_percentage("unknown", since_seconds=3600) is None


def test_uptime_percentage_ignores_entries_outside_period(history_db):
    old = time.time() - 10_000
    history.record_check("m1", "down", None, checked_at=old)
    history.record_check("m1", "up", 5.0)
    # Fenêtre de 60s: ne doit voir que l'enregistrement récent ("up"),
    # pas celui vieux de 10000s ("down") — sinon ce serait 50%.
    assert history.get_uptime_percentage("m1", since_seconds=60) == 100.0


def test_get_retention_days_defaults_when_unset(history_db):
    assert history.get_retention_days() == history.config.HISTORY_RETENTION_DAYS_DEFAULT


def test_set_and_get_retention_days(history_db):
    history.set_retention_days(7)
    assert history.get_retention_days() == 7
    # Rappeler set_ écrase la valeur précédente plutôt que d'en ajouter une
    # deuxième (clé primaire "retention_days", voir la clause ON CONFLICT).
    history.set_retention_days(14)
    assert history.get_retention_days() == 14


def test_set_retention_days_rejects_less_than_one(history_db):
    import pytest
    with pytest.raises(ValueError):
        history.set_retention_days(0)


def test_purge_old_entries_removes_only_old_rows(history_db):
    old = time.time() - 40 * 86400  # 40 jours, au-delà de la rétention par défaut (30j)
    history.record_check("m1", "down", None, checked_at=old)
    history.record_check("m1", "up", 5.0)

    deleted = history.purge_old_entries()

    assert deleted == 1
    assert history.get_uptime_percentage("m1", since_seconds=86400 * 100) == 100.0


def test_purge_old_entries_uses_explicit_retention_override(history_db):
    recent = time.time() - 3600  # 1h
    history.record_check("m1", "up", 5.0, checked_at=recent)

    # Rétention explicite de 0 jour: même un enregistrement vieux d'1h
    # doit être purgé (cutoff = maintenant).
    deleted = history.purge_old_entries(retention_days=0)

    assert deleted == 1


def test_get_timeline_buckets_and_fills_gaps_with_none(history_db):
    # Décalages choisis nettement à l'intérieur de leur segment (pas pile
    # sur une frontière de bucket) : get_timeline() recalcule son propre
    # "maintenant" en interne, légèrement plus tard que celui capturé ici
    # -- une valeur pile sur une frontière basculerait alors parfois dans
    # le bucket voisin selon ce micro-écart, rendant le test friable.
    now = time.time()
    history.record_check("m1", "up", 5.0, checked_at=now - 3550)  # bucket 0 (sur 4, 0-900s)
    history.record_check("m1", "down", None, checked_at=now - 200)  # bucket 3 (2700-3600s)

    timeline = history.get_timeline("m1", since_seconds=3600, buckets=4)

    assert len(timeline) == 4
    assert timeline[0] == 100.0
    assert timeline[3] == 0.0
    assert timeline[1] is None
    assert timeline[2] is None


def test_get_timeline_empty_when_no_data(history_db):
    timeline = history.get_timeline("unknown", since_seconds=3600, buckets=6)
    assert timeline == [None] * 6


def test_get_latency_timeline_buckets_and_fills_gaps_with_none(history_db):
    now = time.time()
    history.record_check("m1", "up", 10.0, checked_at=now - 3550)  # bucket 0
    history.record_check("m1", "up", 30.0, checked_at=now - 200)  # bucket 3
    history.record_check("m1", "up", 50.0, checked_at=now - 190)  # bucket 3 aussi

    timeline = history.get_latency_timeline("m1", since_seconds=3600, buckets=4)

    assert len(timeline) == 4
    assert timeline[0] == 10.0
    assert timeline[3] == 40.0  # moyenne de 30 et 50
    assert timeline[1] is None
    assert timeline[2] is None


def test_get_latency_timeline_ignores_down_checks(history_db):
    # Une vérification "down" (latency_ms=None) ne doit ni compter dans la
    # moyenne ni faire apparaître un segment à 0 -- juste être ignorée.
    history.record_check("m1", "down", None)
    history.record_check("m1", "up", 20.0)

    timeline = history.get_latency_timeline("m1", since_seconds=3600, buckets=1)

    assert timeline == [20.0]


def test_get_latency_timeline_empty_when_no_data(history_db):
    timeline = history.get_latency_timeline("unknown", since_seconds=3600, buckets=6)
    assert timeline == [None] * 6


# --- Journal des connexions (page /sessions) ----------------------------

def test_start_session_returns_an_id(history_db):
    session_id = history.start_session("m1", "ssh", source_ip="10.0.0.5")
    assert session_id is not None


def test_get_recent_sessions_reflects_open_session(history_db):
    history.start_session("m1", "ssh", source_ip="10.0.0.5")

    sessions = history.get_recent_sessions()

    assert len(sessions) == 1
    assert sessions[0]["machine_id"] == "m1"
    assert sessions[0]["protocol"] == "ssh"
    assert sessions[0]["source_ip"] == "10.0.0.5"
    assert sessions[0]["ended_at"] is None


def test_end_session_sets_ended_at(history_db):
    session_id = history.start_session("m1", "vnc")

    history.end_session(session_id)

    sessions = history.get_recent_sessions()
    assert sessions[0]["ended_at"] is not None


def test_end_session_ignores_none_id(history_db):
    history.end_session(None)  # ne doit pas lever (voir ssh_ws.py: pas de session ouverte)


def test_get_recent_sessions_most_recent_first(history_db):
    old_id = history.start_session("m1", "ssh", source_ip=None)
    with history._connect() as conn:
        conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (time.time() - 100, old_id))
    history.start_session("m2", "vnc", source_ip=None)

    sessions = history.get_recent_sessions()

    assert [s["machine_id"] for s in sessions] == ["m2", "m1"]


def test_get_recent_sessions_respects_limit(history_db):
    for i in range(5):
        history.start_session(f"m{i}", "ssh")

    assert len(history.get_recent_sessions(limit=2)) == 2


def test_purge_old_entries_also_purges_old_sessions(history_db):
    old_id = history.start_session("m1", "ssh")
    with history._connect() as conn:
        conn.execute(
            "UPDATE sessions SET started_at = ? WHERE id = ?",
            (time.time() - 40 * 86400, old_id),
        )
    history.start_session("m2", "ssh")

    deleted = history.purge_old_entries()

    assert deleted == 1
    assert [s["machine_id"] for s in history.get_recent_sessions()] == ["m2"]
