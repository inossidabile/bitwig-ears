"""Does a job that moves Bitwig's selection put it back? (real Bitwig)"""
import json, sys, time, server
def sel(label):
    r = server.osc_send_recv("/selection"); r.pop("ok", None); print(f"{label:8}", json.dumps(r)); return r
def job(address, arg):
    print(server.osc_send_recv(address, arg))
    for _ in range(40):
        time.sleep(0.5); r = server.osc_send_recv("/job")
        if r["state"] != "running": return r
before = sel("before")
r = job(sys.argv[1], {"track": sys.argv[2]}); print("job:", r["state"]); [print("   ", l[:150]) for l in r["log"]]
time.sleep(0.5); after = sel("after")
print("\nSELECTION RESTORED" if before == after else "\nSELECTION CHANGED")
