"""List the drum pads of a track's drum device. usage: bw_drum_pads.py <track>"""
import json, sys, time, server
print(server.osc_send_recv("/drum_pads", {"track": sys.argv[1]}))
for _ in range(20):
    time.sleep(0.4)
    r = server.osc_send_recv("/job")
    if r["state"] != "running":
        print("state:", r["state"])
        for line in r["log"]: print("  ", line)
        break
