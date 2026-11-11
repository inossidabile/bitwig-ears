"""Record an existing launcher clip into the arrangement (1 bar, from 1.1).
usage: bw_record.py <track name or id> [slot index, default 0]"""
import sys, time, server
arg = {"track": sys.argv[1], "slot": int(sys.argv[2]) if len(sys.argv) > 2 else 0}
print(server.osc_send_recv("/record_break", arg))
seen = 0
for _ in range(60):
    time.sleep(0.5)
    r = server.osc_send_recv("/job")
    for line in r["log"][seen:]: print("  ", line)
    seen = len(r["log"])
    if r["state"] != "running": print("state:", r["state"]); break
