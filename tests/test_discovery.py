"""Tests pour discovery.py: découverte réseau (page /discover) — sans
dépendre d'un vrai réseau (monitor.ping_host/check_port et
vnc_tls_bridge.probe_available sont monkeypatchés)."""
import socket
import struct
import threading

import pytest

import discovery


def test_parse_hosts_returns_usable_addresses():
    hosts = discovery.parse_hosts("192.168.1.0/30")
    # /30 = 4 adresses, réseau + broadcast exclus par .hosts() -> 2 usables
    assert hosts == ["192.168.1.1", "192.168.1.2"]


def test_parse_hosts_rejects_invalid_cidr():
    with pytest.raises(discovery.DiscoveryError, match="invalide"):
        discovery.parse_hosts("pas-une-plage")


def test_parse_hosts_rejects_range_larger_than_max(monkeypatch):
    monkeypatch.setattr(discovery, "MAX_HOSTS", 2)
    with pytest.raises(discovery.DiscoveryError, match="trop grande"):
        discovery.parse_hosts("192.168.1.0/24")


def test_discover_host_returns_none_when_down(monkeypatch):
    monkeypatch.setattr(discovery.monitor, "ping_host", lambda host, timeout=None: (False, None))
    assert discovery.discover_host("10.0.0.5") is None


def test_discover_host_reports_ssh_and_vnc(monkeypatch):
    monkeypatch.setattr(discovery.monitor, "ping_host", lambda host, timeout=None: (True, 1.2))
    monkeypatch.setattr(discovery.monitor, "check_port", lambda host, port, timeout=None: True)
    monkeypatch.setattr(
        discovery.vnc_tls_bridge, "probe_available",
        lambda machine, timeout=None: True,
    )
    monkeypatch.setattr(discovery, "_reverse_dns", lambda ip: "srv-test.local")

    result = discovery.discover_host("10.0.0.5")

    assert result == {
        "ip": "10.0.0.5", "hostname": "srv-test.local", "ssh": True, "vnc_port": 5900,
    }


def test_discover_host_tries_5901_when_5900_unavailable(monkeypatch):
    monkeypatch.setattr(discovery.monitor, "ping_host", lambda host, timeout=None: (True, 1.2))
    monkeypatch.setattr(discovery.monitor, "check_port", lambda host, port, timeout=None: True)
    monkeypatch.setattr(discovery, "_reverse_dns", lambda ip: None)

    def probe(machine, timeout=None):
        return machine["vnc_port"] == 5901

    monkeypatch.setattr(discovery.vnc_tls_bridge, "probe_available", probe)

    result = discovery.discover_host("10.0.0.5")

    assert result["vnc_port"] == 5901


def test_discover_host_no_ssh_no_vnc(monkeypatch):
    monkeypatch.setattr(discovery.monitor, "ping_host", lambda host, timeout=None: (True, 1.2))
    monkeypatch.setattr(discovery.monitor, "check_port", lambda host, port, timeout=None: False)
    monkeypatch.setattr(discovery, "_reverse_dns", lambda ip: None)

    result = discovery.discover_host("10.0.0.5")

    assert result == {"ip": "10.0.0.5", "hostname": None, "ssh": False, "vnc_port": None}


def test_run_discovery_filters_and_sorts(monkeypatch):
    def fake_discover(ip, timeout=None):
        if ip == "10.0.0.2":
            return None  # ne répond pas
        return {"ip": ip, "hostname": None, "ssh": True, "vnc_port": None}

    monkeypatch.setattr(discovery, "discover_host", fake_discover)

    results = discovery.run_discovery("10.0.0.0/29")

    ips = [r["ip"] for r in results]
    assert "10.0.0.2" not in ips
    assert ips == sorted(ips, key=lambda ip: tuple(int(p) for p in ip.split(".")))


def test_run_discovery_propagates_invalid_cidr():
    with pytest.raises(discovery.DiscoveryError):
        discovery.run_discovery("not-a-cidr")


def test_guess_local_cidr_returns_slash_24(monkeypatch):
    class FakeSocket:
        def connect(self, addr):
            pass

        def getsockname(self):
            return ("192.168.1.42", 12345)

        def close(self):
            pass

    monkeypatch.setattr(discovery.socket, "socket", lambda *a, **k: FakeSocket())

    assert discovery.guess_local_cidr() == "192.168.1.0/24"


