"""
The FACT LEDGER is the single source of truth for an answer.
Every value has an id, a unit, provenance (which probe produced it) and a status.
Answers may only state numbers that exist here (see grounding.py).
"""
import re
from dataclasses import dataclass

@dataclass
class Fact:
    id: str; value: object; unit: str = ""; source: str = ""; status: str = "ok"   # ok | derived | unknown:<why>

class Ledger:
    def __init__(self): self.f = {}
    def put(self, id, value, unit="", source="", status="ok"): self.f[id] = Fact(id, value, unit, source, status)
    def get(self, id, default=None): x = self.f.get(id); return x.value if x and x.status in ("ok", "derived") else default
    def unknown(self, id, why, source=""): self.f[id] = Fact(id, None, "", source, "unknown:" + why)
    def why_unknown(self, id): x = self.f.get(id); return x.status.split(":", 1)[1] if x and x.status.startswith("unknown") else None
    def numbers(self):
        """Every number a truthful answer may contain, in the units a human would use."""
        ok = set()
        def add(x):
            for d in (0, 1, 2): ok.add(f"{x:.{d}f}".rstrip("0").rstrip(".") if d else f"{x:.0f}")
        for x in self.f.values():
            if x.status == "unknown" or x.value is None: continue
            for m in re.findall(r"\d+(?:\.\d+)?", str(x.value)): ok.add(m)
            if isinstance(x.value, (int, float)) and not isinstance(x.value, bool):
                v = float(x.value); add(v)
                if x.unit == "bytes": add(v / 2**30); add(v / 2**20); add(v / 1e9); add(v / 2**40); add(v / 1e12)
                if x.unit == "mhz": add(v / 1000)
        return ok
    def public(self): return {k: {"value": x.value, "unit": x.unit, "source": x.source, "status": x.status} for k, x in self.f.items()}
