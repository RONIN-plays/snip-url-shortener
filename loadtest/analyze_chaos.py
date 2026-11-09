"""Print the chaos timeline as a table.  Usage: python loadtest/analyze_chaos.py <name>"""
import json
import sys
from pathlib import Path

name = sys.argv[1] if len(sys.argv) > 1 else "chaos"
rows = json.loads((Path(__file__).parent / "results" / f"{name}.json").read_text())

EVENTS = {15: "Redis STOP", 30: "Redis START", 40: "Postgres STOP", 55: "Postgres START"}
print(f"{'t(s)':>5} {'req':>6} {'302':>6} {'404':>5} {'5xx':>6} {'err':>5} {'ok%':>6} {'p50ms':>8} {'p95ms':>8} {'p99ms':>8}  event")
for r in rows:
    n = r["302"] + r["404"] + r["429"] + r["5xx"] + r["err"]
    if n == 0:
        continue
    ok = 100 * (r["302"] + r["404"]) / n          # 404 is a correct answer for unknown codes
    f = lambda v: f"{v:8.1f}" if v is not None else "       -"
    ev = ", ".join(e for t, e in EVENTS.items() if r["t"] <= t < r["t"] + 5)
    print(f"{r['t']:>5} {n:>6} {r['302']:>6} {r['404']:>5} {r['5xx']:>6} {r['err']:>5} {ok:>5.1f}% "
          f"{f(r['p50'])} {f(r['p95'])} {f(r['p99'])}  {ev}")
