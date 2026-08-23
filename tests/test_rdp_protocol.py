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


def test_rdp_params_for_machine_defaults_port(credentials_key):
    machine = {"id": "srv-win", "host": "10.0.0.20"}
    params = bridge.rdp_params_for_machine(machine)
    assert params["port"] == 3389
    assert "username" not in params
