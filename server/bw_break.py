"""Start the break-to-arrangement job on a track and print its step log.
usage: bw_break.py <track name or id> [--no-paste] [--pos BEATS]"""
import sys, time, server
track = sys.argv[1]
arg = {"track": track}
if "--no-paste" in sys.argv: arg["paste"] = False
if "--pos" in sys.argv: arg["position_beats"] = float(sys.argv[sys.argv.index("--pos") + 1])
print(server.osc_send_recv("/make_break", arg))
seen = 0
for _ in range(30):
    time.sleep(0.4)
    r = server.osc_send_recv("/job")
    for line in r["log"][seen:]: print("  ", line)
    seen = len(r["log"])
    if r["state"] != "running": print("state:", r["state"]); break
