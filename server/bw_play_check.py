import asyncio, server
async def snap(tag):
    t = await server.transport_settings()
    print(f"{tag:10} playing={t['playing']} pos={t['position_beats']}")
async def main():
    pos = (await server.transport_settings())["position_beats"]
    await snap("before")
    print(await server.transport("play"))
    for i in range(6):
        await asyncio.sleep(0.5); await snap(f"+{(i+1)*0.5}s")
    await server.transport("stop"); await asyncio.sleep(0.3); await snap("after stop")
    await server.transport_settings(position_beats=pos); await asyncio.sleep(0.3); await snap("restored")
asyncio.run(main())
