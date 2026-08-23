"""Tests pour gen_vnc_tokens.py: toute machine avec un port VNC est routée
vers son pont local dédié (vnc_tls_bridge.py). Le RDP n'apparaît pas ici —
rdp_bridge.py sert ses propres WebSocket, voir son docstring."""
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


def test_rdp_only_machine_produces_no_token(machines_file, monkeypatch, capsys):
    monkeypatch.setattr(
        store, "load_machines",
        lambda: [{"id": "srv-win", "host": "10.0.0.8", "rdp_port": 3389}],
    )
    output = _run_gen_vnc_tokens(capsys)
    assert output.strip() == ""


def test_machine_with_both_vnc_and_rdp_only_gets_a_vnc_token(machines_file, monkeypatch, capsys):
    monkeypatch.setattr(
        store, "load_machines",
        lambda: [{
            "id": "srv-win", "host": "10.0.0.8",
            "vnc_port": 5900, "vnc_bridge_port": 6100,
            "rdp_port": 3389,
        }],
    )
    output = _run_gen_vnc_tokens(capsys)
    assert output.strip() == "srv-win: 127.0.0.1:6100"
