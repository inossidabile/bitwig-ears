"""Insert the tap plugin on a track (script job). usage: bw_add_tap.py <track>
Identify the new audio stream yourself by diffing list_tracks before/after (the server isn't in this process)."""
import sys, time, server
print(server.osc_send_recv("/add_tap", {"track": sys.argv[1], "slot": 1}))
for _ in range(40):
    time.sleep(0.5)
    r = server.osc_send_recv("/job")
    if r["state"] != "running":
        print("state:", r["state"]); [print("  ", l) for l in r["log"]]; break
