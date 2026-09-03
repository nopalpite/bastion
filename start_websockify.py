"""Lance websockify (pont VNC, voir vnc_tls_bridge.py) avec TLS si activé.

supervisord.conf (command=...) ne peut pas conditionner ses arguments sur
une variable d'environnement — ce petit wrapper construit la ligne de
commande ici, dans le même langage que le reste du projet plutôt que dans
un script shell séparé. Voir tls.py pour la cohérence avec les deux autres
serveurs réseau (app.py, rdp_bridge.py).
"""
import os

import tls

argv = [
    "websockify",
    "--token-plugin=websockify.token_plugins.TokenFile",
    "--token-source=/app/vnc_tokens.conf",
]

cert_path, key_path = tls.resolve_cert_paths()
if cert_path:
    argv += [f"--cert={cert_path}", f"--key={key_path}", "--ssl-only"]

argv.append("6080")

os.execvp("websockify", argv)
