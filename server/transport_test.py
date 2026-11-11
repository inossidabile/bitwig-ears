"""Stub of the Bitwig OSC script + call the transport tool."""
import asyncio, json, socket, struct, threading, server
state = {"playing": False}
def stub():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", server.CONTROL_PORT)); s.settimeout(5)
    while True:
        try: data, peer = s.recvfrom(1024)
        except socket.timeout: return
        a = data[:data.index(b"\0")].decode()
        if a in ("/play", "/restart"): state["playing"] = True
        if a == "/stop": state["playing"] = False
        body = json.dumps({"ok": True, "playing": state["playing"]}).encode()
        s.sendto(server._osc_pad(b"/reply") + server._osc_pad(b",s") + server._osc_pad(body), ("127.0.0.1", server.REPLY_PORT))
async def main():
    server.CONTROL_PORT = 17879  # never collide with real Bitwig
    print("no bitwig:", await server.transport("play"))
    threading.Thread(target=stub, daemon=True).start(); await asyncio.sleep(0.2)
    for a in ("status", "play", "stop", "restart"):
        print(a, await server.transport(a))
asyncio.run(main())
