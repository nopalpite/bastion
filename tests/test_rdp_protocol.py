"""Tests pour rdp_protocol.py: encodage/décodage du protocole Guacamole
(échangé entre le pont RDP et guacd — pas RDP lui-même, entièrement délégué
à guacd, voir le docstring du module)."""
import rdp_protocol as bridge


class FakeSocket:
    """Simule un socket dont recv() renvoie des données pré-écrites, morceau
    par morceau (pour exercer la lecture incrémentale sans dépendre d'un
    vrai réseau)."""

    def __init__(self, data, chunk_size=None):
        self.data = data
        self.pos = 0
        self.chunk_size = chunk_size or len(data)

    def recv(self, n):
        size = min(n, self.chunk_size, len(self.data) - self.pos)
        chunk = self.data[self.pos:self.pos + size]
        self.pos += len(chunk)
        return chunk


class FakeHandshakeSocket(FakeSocket):
    """FakeSocket qui enregistre aussi ce qui est envoyé via sendall(), pour
    vérifier le contenu exact des instructions émises par le client (pas
    seulement ce qu'il lit) — utilisé pour tester _handshake()."""

    def __init__(self, data, chunk_size=None):
        super().__init__(data, chunk_size)
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)


def test_encode_instruction_basic():
    assert bridge.encode_instruction("select", "rdp") == b"6.select,3.rdp;"


def test_encode_instruction_empty_args():
    # Utilisé pour les instructions de capacités client qu'on n'exploite
    # pas (audio/video/image) — un opcode seul, sans argument.
    assert bridge.encode_instruction("audio") == b"5.audio;"


def test_read_instruction_round_trip():
    raw = bridge.encode_instruction("args", "1.5.0", "hostname", "port")
    opcode, args, rest = bridge.read_instruction(FakeSocket(raw), b"")
    assert opcode == "args"
    assert args == ["1.5.0", "hostname", "port"]
    assert rest == b""


def test_read_instruction_byte_by_byte_delivery():
    # Le flux réseau peut découper les données n'importe où -- la lecture
    # doit rester correcte même reçue un octet à la fois.
    raw = bridge.encode_instruction("ready", "conn-id-42")
    opcode, args, rest = bridge.read_instruction(FakeSocket(raw, chunk_size=1), b"")
    assert opcode == "ready"
    assert args == ["conn-id-42"]


def test_read_instruction_reuses_leftover_buffer():
    raw = bridge.encode_instruction("a") + bridge.encode_instruction("b")
    opcode1, _, rest = bridge.read_instruction(FakeSocket(b""), raw)
    assert opcode1 == "a"
    opcode2, _, rest = bridge.read_instruction(FakeSocket(b""), rest)
    assert opcode2 == "b"
    assert rest == b""


def test_length_prefix_counts_unicode_characters_not_utf8_bytes():
    # La longueur Guacamole compte des caractères Unicode, pas des octets
    # UTF-8 (confirmé par la doc du protocole) -- un accent (2 octets en
    # UTF-8, 1 caractère) doit être compté comme 1, pas 2. C'est le bug
    # trouvé en testant ce module contre un faux serveur: une valeur avec
    # accent désynchronisait la lecture de tout ce qui suivait.
    value = "identifiants refusés"  # contient un "é" (2 octets UTF-8, 1 caractère)
    raw = bridge.encode_instruction("error", value, "519")
    # Le préfixe doit être la longueur EN CARACTÈRES ("21", pas "22")
    assert raw.startswith(f"5.error,{len(value)}.".encode())
    opcode, args, rest = bridge.read_instruction(FakeSocket(raw), b"")
    assert opcode == "error"
    assert args == [value, "519"]
    assert rest == b""


def test_length_prefix_unicode_with_byte_by_byte_delivery():
    # Même chose, mais en forçant le décodage incrémental à composer un
    # caractère multi-octets à partir de plusieurs recv() -- le cas
    # potentiellement fragile de l'implémentation.
    raw = bridge.encode_instruction("name", "Serveur é à ô ç")
    opcode, args, rest = bridge.read_instruction(FakeSocket(raw, chunk_size=1), b"")
    assert opcode == "name"
    assert args == ["Serveur é à ô ç"]


