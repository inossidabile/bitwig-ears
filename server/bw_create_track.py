"""Create an instrument track and optionally load a preset. usage: bw_create_track.py <name> [preset path]"""
import sys, time, server
arg = {"name": sys.argv[1], "preset_path": sys.argv[2] if len(sys.argv) > 2 else None}
print(server.osc_send_recv("/create_track", arg))
seen = 0
for _ in range(40):
    time.sleep(0.5)
    r = server.osc_send_recv("/job")
    for line in r["log"][seen:]: print("  ", line)
    seen = len(r["log"])
    if r["state"] != "running": print("state:", r["state"]); break
