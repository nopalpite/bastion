"""Régression : une réponse 304 ne doit jamais être suivie d'octets de corps.

eventlet <= 0.37 ajoutait `Transfer-Encoding: chunked` à toute réponse
HTTP/1.1 sans Content-Length (y compris un 304, que Werkzeug sert sans
Content-Length pour les fichiers statiques) puis envoyait le terminateur
`0\\r\\n\\r\\n` après les en-têtes. Sur une connexion keep-alive réutilisée
(reverse proxy), le client lisait ces 5 octets comme le début de la réponse
suivante -> 502 aléatoires. Corrigé côté eventlet (>= 0.38, réponses sans
corps 204/304/HEAD) ; ce test fige le comportement avec le vrai serveur et
la vraie appli, pour qu'un futur downgrade/remplacement du serveur WSGI ne
réintroduise pas le défaut silencieusement.

Importer `app` déclenche eventlet.monkey_patch() (voir tests/test_app_routes.py)."""
import socket

import eventlet
import pytest
from eventlet import wsgi

import app as app_module

STATIC_PATH = "/static/css/style.css"


@pytest.fixture
def server_port():
    listener = eventlet.listen(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    greenthread = eventlet.spawn(wsgi.server, listener, app_module.app, log_output=False)
    eventlet.sleep(0.1)
    yield port
    greenthread.kill()
    listener.close()


def _read_response_head(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    return head, rest


def _request(path, extra_headers=""):
    return (
        f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n{extra_headers}"
        "Connection: keep-alive\r\n\r\n"
    ).encode()


def _header(head, name):
    for line in head.split(b"\r\n")[1:]:
        key, _, value = line.partition(b": ")
        if key.lower() == name.lower().encode():
            return value
    return None


def test_304_has_no_body_bytes_and_keepalive_connection_stays_usable(server_port):
    sock = socket.create_connection(("127.0.0.1", server_port), timeout=2)
    try:
        # 1re requête sans condition: récupère l'ETag, lit le corps complet.
        sock.sendall(_request(STATIC_PATH))
        head, rest = _read_response_head(sock)
        assert head.startswith(b"HTTP/1.1 200")
        length = int(_header(head, "Content-Length"))
        while len(rest) < length:
            rest += sock.recv(65536)
        etag = _header(head, "ETag").decode()

        # 2e requête conditionnelle sur la MÊME connexion.
        sock.sendall(_request(STATIC_PATH, f"If-None-Match: {etag}\r\n"))
        head, rest = _read_response_head(sock)
        assert head.startswith(b"HTTP/1.1 304")
        assert _header(head, "Transfer-Encoding") is None
        # Aucun octet n'a le droit de suivre les en-têtes d'un 304.
        sock.settimeout(0.3)
        try:
            extra = sock.recv(65536)
        except TimeoutError:
            extra = b""
        assert rest == b"" and extra == b"", f"octets parasites après le 304: {rest + extra!r}"

        # 3e requête sur la même connexion: doit rester une réponse valide
        # (avant le correctif, les "0\r\n\r\n" parasites cassaient celle-ci).
        sock.settimeout(2)
        sock.sendall(_request(STATIC_PATH, f"If-None-Match: {etag}\r\n"))
        head, rest = _read_response_head(sock)
        assert head.startswith(b"HTTP/1.1 304")
        assert rest == b""
    finally:
        sock.close()