def test_rdp_params_for_machine(credentials_key):
    import credentials
    machine = {
        "id": "srv-win", "host": "10.0.0.20", "rdp_port": 3389,
        "rdp_username": "admin", "rdp_password": credentials.encrypt("s3cret"),
    }
    params = bridge.rdp_params_for_machine(machine)
    assert params["hostname"] == "10.0.0.20"
    assert params["port"] == 3389
    assert params["username"] == "admin"
    assert params["password"] == "s3cret"
    assert params["ignore-cert"] == "true"
    # Sans rdp_security explicite sur la machine: repli sur DEFAULT_RDP_SECURITY
    # ("nla") -- voir son commentaire pour pourquoi ce n'est PAS "any"
    # (négociation automatique), qui a un bug connu et documenté côté
    # FreeRDP/guacd face à un serveur qui impose NLA.
    assert params["security"] == bridge.DEFAULT_RDP_SECURITY == "nla"


def test_rdp_params_for_machine_uses_configured_security(credentials_key):
    # Après deux échecs réels différents ("any" puis "nla" forcé, tous
    # deux refusés par le même serveur Windows malgré des identifiants
    # corrects), security est configurable par machine plutôt que figé
    # dans le code -- voir templates/host_form.html.
    machine = {"id": "srv-win", "host": "10.0.0.20", "rdp_security": "tls"}
    params = bridge.rdp_params_for_machine(machine)
    assert params["security"] == "tls"


def test_rdp_params_for_machine_defaults_port(credentials_key):
    machine = {"id": "srv-win", "host": "10.0.0.20"}
    params = bridge.rdp_params_for_machine(machine)
    assert params["port"] == 3389
    assert "username" not in params


# --- _handshake: la vraie négociation avec guacd (select -> args ->
# capacités client -> connect -> ready). Jusqu'ici, seules les primitives
# bas niveau (encode/decode d'une instruction isolée) étaient testées —
# jamais cette séquence, ce qui a laissé passer un vrai bug (voir
# ci-dessous), découvert seulement contre un vrai guacd 1.6.0 en
# production, pas en test. ---------------------------------------------

def test_handshake_connect_includes_protocol_version():
    # Le "connect" envoyé par le client doit inclure la version de
    # protocole (reçue en premier dans "args") comme PREMIÈRE valeur,
    # avant celles des paramètres — confirmé dans l'exemple donné par la
    # doc officielle du protocole Guacamole (guacamole-protocol.html):
    # "7.connect,13.VERSION_1_1_0,9.localhost,4.5900,0.,0.,0.;". L'omettre
    # fait que "connect" a une valeur de MOINS que ce que guacd attend, et
    # guacd refuse la connexion avec "Client did not return the expected
    # number of arguments" — exactement le bug qui s'est produit en
    # production contre un vrai guacd 1.6.0 avant ce correctif.
    args_reply = bridge.encode_instruction("args", "VERSION_1_1_0", "hostname", "port")
    ready_reply = bridge.encode_instruction("ready", "conn-id-1")
    sock = FakeHandshakeSocket(args_reply + ready_reply)

    leftover = bridge._handshake(sock, "rdp", {"hostname": "10.0.0.1", "port": "3389"})

    assert leftover == b""
    connect_sent = next(s for s in sock.sent if s.startswith(b"7.connect,"))
    opcode, args, _ = bridge.read_instruction(FakeSocket(connect_sent), b"")
    assert opcode == "connect"
    assert args == ["VERSION_1_1_0", "10.0.0.1", "3389"]


def test_handshake_connect_uses_empty_string_for_missing_params():
    args_reply = bridge.encode_instruction("args", "VERSION_1_1_0", "hostname", "domain", "port")
    ready_reply = bridge.encode_instruction("ready", "conn-id-2")
    sock = FakeHandshakeSocket(args_reply + ready_reply)

    # "domain" volontairement absent des params fournis: doit être envoyé
    # comme chaîne vide, pas omis (voir le commentaire de _handshake).
    bridge._handshake(sock, "rdp", {"hostname": "10.0.0.1", "port": "3389"})

    connect_sent = next(s for s in sock.sent if s.startswith(b"7.connect,"))
    _, args, _ = bridge.read_instruction(FakeSocket(connect_sent), b"")
    assert args == ["VERSION_1_1_0", "10.0.0.1", "", "3389"]


def test_handshake_raises_on_error_response():
    import pytest
    args_reply = bridge.encode_instruction("args", "VERSION_1_1_0", "hostname")
    error_reply = bridge.encode_instruction("error", "identifiants refusés", "519")
    sock = FakeHandshakeSocket(args_reply + error_reply)

    with pytest.raises(bridge.GuacamoleError, match="identifiants refusés"):
        bridge._handshake(sock, "rdp", {"hostname": "10.0.0.1"})
