import asyncio, server
async def main():
    for a in ("status", "play"):
        print(a, await server.transport(a))
    await asyncio.sleep(3)
    for a in ("stop", "status"):
        print(a, await server.transport(a))
asyncio.run(main())
