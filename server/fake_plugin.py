"""Stand-in for the CLAP plugin: sends a 440 Hz sine as slot N."""
import json, math, os, socket, struct, sys, time
slot = int(sys.argv[1]) if len(sys.argv) > 1 else 1
sr = 48000
s = socket.create_connection(("127.0.0.1", int(os.environ.get("BITWIG_EARS_INGEST_PORT", "7878"))))
def send(t, p): s.sendall(struct.pack("<BI", t, len(p)) + p)
send(1, json.dumps({"slot": slot, "sample_rate": sr, "channels": 2, "plugin_version": "fake"}).encode())
i = 0
while True:
    blk = b"".join(struct.pack("<ff", *(0.5 * math.sin(2 * math.pi * 440 * (i + k) / sr),) * 2) for k in range(480))
    send(2, blk); i += 480; time.sleep(0.01)
