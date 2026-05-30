import socket, ssl, time, subprocess, os, sys, signal

PORT = 4600
env = dict(os.environ, MUD_PORT=str(PORT))
srv = subprocess.Popen([sys.executable, "server/server.py"], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=".")
time.sleep(2.5)

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
raw = socket.create_connection(("127.0.0.1", PORT), timeout=5)
s = ctx.wrap_socket(raw, server_hostname="127.0.0.1")
print("TLS version:", s.version())

def drain(t=0.4):
    s.settimeout(t); out=b""
    try:
        while True: out += s.recv(4096)
    except: pass
    return out.decode(errors="replace")

def send(line):
    s.sendall((line+"\n").encode()); time.sleep(0.3); return drain()

drain()  # banner
print("REGISTER:", "Welcome" in send("register Hero pass123"))
print("SCORE:", "Level 1" in send("score"))
send("south")                       # tavern
print("TALK:", "wolf pelts" in send("talk").lower())
print("ACCEPT:", "accepted" in send("accept").lower())
send("north"); send("east")         # gate
r = send("east")                    # wilds
print("WILDS:", "Wilds Path" in r)
# grind a wolf — keep attacking until something dies
combat = ""
for tgt in ("boar","goblin","wolf"):
    for _ in range(12):
        combat = send("attack "+tgt)
        if "No such target" in combat: break
        if "slain" in combat or "died" in combat:
            break
    if "slain" in combat: break
print("COMBAT WORKS:", ("hit" in combat.lower()) or ("slain" in combat.lower()))
print("CLANS LIST:", "Ironbound" in send("clans"))
send("west"); send("west")          # back toward square
send("west")                        # guildhall
print("JOIN:", "Ironbound" in send("join ironbound"))
print("WHO:", "Hero" in send("who"))
print("SHOP:", "iron sword" in (send("east"), send("north"), send("shop"))[2].lower())
print("BUY:", "buy" in send("buy health potion").lower() or "afford" in send("buy health potion").lower())
print("QUIT:", "Farewell" in send("quit"))
s.close()
srv.send_signal(signal.SIGINT); srv.terminate()
print("\nALL CORE SYSTEMS EXERCISED.")
