import asyncio, subprocess, sys, server
async def main():
    srv = await asyncio.start_server(server.handle_plugin, "127.0.0.1", server.INGEST_PORT)
    p = subprocess.Popen([sys.executable, "fake_plugin.py", "3"])
    await asyncio.sleep(1.5)
    print(server.list_tracks())
    print(server.get_levels("slot-3", 1.0))
    r = server.get_audio("slot-3", 1.0)
    print(r[0].type, r[0].mimeType, len(r[0].data))
    p.kill(); srv.close()
asyncio.run(main())
