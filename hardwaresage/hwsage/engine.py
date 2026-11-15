"""Question -> intent (rule or local LLM) -> allow-listed probes -> ledger -> grounded answer."""
import re
from . import answers as A
from .facts import Ledger
from .grounding import ungrounded
from .probes import Runner

TOPICS = [  # order matters: most specific first
 ("battery", r"\bbatter|charge cycle|battery health"),
 ("gpu", r"\bgpu\b|graphic|video card|nvidia|radeon|geforce"),
 ("storage", r"\bssd\b|\bhdd\b|nvme|\bdisk|storage|\bdrive|sata|hard drive"),
 ("memory", r"\bram\b|memory|dimm|\bslots?\b"),
 ("cpu", r"\bcpu\b|processor|\bcores?\b|threads?|clock|ghz"),
 ("system", r"\bmodel\b|laptop|bios|manufacturer|vendor|motherboard|\bos\b|distro|kernel|linux version|specs?\b|machine"),
]
UPGRADE = r"\badd\b|upgrade|expand|more ram|maximum|\bmax\b|free slot|how much (can|could)|can i (add|install|put)"

class Rules:
    def classify(self, q, last):
        ql, hits = q.lower(), [t for t, rx in TOPICS if re.search(rx, q.lower())]
        up = re.search(UPGRADE, ql)
        if up and ("memory" in hits or (not hits and "memory" in last)): return ["memory_upgrade"]
        if hits: return hits[:2]
        if re.search(r"\b(it|that|this|those|them|and|also|what about)\b", ql) and last: return last      # follow-up -> reuse topic
        return []

class Engine:
    def __init__(self, runner=None, llm=None):
        self.r, self.llm, self.last, self.rules = runner or Runner(), llm, [], Rules()

    def ingest(self, probe_names):
        L = Ledger()
        for name in dict.fromkeys(probe_names):
            res = self.r.run(name); ns = {"mem_slots": "memslots", "mem_max": "memslots"}.get(name, name)
            if res.status != "ok":
                L.unknown(f"{ns}.slots" if name == "mem_slots" else f"{ns}.max_bytes" if name == "mem_max" else ns + "." + {"storage": "disks", "gpu": "gpus"}.get(name, "value"), res.status, name)
                if name == "memory": L.unknown("memory.usable_bytes", res.status, name)
                if name == "cpu": L.unknown("cpu.model", res.status, name)
                continue
            for k, v in res.data.items():
                key = {"memslots.slots": 1}.get(f"{ns}.{k}") and f"{ns}.{k}" or f"{ns}.{k}"
                L.put(key, v, "bytes" if k.endswith("_bytes") else "mhz" if k.endswith("mhz") else "", f"{name} via {res.via}")
            if name == "mem_max" and res.data.get("slot_count"): L.put("memslots.count", res.data["slot_count"], "", name)
        slots = L.get("memslots.slots")
        if slots and any(s["size_bytes"] for s in slots): L.put("memory.installed_bytes", sum(s["size_bytes"] or 0 for s in slots), "bytes", "mem_slots (derived)", "derived")
        return L

    def ask(self, q):
        intents = None
        if self.llm:
            i = self.llm.classify(q, self.last)
            intents = [i] if i in A.INTENTS else None          # validated: anything else is ignored
        intents = intents or self.rules.classify(q, self.last)
        if not intents:
            return {"answer": "I can answer questions about this machine's RAM, CPU, disks, GPU, battery, and model/OS. I don't know how to answer that from hardware data, so I won't guess.", "evidence": [], "trace": [], "intents": []}
        self.last = intents
        probes = [p for i in intents for p in A.INTENTS[i][0]]
        before = len(self.r.trace); L = self.ingest(probes)
        texts, ev = [], []
        for i in intents:
            fn = A.INTENTS[i][1]
            t, e = fn(L, q) if i == "storage" else fn(L); texts.append(t); ev += e
        draft = " ".join(texts); final = draft
        if self.llm:
            try:
                alt = self.llm.rephrase(q, draft)
                bad = ungrounded(alt, L, q)
                final = alt if not bad else draft + f"  [LLM rewrite discarded: ungrounded numbers {bad}]"
            except Exception: pass
        return {"answer": final, "evidence": ev, "trace": self.r.trace[before:], "intents": intents, "ledger": L.public()}
