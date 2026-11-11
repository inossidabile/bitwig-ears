"""Stub Bitwig OSC script: checks the new tools encode/decode correctly (never touches real Bitwig)."""
import asyncio, json, socket, threading, server
server.CONTROL_PORT = 17880
seen = []
def stub():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", server.CONTROL_PORT)); s.settimeout(4)
    while True:
        try: data, _ = s.recvfrom(65536)
        except socket.timeout: return
        a, i = server._osc_read_str(data, 0); tags, i = server._osc_read_str(data, i)
        arg = json.loads(server._osc_read_str(data, i)[0]) if tags == ",s" else None
        seen.append((a, arg))
        body = json.dumps({"ok": True, "echo": a, "arg": arg}).encode()
        s.sendto(server._osc_pad(b"/reply") + server._osc_pad(b",s") + server._osc_pad(body), ("127.0.0.1", server.REPLY_PORT))
async def main():
    threading.Thread(target=stub, daemon=True).start(); await asyncio.sleep(0.2)
    print(await server.transport_settings(tempo_bpm=128, loop=True))
    print(await server.transport_settings())
    print(await server.bitwig_tracks(limit=5))
    print(await server.set_bitwig_track("Drums", volume=0.7, mute=False))
    print(await server.set_bitwig_track("Drums"))
    print(await server.bitwig_meters())
    print("sent:", seen)
asyncio.run(main())
