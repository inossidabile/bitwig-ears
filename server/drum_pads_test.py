"""Stub of the script's /drum_pads job: checks note = first_note + index (isolated ports)."""
import asyncio, json, socket, threading, server
server.CONTROL_PORT = 17882
def stub():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", server.CONTROL_PORT)); s.settimeout(5)
    while True:
        try: data, _ = s.recvfrom(65536)
        except socket.timeout: return
        a, _i = server._osc_read_str(data, 0)
        body = {"ok": True, "started": True} if a == "/drum_pads" else {"ok": True, "state": "done", "log": [
            "cursor track = foo", "device=V9 Breaks Kit hasDrumPads=true scroll=36",
            json.dumps([{"index": 0, "name": "Breaks Kick"}, {"index": 1, "name": "Breaks Snare"}, {"index": 6, "name": "Breaks Rimshot"}])]}
        s.sendto(server._osc_pad(b"/reply") + server._osc_pad(b",s") + server._osc_pad(json.dumps(body).encode()), ("127.0.0.1", server.REPLY_PORT))
async def main():
    threading.Thread(target=stub, daemon=True).start(); await asyncio.sleep(0.2)
    print(await server.drum_pads("foo"))
asyncio.run(main())