def test_guess_local_cidr_returns_none_on_socket_error(monkeypatch):
    class FailingSocket:
        def connect(self, addr):
            raise OSError("no network")

        def close(self):
            pass

    monkeypatch.setattr(discovery.socket, "socket", lambda *a, **k: FailingSocket())

    assert discovery.guess_local_cidr() is None


# --- _local_ip_for: interface à utiliser pour joindre une IP précise
# (contrairement à guess_local_cidr, qui devine l'interface "par défaut"
# via une destination arbitraire) -- voir son usage dans
# _mdns_reverse_lookup pour le bug multi-interfaces qui a motivé ça -----

def test_local_ip_for_returns_route_ip(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.connected_to = None

        def connect(self, addr):
            self.connected_to = addr

        def getsockname(self):
            return ("10.0.0.5", 54321)

        def close(self):
            pass

    fake = FakeSocket()
    monkeypatch.setattr(discovery.socket, "socket", lambda *a, **k: fake)

    assert discovery._local_ip_for("10.0.0.42") == "10.0.0.5"
    assert fake.connected_to == ("10.0.0.42", 1)


def test_local_ip_for_returns_none_on_socket_error(monkeypatch):
    class FailingSocket:
        def connect(self, addr):
            raise OSError("network unreachable")

        def close(self):
            pass

    monkeypatch.setattr(discovery.socket, "socket", lambda *a, **k: FailingSocket())

    assert discovery._local_ip_for("10.0.0.42") is None


# --- Résolution mDNS (_reverse_dns priorise le vrai hostname annoncé par
# l'appareil lui-même plutôt que le DNS/DHCP du routeur -- voir le
# docstring de _mdns_reverse_lookup pour le cas réel qui a motivé ça) ---

def _dns_name_bytes(name):
    return b"".join(bytes([len(label)]) + label.encode() for label in name.split(".")) + b"\x00"


def test_read_dns_name_simple_label():
    data = b"\x03foo\x03bar\x00REST"
    name, pos = discovery._read_dns_name(data, 0)
    assert name == "foo.bar"
    assert pos == 9  # juste après l'octet nul, avant "REST"


def test_read_dns_name_follows_compression_pointer():
    # "foo.bar" à l'offset 20, puis un pointeur vers cet offset ailleurs
    # dans le paquet -- cas réaliste (une réponse DNS pointe souvent vers
    # un nom déjà présent plutôt que de le répéter en toutes lettres).
    prefix = b"\x00" * 20
    target = b"\x03foo\x03bar\x00"
    data = prefix + target + struct.pack(">H", 0xC000 | 20)
    pointer_pos = len(prefix) + len(target)

    name, pos = discovery._read_dns_name(data, pointer_pos)

    assert name == "foo.bar"
    assert pos == pointer_pos + 2  # s'arrête après LE POINTEUR, pas après la cible


def test_read_dns_name_raises_on_truncated_packet():
    with pytest.raises(ValueError):
        discovery._read_dns_name(b"\x05abc", 0)  # longueur annoncée 5, seulement 3 octets fournis


def test_build_ptr_query_targets_reverse_address_with_qu_bit():
    packet = discovery._build_ptr_query("192.168.1.20")

    qdcount = struct.unpack(">H", packet[4:6])[0]
    assert qdcount == 1
    name, pos = discovery._read_dns_name(packet, 12)
    assert name == "20.1.168.192.in-addr.arpa"
    qtype, qclass = struct.unpack(">HH", packet[pos:pos + 4])
    assert qtype == discovery._DNS_TYPE_PTR
    assert qclass & 0x8000  # bit QU (réponse unicast demandée, RFC 6762 §5.4)
    assert qclass & 0x7FFF == discovery._DNS_CLASS_IN


def test_parse_ptr_response_extracts_target_no_question_echoed():
    rdata = _dns_name_bytes("rpi-3-bureau.local")
    owner = _dns_name_bytes("20.1.168.192.in-addr.arpa")
    header = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0)
    answer = owner + struct.pack(">HHIH", discovery._DNS_TYPE_PTR, 1, 120, len(rdata)) + rdata

    assert discovery._parse_ptr_response(header + answer) == "rpi-3-bureau.local"


