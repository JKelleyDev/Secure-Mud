#!/usr/bin/env python3
"""SecureMUD terminal client. Connects over TLS.

Usage:  python3 client.py [host] [port]
By default trusts the server cert (self-signed). For production, point
--cafile at the server's cert for full verification."""
import socket, ssl, sys, threading, argparse

def reader(sock):
    buf = b""
    while True:
        try:
            data = sock.recv(4096)
        except OSError:
            break
        if not data:
            print("\n[connection closed]")
            break
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            sys.stdout.write(line.decode(errors="replace") + "\n")
            sys.stdout.flush()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?", default="127.0.0.1")
    ap.add_argument("port", nargs="?", type=int, default=4443)
    ap.add_argument("--cafile", help="CA/cert file to verify server (else trust-on-first-use)")
    args = ap.parse_args()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if args.cafile:
        ctx.load_verify_locations(args.cafile)
    else:
        # self-signed dev mode: encrypt but don't verify identity
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    raw = socket.create_connection((args.host, args.port))
    sock = ctx.wrap_socket(raw, server_hostname=args.host)
    print(f"[*] Connected securely ({sock.version()}) to {args.host}:{args.port}\n")

    threading.Thread(target=reader, args=(sock,), daemon=True).start()
    try:
        for line in sys.stdin:
            sock.sendall(line.encode())
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()

if __name__ == "__main__":
    main()
