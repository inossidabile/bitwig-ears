"""Real-Bitwig check of the v0.8 features. Reads first; writes only restore-safe values."""
import asyncio, json, server
def show(label, r, n=700):
    print(f"## {label}\n{json.dumps(r)[:n]}\n")
async def main():
    t0 = await server.transport_settings(); show("transport_settings()", t0)
    tr = await server.bitwig_tracks(limit=6); show("bitwig_tracks(limit=6)", tr, 1500)
    show("bitwig_meters()", await server.bitwig_meters(), 600)
    if not (t0.get("ok") and tr.get("ok") and tr.get("tracks")): return
    # restore-safe writes: same tempo, metronome flipped then restored, volume set to its current value
    show("tempo -> same value", await server.transport_settings(tempo_bpm=t0["tempo_bpm"]), 300)
    m = t0["metronome"]
    show("metronome flip", await server.transport_settings(metronome=not m), 300)
    show("metronome restore", await server.transport_settings(metronome=m), 300)
    first = tr["tracks"][0]
    show(f"volume of {first['name']} -> same value", await server.set_bitwig_track(first["id"], volume=first["volume"]))
    show("re-read track", (await server.bitwig_tracks(limit=1)), 500)
asyncio.run(main())
