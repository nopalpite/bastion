"""Tests pour discovery.py: découverte réseau (page /discover) — sans
dépendre d'un vrai réseau (monitor.ping_host/check_port et
vnc_tls_bridge.probe_available sont monkeypatchés)."""
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
