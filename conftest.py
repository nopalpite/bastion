"""Fixtures partagées par les tests.

Un conftest.py à la racine (plutôt que dans tests/) fait que pytest ajoute
la racine du dépôt à sys.path — nécessaire ici car les modules de l'appli
(store.py, credentials.py...) sont des fichiers plats, pas un package.
"""
import pytest
from cryptography.fernet import Fernet

import gen_vnc_tokens
import store


@pytest.fixture
def machines_file(tmp_path, monkeypatch):
    """Redirige store.py vers un machines.yaml temporaire et vide pour
    chaque test, afin de ne jamais lire/écrire le fichier réel du projet.

    Monkeypatcher store.MACHINES_FILE (pas config.MACHINES_FILE): store.py
    fait "from config import MACHINES_FILE", qui copie la valeur au moment
    de l'import — patcher config.MACHINES_FILE après coup n'aurait aucun
    effet sur le nom déjà lié dans le module store.

    Redirige aussi gen_vnc_tokens.TOKENS_FILE (même raison qu'au-dessus) :
    add_machine/update_machine/delete_machine régénèrent ce fichier à
    chaque appel (voir store._regenerate_vnc_tokens) — sans ce patch, les
    tests écriraient pour de vrai dans vnc_tokens.conf à la racine du
    projet."""
    path = tmp_path / "machines.yaml"
    path.write_text("rooms: []\nmachines: []\n", encoding="utf-8")
    monkeypatch.setattr(store, "MACHINES_FILE", str(path))
    monkeypatch.setattr(gen_vnc_tokens, "TOKENS_FILE", str(tmp_path / "vnc_tokens.conf"))
    return path


@pytest.fixture
def credentials_key(monkeypatch):
    """Active le chiffrement des identifiants mémorisés pour la durée du
    test (credentials.py lit BASTION_CREDENTIALS_KEY depuis l'environnement
    à chaque appel, donc monkeypatch.setenv fonctionne ici, contrairement
    à config.ADMIN_USER/MACHINES_FILE qui sont lus une fois à l'import)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("BASTION_CREDENTIALS_KEY", key)
    return key
