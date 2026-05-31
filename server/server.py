#!/usr/bin/env python3
"""SecureMUD server. TLS + JSON-line protocol. One thread per client.

First run generates a self-signed cert (server.crt/server.key) if absent.
All gameplay traffic is encrypted end-to-end (TLS 1.2+).

Wire protocol: client sends raw command lines; server sends one JSON
event per line (see server/protocol.py). Strings handed to Client.send
are auto-wrapped as 'narrate' events for back-compat with the engine's
existing string returns."""
import socket, ssl, threading, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from engine import Engine, col
import protocol

HOST = os.environ.get("MUD_HOST", "0.0.0.0")
PORT = int(os.environ.get("MUD_PORT", "4443"))
CERT = os.path.join(os.path.dirname(__file__), "..", "server.crt")
KEY  = os.path.join(os.path.dirname(__file__), "..", "server.key")

BANNER = col(r"""
  ____                          __  __ _   _ ____
 / ___|  ___  ___ _   _ _ __ ___|  \/  | | | |  _ \
 \___ \ / _ \/ __| | | | '__/ _ \ |\/| | | | | | | |
  ___) |  __/ (__| |_| | | |  __/ |  | | |_| | |_| |
 |____/ \___|\___|\__,_|_|  \___|_|  |_|\___/|____/
        TLS-secured Multi-User Dungeon
""", "cyn")

def ensure_cert():
    if os.path.exists(CERT) and os.path.exists(KEY):
        return
    print("[*] Generating self-signed TLS certificate...")
    from datetime import datetime, timedelta
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"securemud.local")])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(datetime.utcnow())
                .not_valid_after(datetime.utcnow() + timedelta(days=3650))
                .sign(key, hashes.SHA256()))
        with open(KEY, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption()))
        with open(CERT, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
    except ImportError:
        # fall back to openssl CLI
        os.system(f'openssl req -x509 -newkey rsa:2048 -nodes -keyout "{KEY}" '
                  f'-out "{CERT}" -days 3650 -subj "/CN=securemud.local" 2>/dev/null')
    print("[*] Certificate ready.")

ENGINE = Engine()

class Client:
    def __init__(self, conn):
        self.conn = conn
        self.buf = b""
        self.session = None
    def send(self, payload):
        """Send a JSON event. Accepts a dict (event) or a str (auto-wrapped
        as a 'narrate' event so legacy callers keep working)."""
        if isinstance(payload, str):
            payload = protocol.narrate(payload)
        try:
            self.conn.sendall(protocol.encode(payload))
        except OSError:
            pass
    def recv_line(self):
        while b"\n" not in self.buf:
            chunk = self.conn.recv(1024)
            if not chunk:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode(errors="replace").rstrip("\r")

def handle_client(conn, addr):
    c = Client(conn)
    from engine import Session
    c.session = Session(c.send)
    try:
        c.send(BANNER)
        # ---- auth ----
        c.send("Type 'login <name> <pass>' or 'register <name> <pass>'")
        while True:
            line = c.recv_line()
            if line is None: return
            parts = line.split()
            if len(parts) == 3 and parts[0] in ("login", "register"):
                ok, err = ENGINE.authenticate(c.session, parts[1], parts[2],
                                               register=(parts[0] == "register"))
                if ok:
                    c.send(col(f"Welcome, {c.session.player.name}!", "grn"))
                    # cmd_look now emits a structured 'room' event and
                    # returns "" — skip the empty narrate.
                    out = ENGINE.handle(c.session, "look")
                    if out:
                        c.send(out)
                    break
                c.send(col(err, "red"))
            else:
                c.send(col("Format: login <name> <pass>  OR  register <name> <pass>", "red"))
        # ---- main loop ----
        while True:
            line = c.recv_line()
            if line is None: break
            out = ENGINE.handle(c.session, line)
            if out == "__QUIT__":
                c.send(col("Farewell, adventurer.", "cyn"))
                c.send(protocol.quit_())
                break
            if out:
                c.send(out)
    finally:
        ENGINE.disconnect(c.session)
        conn.close()

def main():
    ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    raw.bind((HOST, PORT))
    raw.listen(50)
    print(f"[*] SecureMUD listening on {HOST}:{PORT} (TLS)")
    while True:
        try:
            sock, addr = raw.accept()
            conn = ctx.wrap_socket(sock, server_side=True)
        except (ssl.SSLError, OSError) as e:
            print(f"[!] Handshake failed: {e}")
            continue
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    main()