def test_parse_ptr_response_follows_compressed_owner_name():
    # Réponse réaliste : la question est répétée (QDCOUNT=1), et le nom
    # de la réponse pointe vers elle par compression plutôt que de la
    # répéter -- le cas le plus susceptible de révéler un bug de parsing.
    question_name = _dns_name_bytes("20.1.168.192.in-addr.arpa")
    question = question_name + struct.pack(">HH", discovery._DNS_TYPE_PTR, discovery._DNS_CLASS_IN)
    header = struct.pack(">HHHHHH", 0, 0x8400, 1, 1, 0, 0)

    pointer_to_question_name = struct.pack(">H", 0xC000 | 12)
    rdata = _dns_name_bytes("rpi-3-bureau.local")
    answer = (
        pointer_to_question_name
        + struct.pack(">HHIH", discovery._DNS_TYPE_PTR, 1, 120, len(rdata))
        + rdata
    )

    packet = header + question + answer
    assert discovery._parse_ptr_response(packet) == "rpi-3-bureau.local"


def test_parse_ptr_response_returns_none_without_ptr_answer():
    header = struct.pack(">HHHHHH", 0, 0x8400, 0, 0, 0, 0)  # aucune réponse
    assert discovery._parse_ptr_response(header) is None


def test_parse_ptr_response_returns_none_on_garbage():
    assert discovery._parse_ptr_response(b"\x00\x01\x02") is None


def _run_fake_mdns_responder(response_bytes, ready_event, port_holder):
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    port_holder.append(server.getsockname()[1])
    ready_event.set()
    server.settimeout(2)
    try:
        _query, addr = server.recvfrom(4096)
        server.sendto(response_bytes, addr)
    except OSError:
        pass
    finally:
        server.close()


def test_mdns_reverse_lookup_round_trip_with_fake_responder(monkeypatch):
    rdata = _dns_name_bytes("rpi-3-bureau.local")
    owner = _dns_name_bytes("20.1.168.192.in-addr.arpa")
    header = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0)
    response = (
        header + owner
        + struct.pack(">HHIH", discovery._DNS_TYPE_PTR, 1, 120, len(rdata))
        + rdata
    )

    ready = threading.Event()
    port_holder = []
    thread = threading.Thread(
        target=_run_fake_mdns_responder, args=(response, ready, port_holder), daemon=True,
    )
    thread.start()
    assert ready.wait(timeout=2)
    monkeypatch.setattr(discovery, "_MDNS_ADDR", ("127.0.0.1", port_holder[0]))

    # Cible "127.0.0.1" (pas une vraie IP de machine) précisément pour que
    # _local_ip_for() résolve aussi en loopback : le faux répondeur n'est
    # bindé que sur 127.0.0.1, donc forcer le socket client sur une
    # interface LAN réelle romprait l'aller-retour (un socket bindé sur
    # 127.0.0.1 ne peut pas répondre vers une adresse hors loopback).
    assert discovery._mdns_reverse_lookup("127.0.0.1") == "rpi-3-bureau.local"
    thread.join(timeout=2)


def test_mdns_reverse_lookup_returns_none_when_nothing_responds(monkeypatch):
    # Un port local sur lequel rien n'écoute : le paquet UDP part
    # (fire-and-forget), mais aucune réponse n'arrivera jamais.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    unused_port = probe.getsockname()[1]
    probe.close()

    monkeypatch.setattr(discovery, "_MDNS_ADDR", ("127.0.0.1", unused_port))

    assert discovery._mdns_reverse_lookup("192.168.1.20", timeout=0.2) is None


# --- Sélection d'interface sur un hôte multi-interfaces (bug signalé par
# un utilisateur d'un autre projet utilisant Bastion : sans bind()/
# IP_MULTICAST_IF, le paquet sort par la route par défaut, jamais par
# l'interface réellement connectée au réseau de la machine ciblée) ------

class _RecordingSocket:
    """Socket factice qui note tous les appels reçus, pour vérifier que
    bind()/setsockopt() ont bien lieu (ou pas), avant sendto()."""

    def __init__(self, response=b"\x00" * 12, raise_on_bind=False):
        self.calls = []
        self._response = response
        self._raise_on_bind = raise_on_bind

    def settimeout(self, timeout):
        self.calls.append(("settimeout", timeout))

    def bind(self, addr):
        self.calls.append(("bind", addr))
        if self._raise_on_bind:
            raise OSError("cannot bind")

    def setsockopt(self, level, optname, value):
        self.calls.append(("setsockopt", level, optname, value))

    def sendto(self, data, addr):
        self.calls.append(("sendto", addr))

    def recvfrom(self, bufsize):
        return self._response, ("0.0.0.0", 5353)

    def close(self):
        self.calls.append(("close",))


