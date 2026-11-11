"""Stub OSC script: checks add_clip_to_arrangement validation and happy path (isolated ports, no real Bitwig)."""
import asyncio, json, socket, threading, server
server.CONTROL_PORT = 17881
got = []
def stub():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", server.CONTROL_PORT)); s.settimeout(6)
    polls = 0
    while True:
        try: data, _ = s.recvfrom(65536)
        except socket.timeout: return
        a, i = server._osc_read_str(data, 0); tags, i = server._osc_read_str(data, i)
        arg = json.loads(server._osc_read_str(data, i)[0]) if tags == ",s" else None
        if a == "/add_clip": got.append(arg); body = {"ok": True, "started": True, "slot": 0}
        elif a == "/instrument": body = {"ok": True, "track": "t2", "devices": [{"name": "Drum Machine", "drum_pads": True}]}
        elif a == "/job":
            polls += 1
            body = {"ok": True, "state": "running" if polls < 2 else "done", "log": ["step a", "step b"]}
        else: body = {"ok": False}
        s.sendto(server._osc_pad(b"/reply") + server._osc_pad(b",s") + server._osc_pad(json.dumps(body).encode()), ("127.0.0.1", server.REPLY_PORT))
async def main():
    threading.Thread(target=stub, daemon=True).start(); await asyncio.sleep(0.2)
    f = lambda track, **kw: server.add_clip_to_arrangement(track, kw.pop("description", "test clip"), **kw)
    print("instrument:", await server.track_instrument("foo"))
    print("empty    :", await f("foo", notes=[]))
    print("bad length:", await f("foo", notes=[{"start": 0, "pitch": 36}], length_beats=40))
    print("bad note :", await f("foo", notes=[{"pitch": 36}]))
    print("too many :", await f("foo", notes=[{"start": 0, "pitch": 36}] * 201))
    kick = [{"start": 0, "pitch": 36, "velocity": 120, "duration": .25}, {"start": 2, "pitch": 36}]
    print("notes ok :", await f("foo", notes=kick))
    print("2nd call :", await f("foo", notes=[{"start": 0, "pitch": 60, "velocity": 90, "duration": 1}], start_beats=4, length_beats=2))
    print("sent #1  :", json.dumps(got[0]))
    print("sent #2  :", json.dumps(got[1]))
asyncio.run(main())
