"""Tests pour gen_vnc_tokens.py: toute machine avec un port VNC est routée
vers son pont local (vnc_tls_bridge.py sonde et s'adapte lui-même)."""
import runpy

import store


def _run_gen_vnc_tokens(capsys):
    runpy.run_module("gen_vnc_tokens", run_name="__main__")
    return capsys.readouterr().out


def test_vnc_machine_routes_to_local_bridge_port(machines_file, monkeypatch, capsys):
    monkeypatch.setattr(
        store, "load_machines",
        lambda: [{"id": "srv1", "host": "10.0.0.5", "vnc_port": 5900, "vnc_bridge_port": 6100}],
    )
    output = _run_gen_vnc_tokens(capsys)
    assert output.strip() == "srv1: 127.0.0.1:6100"


def test_vnc_without_assigned_bridge_port_is_skipped(machines_file, monkeypatch, capsys):
    monkeypatch.setattr(
        store, "load_machines",
        lambda: [{"id": "srv2", "host": "10.0.0.6", "vnc_port": 5900}],
    )
    output = _run_gen_vnc_tokens(capsys)
    assert output.strip() == ""


def test_machine_without_vnc_port_is_skipped(machines_file, monkeypatch, capsys):
    monkeypatch.setattr(store, "load_machines", lambda: [{"id": "srv3", "host": "10.0.0.7"}])
    output = _run_gen_vnc_tokens(capsys)
    assert output.strip() == ""
