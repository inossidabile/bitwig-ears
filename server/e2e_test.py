"""Real end-to-end: launch server over stdio, feed it with the fake plugin, call tools via MCP client."""
import asyncio, os, subprocess, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command=sys.executable, args=["server.py"], env=dict(os.environ))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        await asyncio.sleep(0.5)
        p = subprocess.Popen([sys.executable, "fake_plugin.py", "2"])
        await asyncio.sleep(1.5)
        print((await s.call_tool("list_tracks", {})).content[0].text)
        print((await s.call_tool("get_levels", {"track": "slot-2"})).content[0].text)
        a = (await s.call_tool("get_audio", {"track": "slot-2", "seconds": 1})).content[0]
        print(a.type, a.mimeType, len(a.data))
        p.kill()
asyncio.run(main())
