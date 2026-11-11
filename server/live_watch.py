"""Listen for real plugin connections and print levels for N seconds."""
import asyncio, sys, server
async def main(n):
    srv = await asyncio.start_server(server.handle_plugin, "127.0.0.1", server.INGEST_PORT)
    for _ in range(n):
        await asyncio.sleep(1)
        for k, t in server.tracks.items():
            print(k, "connected" if t.connected else "dropped", f"sr={t.sample_rate:.0f}",
                  f"frames={t.total_frames}", server.get_levels(k, 0.5), flush=True)
    srv.close()
asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 15))
