"""Tests pour vnc_tls_bridge.py.

Les vecteurs ci-dessous ne sont pas inventés: le bit-reversal est vérifié
contre un exemple publié indépendamment (mot de passe "12345678" -> clé
DES "8c4ccc2cac6cec1c"), et le résultat DES complet contre une sortie
OpenSSL réelle (openssl enc -des-ecb -provider legacy), pas seulement
contre l'implémentation de ce module elle-même — sinon un bug reproduit
à l'identique côté test ne prouverait rien.

La négociation VeNCrypt complète (TLS, ack de polarité inversée, TOFU du
certificat, relais bidirectionnel) est vérifiée séparément par un harnais
avec un faux serveur VeNCrypt local (voir la conversation/PR associée) —
pas reproduite ici pour garder cette suite rapide et sans réseau ni
threads ni certificats à générer à chaque run."""
import vnc_tls_bridge as bridge


def test_reverse_bits_matches_published_example():
    # Voir vidarholen.net "The DES encryption used by VNC servers":
    # mot de passe "12345678" -> octets inversés bit à bit.
    assert bridge._vnc_des_key("12345678") == bytes.fromhex("8c4ccc2cac6cec1c")


def test_vnc_des_key_pads_short_password_with_nulls():
    key = bridge._vnc_des_key("ab")
    assert key == bytes(bridge._reverse_bits(b) for b in b"ab\x00\x00\x00\x00\x00\x00")


def test_vnc_des_key_truncates_to_eight_bytes():
    key = bridge._vnc_des_key("123456789999")  # plus de 8 caractères
    assert key == bridge._vnc_des_key("12345678")


def test_challenge_response_matches_openssl_des_ecb_ground_truth():
    # Vérifié via: openssl enc -des-ecb -provider legacy -provider default
    #   -K 8c4ccc2cac6cec1c -nopad (clé = _vnc_des_key("12345678"))
    challenge = bytes(range(16))
    response = bridge.vnc_challenge_response(challenge, "12345678")
    assert response == bytes.fromhex("83dd2b4dbd04367f28578fdd5b142740")


def test_challenge_response_requires_sixteen_byte_challenge():
    import pytest
    with pytest.raises(ValueError):
        bridge.vnc_challenge_response(b"trop court", "12345678")


def test_select_vencrypt_prefers_authenticated_encrypted_subtypes():
    # X509Vnc doit être préféré à X509None quand les deux sont proposés
    # (voir SUPPORTED_SUBTYPES: chiffré+authentifié d'abord).
    assert bridge.SUPPORTED_SUBTYPES.index(bridge.VENCRYPT_X509_VNC) < \
        bridge.SUPPORTED_SUBTYPES.index(bridge.VENCRYPT_X509_NONE)


# --- _current_machine_for_port: relecture de l'inventaire à chaque
# connexion (pas de "machine" figée au démarrage) — voir le docstring de
# la section "Service" dans vnc_tls_bridge.py pour pourquoi c'est
# nécessaire (machine ajoutée/modifiée/supprimée après coup, sans
# redémarrer ce process). --------------------------------------------

def test_current_machine_for_port_finds_match(machines_file):
    import store
    machine_id = store.add_machine(name="Srv", os_type="linux", host="10.0.0.1", vnc_port=5900)
    port = store.get_machine(machine_id)["vnc_bridge_port"]

    found = bridge._current_machine_for_port(port)
    assert found is not None
    assert found["id"] == machine_id


def test_current_machine_for_port_reflects_live_edits(machines_file):
    import store
    machine_id = store.add_machine(name="Srv", os_type="linux", host="10.0.0.1", vnc_port=5900)
    port = store.get_machine(machine_id)["vnc_bridge_port"]

    # Modifie l'hôte APRÈS le "démarrage" simulé du pont (pas de process
    # à relancer ici, juste un nouvel appel — c'est justement le point:
    # aucun état de la machine n'est mis en cache entre deux connexions).
    store.update_machine(
        machine_id, name="Srv", os_type="linux", host="10.0.0.99", vnc_port=5900,
    )

    found = bridge._current_machine_for_port(port)
    assert found["host"] == "10.0.0.99"


def test_current_machine_for_port_returns_none_after_deletion(machines_file):
    import store
    machine_id = store.add_machine(name="Srv", os_type="linux", host="10.0.0.1", vnc_port=5900)
    port = store.get_machine(machine_id)["vnc_bridge_port"]

    store.delete_machine(machine_id)

    assert bridge._current_machine_for_port(port) is None


def test_current_machine_for_port_returns_none_for_unused_port(machines_file):
    assert bridge._current_machine_for_port(65000) is None


# --- probe_available: sonde de disponibilité pour monitor.py -----------
#
# Voir son docstring pour le raisonnement complet: doit s'arrêter à la
# lecture de la poignée de main (comme _probe), ne JAMAIS choisir de type
# de sécurité ni tenter d'authentification — c'est précisément ce que la
# doc officielle RealVNC (BlacklistThreshold) documente comme déclenchant
# son blacklistage anti-bruteforce ("unsuccessful authentication
# attempts"), pas une simple connexion TCP suivie d'une lecture.

class _FakeSock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_probe_available_returns_true_on_success(monkeypatch):
    fake_sock = _FakeSock()
    monkeypatch.setattr(bridge, "_probe",
                         lambda machine, timeout: (fake_sock, b"", b"", b"", [1, 2]))
    assert bridge.probe_available({"host": "10.0.0.1", "vnc_port": 5900}) is True
    assert fake_sock.closed  # ne laisse pas la connexion ouverte derrière lui


def test_probe_available_returns_false_on_connection_error(monkeypatch):
    def raise_oserror(machine, timeout):
        raise OSError("connection refused")
    monkeypatch.setattr(bridge, "_probe", raise_oserror)
    assert bridge.probe_available({"host": "10.0.0.1", "vnc_port": 5900}) is False


def test_probe_available_returns_false_on_protocol_error(monkeypatch):
    def raise_bridge_error(machine, timeout):
        raise bridge.VncBridgeError("le serveur refuse")
    monkeypatch.setattr(bridge, "_probe", raise_bridge_error)
    assert bridge.probe_available({"host": "10.0.0.1", "vnc_port": 5900}) is False


def test_probe_available_never_chooses_a_security_type(monkeypatch):
    # Garde-fou du raisonnement de sécurité ci-dessus, sous forme
    # d'assertion plutôt que d'un simple commentaire: probe_available ne
    # doit jamais appeler _choose_security_type (donc jamais s'engager
    # sur un type, jamais tenter d'authentification).
    called = []
    monkeypatch.setattr(bridge, "_choose_security_type", lambda *a: called.append(a))
    fake_sock = _FakeSock()
    monkeypatch.setattr(bridge, "_probe", lambda machine, timeout: (fake_sock, b"", b"", b"", [2]))
    bridge.probe_available({"host": "10.0.0.1", "vnc_port": 5900})
    assert called == []
