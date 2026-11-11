"""Stub of the script's tap jobs: checks tap_status / ensure_tap decisions (isolated ports, no real Bitwig)."""
import asyncio, json, socket, threading, server
server.CONTROL_PORT = 17883
state = {"has_tap": {"foo": True, "Bass": False}, "added": []}
cur = {}
def stub():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", server.CONTROL_PORT)); s.settimeout(8)
    while True:
        try: data, _ = s.recvfrom(65536)
        except socket.timeout: return
        a, i = server._osc_read_str(data, 0); tags, i = server._osc_read_str(data, i)
        arg = json.loads(server._osc_read_str(data, i)[0]) if tags == ",s" else None
        if a == "/tap_status":
            cur["log"] = [json.dumps({"has_tap": True, "slot": 3} if state["has_tap"][arg["track"]] else {"has_tap": False})]
            body = {"ok": True, "started": True}
        elif a == "/add_tap":
            state["added"].append(arg); cur["log"] = [json.dumps({"added": True, "slot": arg["slot"]})]
            server.tracks[f"slot-{arg['slot']}"] = server.Track(arg["slot"], 48000, 2)   # the plugin connects
            body = {"ok": True, "started": True}
        elif a == "/job": body = {"ok": True, "state": "done", "log": cur["log"]}
        else: body = {"ok": False}
        s.sendto(server._osc_pad(b"/reply") + server._osc_pad(b",s") + server._osc_pad(json.dumps(body).encode()), ("127.0.0.1", server.REPLY_PORT))
async def main():
    threading.Thread(target=stub, daemon=True).start(); await asyncio.sleep(0.2)
    print("foo (tap, not connected):", await server.tap_status("foo"))
    server.tracks["slot-3"] = server.Track(3, 48000, 2)
    print("foo (tap, connected)    :", await server.tap_status("foo"))
    print("Bass (no tap)           :", await server.tap_status("Bass"))
    print("ensure Bass             :", await server.ensure_tap("Bass"))
    print("ensure foo (has one)    :", await server.ensure_tap("foo"))
    print("add calls               :", state["added"])
asyncio.run(main())
