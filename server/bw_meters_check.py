import asyncio, json, server
async def main():
    t0 = await server.transport_settings(); pos = t0["position_beats"]
    tr = await server.bitwig_tracks(); print("tracks:", tr["total"], [(t["id"], t["name"]) for t in tr["tracks"]])
    print("play:", await server.transport("play")); await asyncio.sleep(2.5)
    for i in range(3):
        m = await server.bitwig_meters()
        print(f"meters #{i}", m["playing"], [(x["name"], x["peak"], x["rms"]) for x in m["meters"]]); await asyncio.sleep(0.7)
    print("stop:", await server.transport("stop"))
    print("restore position:", (await server.transport_settings(position_beats=pos))["position_beats"], "was", pos)
asyncio.run(main())
