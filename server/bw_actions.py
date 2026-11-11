import json, sys, server
for f in sys.argv[1:]:
    r = server.osc_send_recv("/actions", {"filter": f, "limit": 60})
    print(f"## filter '{f}': {r.get('total_matching')} matches" if r.get("ok") else r)
    for a in r.get("actions", []): print(f"  {a['id']:48} | {a['name']} [{a['category']}]")