def test_mdns_reverse_lookup_binds_to_detected_local_interface(monkeypatch):
    sock = _RecordingSocket()
    monkeypatch.setattr(discovery.socket, "socket", lambda *a, **k: sock)
    monkeypatch.setattr(discovery, "_local_ip_for", lambda ip: "10.0.0.5")

    discovery._mdns_reverse_lookup("10.0.0.42")

    assert ("bind", ("10.0.0.5", 0)) in sock.calls
    assert (
        "setsockopt", socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("10.0.0.5"),
    ) in sock.calls
    # bind()/setsockopt() doivent précéder l'envoi, pas le suivre.
    bind_idx = sock.calls.index(("bind", ("10.0.0.5", 0)))
    send_idx = next(i for i, c in enumerate(sock.calls) if c[0] == "sendto")
    assert bind_idx < send_idx


def test_mdns_reverse_lookup_skips_binding_when_interface_undetectable(monkeypatch):
    sock = _RecordingSocket()
    monkeypatch.setattr(discovery.socket, "socket", lambda *a, **k: sock)
    monkeypatch.setattr(discovery, "_local_ip_for", lambda ip: None)

    discovery._mdns_reverse_lookup("10.0.0.42")

    # Hôte mono-interface (cas d'origine) : comportement inchangé, on
    # laisse le noyau choisir l'interface comme avant ce correctif.
    assert not any(call[0] == "bind" for call in sock.calls)
    assert not any(call[0] == "setsockopt" for call in sock.calls)
    assert any(call[0] == "sendto" for call in sock.calls)


def test_mdns_reverse_lookup_still_sends_if_bind_fails(monkeypatch):
    sock = _RecordingSocket(raise_on_bind=True)
    monkeypatch.setattr(discovery.socket, "socket", lambda *a, **k: sock)
    monkeypatch.setattr(discovery, "_local_ip_for", lambda ip: "10.0.0.5")

    discovery._mdns_reverse_lookup("10.0.0.42")  # ne doit pas lever

    assert any(call[0] == "sendto" for call in sock.calls)


def _must_not_be_called(*args, **kwargs):
    raise AssertionError("ne doit pas être appelé si mDNS a déjà répondu")


def test_reverse_dns_prefers_mdns_over_router_dns(monkeypatch):
    monkeypatch.setattr(
        discovery, "_mdns_reverse_lookup",
        lambda ip, timeout=None: "rpi-3-bureau.local",
    )
    monkeypatch.setattr(discovery.socket, "gethostbyaddr", _must_not_be_called)

    # Le ".local" (systématique en mDNS, RFC 6762 §3) est retiré: pas
    # d'intérêt pour l'utilisateur, voir _strip_local_suffix.
    assert discovery._reverse_dns("192.168.1.20") == "rpi-3-bureau"


def test_reverse_dns_falls_back_to_router_dns_when_mdns_silent(monkeypatch):
    monkeypatch.setattr(discovery, "_mdns_reverse_lookup", lambda ip, timeout=None: None)
    monkeypatch.setattr(discovery.socket, "gethostbyaddr", lambda ip: ("ah-ade980", [], [ip]))

    assert discovery._reverse_dns("192.168.1.20") == "ah-ade980"


# --- _strip_local_suffix: le ".local" mDNS est un détail de protocole, pas
# une info utile à afficher/préremplir dans l'inventaire ------------------

def test_strip_local_suffix_removes_trailing_local():
    assert discovery._strip_local_suffix("rpi-3-bureau.local") == "rpi-3-bureau"


def test_strip_local_suffix_is_case_insensitive():
    assert discovery._strip_local_suffix("rpi-3-bureau.LOCAL") == "rpi-3-bureau"


def test_strip_local_suffix_leaves_other_names_untouched():
    assert discovery._strip_local_suffix("ah-ade980") == "ah-ade980"


def test_strip_local_suffix_passes_through_none():
    assert discovery._strip_local_suffix(None) is None


def test_reverse_dns_strips_local_suffix_from_router_dns_too(monkeypatch):
    # Rare mais possible (résolveur DNS local configuré avec un domaine
    # ".local") -- la même règle s'applique quelle que soit la source.
    monkeypatch.setattr(discovery, "_mdns_reverse_lookup", lambda ip, timeout=None: None)
    monkeypatch.setattr(discovery.socket, "gethostbyaddr", lambda ip: ("srv.local", [], [ip]))

    assert discovery._reverse_dns("192.168.1.20") == "srv"
