"""Tests pour sftp_ws.py: logique de transfert par morceaux et limites de
taille, avec un faux SFTPClient (pas de vraie session SSH) et un faux
Socket.IO qui capture les handlers enregistrés par register_sftp_handlers()
pour les appeler directement dans les tests."""
import base64
from types import SimpleNamespace

import pytest

import sftp_ws


class FakeWriteHandle:
    """Retourné par FakeSFTP.open(path, "wb"): utilisé aussi bien comme
    context manager (sftp_write_file, "with ... as f") que comme handle
    gardé ouvert entre deux appels (sftp_upload_chunk)."""

    def __init__(self, sink):
        self.sink = sink

    def write(self, data):
        self.sink.written += data

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


class FakeSFTP:
    def __init__(self, file_bytes=b""):
        self.file_bytes = file_bytes
        self.written = b""

    def stat(self, path):
        return SimpleNamespace(st_size=len(self.file_bytes))

    def open(self, path, mode):
        if "r" in mode:
            import io
            return io.BytesIO(self.file_bytes)
        return FakeWriteHandle(self)


class FakeSocketIO:
    """Capture les handlers enregistrés via @socketio.on(...) pour que les
    tests puissent les appeler directement, sans vrai serveur Socket.IO."""

    def __init__(self):
        self.handlers = {}

    def on(self, event):
        def decorator(fn):
            self.handlers[event] = fn
            return fn
        return decorator


@pytest.fixture
def sftp_env(monkeypatch):
    """Enregistre les handlers sftp_ws sur un faux Socket.IO, avec une
    session déjà "connectée" (sid fixe) et son propre FakeSFTP. Retourne
    (handlers, sessions) pour que chaque test configure/appelle ce qu'il
    lui faut."""
    fake_socketio = FakeSocketIO()
    sessions = {"test-sid": {"client": None, "channel": None, "sftp": FakeSFTP()}}
    monkeypatch.setattr(sftp_ws, "request", SimpleNamespace(sid="test-sid"))
    sftp_ws.register_sftp_handlers(fake_socketio, sessions)
    return fake_socketio.handlers, sessions


@pytest.fixture
def emitted(monkeypatch):
    """Capture les appels à emit() (le "emit" global de flask_socketio
    importé dans sftp_ws, pas une méthode du faux Socket.IO ci-dessus)."""
    calls = []
    monkeypatch.setattr(sftp_ws, "emit", lambda event, data=None, **kw: calls.append((event, data)))
    return calls


def test_upload_chunk_writes_and_acks(sftp_env):
    handlers, sessions = sftp_env
    content = base64.b64encode(b"hello").decode()

    resp = handlers["sftp_upload_chunk"]({
        "upload_id": "u1", "path": "/tmp/f.txt", "chunk_index": 0, "content_base64": content,
    })

    assert resp == {"ok": True}
    assert sessions["test-sid"]["uploads"]["u1"]["written"] == 5
    assert sessions["test-sid"]["sftp"].written == b"hello"


def test_upload_chunk_rejects_invalid_base64(sftp_env):
    handlers, _ = sftp_env
    resp = handlers["sftp_upload_chunk"]({
        "upload_id": "u1", "path": "/tmp/f.txt", "chunk_index": 0,
        "content_base64": "%%%not-base64%%%",
    })
    assert resp["ok"] is False


def test_upload_chunk_out_of_order_first_chunk_is_rejected(sftp_env):
    handlers, sessions = sftp_env
    content = base64.b64encode(b"hello").decode()

    resp = handlers["sftp_upload_chunk"]({
        "upload_id": "u2", "path": "/tmp/f.txt", "chunk_index": 5, "content_base64": content,
    })

    assert resp["ok"] is False
    assert "désynchronisé" in resp["error"]
    assert "u2" not in sessions["test-sid"].get("uploads", {})


def test_upload_chunk_rejects_file_over_max_transfer_bytes(sftp_env):
    handlers, sessions = sftp_env
    big_chunk = base64.b64encode(b"x" * (sftp_ws.MAX_TRANSFER_BYTES + 1)).decode()

    resp = handlers["sftp_upload_chunk"]({
        "upload_id": "u3", "path": "/tmp/big.bin", "chunk_index": 0, "content_base64": big_chunk,
    })

    assert resp["ok"] is False
    assert "volumineux" in resp["error"]
    assert "u3" not in sessions["test-sid"]["uploads"]


def test_read_file_returns_utf8_content(sftp_env, emitted):
    handlers, sessions = sftp_env
    sessions["test-sid"]["sftp"] = FakeSFTP(file_bytes=b"bonjour")

    handlers["sftp_read_file"]({"path": "/tmp/f.txt"})

    assert emitted == [("sftp_file_text", {"path": "/tmp/f.txt", "content": "bonjour"})]


def test_read_file_rejects_non_utf8_content(sftp_env, emitted):
    handlers, sessions = sftp_env
    sessions["test-sid"]["sftp"] = FakeSFTP(file_bytes=b"\xff\xfe\x00binaire")

    handlers["sftp_read_file"]({"path": "/tmp/bin.dat"})

    assert emitted[-1][0] == "sftp_error"
    assert "UTF-8" in emitted[-1][1]["message"]


def test_read_file_rejects_file_over_max_edit_bytes(sftp_env, emitted):
    handlers, sessions = sftp_env
    sessions["test-sid"]["sftp"] = FakeSFTP(file_bytes=b"x" * (sftp_ws.MAX_EDIT_BYTES + 1))

    handlers["sftp_read_file"]({"path": "/tmp/big.txt"})

    assert emitted[-1][0] == "sftp_error"
    assert "volumineux" in emitted[-1][1]["message"]


def test_write_file_saves_content(sftp_env, emitted):
    handlers, sessions = sftp_env

    handlers["sftp_write_file"]({"path": "/tmp/f.txt", "content": "bonjour"})

    assert sessions["test-sid"]["sftp"].written == b"bonjour"
    assert emitted == [("sftp_file_saved", {"path": "/tmp/f.txt"})]


def test_write_file_rejects_content_over_max_edit_bytes(sftp_env, emitted):
    handlers, _ = sftp_env
    oversized_content = "x" * (sftp_ws.MAX_EDIT_BYTES + 1)

    handlers["sftp_write_file"]({"path": "/tmp/f.txt", "content": oversized_content})

    assert emitted[-1][0] == "sftp_error"
    assert "volumineux" in emitted[-1][1]["message"]


def test_handlers_without_active_session_report_error(monkeypatch, emitted):
    fake_socketio = FakeSocketIO()
    monkeypatch.setattr(sftp_ws, "request", SimpleNamespace(sid="no-such-sid"))
    sftp_ws.register_sftp_handlers(fake_socketio, {})  # aucune session active

    fake_socketio.handlers["sftp_list"]({"path": "/"})

    assert emitted == [("sftp_error", {"message": "Aucune session SSH active."})]
