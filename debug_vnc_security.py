"""Diagnostic RFB minimal: se connecte à un serveur VNC, fait la
négociation de version, et affiche la liste EXACTE des types de sécurité
proposés par le serveur — sans dépendre de libvncclient ou de quoi que ce
soit d'autre. Juste le protocole RFB brut (RFC 6143), pour savoir avec
certitude ce que le serveur annonce plutôt que de deviner.

Usage:
    python3 debug_vnc_security.py <host> <port>

Exemple:
    python3 debug_vnc_security.py 172.17.32.56 5900
"""
import socket
import sys

# Table des types de sécurité RFB connus, pour affichage lisible. Les
# valeurs non listées s'affichent quand même (juste avec un nom "inconnu").
SECURITY_TYPE_NAMES = {
    0: "Invalid",
    1: "None (pas d'authentification)",
    2: "VNC Authentication (mot de passe standard)",
    5: "RA2 (RealVNC, propriétaire)",
    6: "RA2ne (RealVNC, propriétaire)",
    16: "Tight",
    17: "Ultra",
    18: "TLS",
    19: "VeNCrypt",
    20: "SASL",
    21: "MD5 hash authentication (Apple)",
    22: "xvp (Colin Dean)",
    30: "Apple Remote Desktop",
    128: "__RealVNC (128) — non standard__",
    129: "__RealVNC (129) — non standard, souvent RSA-AES__",
    130: "__RealVNC (130) — non standard__",
}


def main(host, port):
    print(f"Connexion à {host}:{port}...")
    sock = socket.create_connection((host, port), timeout=5)

    # --- ProtocolVersion handshake ---
    server_version = sock.recv(12)
    print(f"Version serveur annoncée: {server_version!r}")

    # On répond avec la même version que le serveur (comportement standard
    # d'un client qui accepte ce que le serveur propose).
    sock.sendall(server_version)

    version_str = server_version.decode(errors="ignore").strip()
    # ex: "RFB 003.008" -> (3, 8)
    try:
        _, ver = version_str.split(" ")
        major, minor = (int(x) for x in ver.split("."))
    except Exception:  # noqa: BLE001
        print(f"Impossible de parser la version, arrêt: {version_str!r}")
        sock.close()
        return

    if (major, minor) < (3, 7):
        # RFB 3.3: le serveur impose directement UN type de sécurité
        # (4 octets), pas de liste/négociation.
        data = sock.recv(4)
        sec_type = int.from_bytes(data, "big")
        print(f"[RFB {major}.{minor}] Type de sécurité imposé (pas de choix côté client): "
              f"{sec_type} — {SECURITY_TYPE_NAMES.get(sec_type, 'inconnu')}")
    else:
        # RFB >= 3.7: le serveur envoie le NOMBRE de types proposés, puis
        # la liste (1 octet chacun).
        count_byte = sock.recv(1)
        if not count_byte:
            print("Le serveur a fermé la connexion avant d'envoyer la liste des types.")
            sock.close()
            return
        count = count_byte[0]

        if count == 0:
            # Le serveur refuse la connexion et explique pourquoi via une
            # chaîne de raison (longueur 4 octets + texte).
            reason_len = int.from_bytes(sock.recv(4), "big")
            reason = sock.recv(reason_len).decode(errors="ignore")
            print(f"Le serveur refuse la connexion. Raison: {reason}")
            sock.close()
            return

        types = list(sock.recv(count))
        print(f"[RFB {major}.{minor}] {count} type(s) de sécurité proposé(s) par le serveur:")
        for t in types:
            print(f"  - {t} : {SECURITY_TYPE_NAMES.get(t, 'inconnu (non documenté ici)')}")

    sock.close()
    print("\nTerminé. (Cette connexion de test est fermée sans authentification.)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], int(sys.argv[2]))
