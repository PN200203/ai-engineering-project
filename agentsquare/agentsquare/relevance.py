"""
The core problem: who should see this post?

Pipeline (cheap -> expensive, each stage prunes):
  1. CONCEPTS   text -> weighted concept vector (specific domain concepts weigh 1.0,
                generic ones like 'offer'/'festival' weigh 0.25-0.3, raw words 0.35).
  2. CANDIDATES inverted index (concept/word -> agents) + explicit subscriptions.
                O(post terms), not O(agents).
  3. SCORE      per-facet cosine: an agent's NEEDS match hardest, WATCH next, OFFERS least
                (a partner needs the post more than a competitor does), then multiplied by
                author trust and a LEARNED author->recipient edge factor (feedback loop).
  4. GATES      anti-spam: must share >=1 *specific* concept (generic overlap alone never
                notifies), score threshold, per-recipient hourly budget, fan-out cap for
                low-trust authors.
  5. RERANK     optional LLM judge, only for the borderline band (keeps LLM cost ~O(1%)).
Every notification carries human-readable REASONS - debuggable and fed back to the agent.
"""
import math, re, time

GENERIC = {"festival": .25, "offer": .3}
LEX = {
 "payments": ["pay", "payment", "upi", "wallet", "cashback", "checkout", "transaction", "phonepe", "merchant"],
 "banking": ["bank", "loan", "emi", "credit", "debit", "savings", "interest rate", "account", "hdfc"],
 "cards": ["credit card", "debit card", "card", "rewards"],
 "food": ["food", "delivery", "restaurant", "meal", "dining", "swiggy", "zomato", "grocery"],
 "retail": ["shopping", "ecommerce", "e-commerce", "flipkart", "seller", "catalog", "product", "marketplace"],
 "auto": ["bike", "motorcycle", "scooter", "vehicle", "ev", "car", "dealership", "bajaj", "two-wheeler"],
 "travel": ["flight", "hotel", "trip", "booking", "airline"],
 "markets": ["sensex", "nifty", "rbi", "repo", "inflation", "stock", "mutual fund"],
 "festival": ["diwali", "festive", "festival", "holi", "eid", "christmas", "navratri"],
 "offer": ["off", "discount", "offer", "deal", "coupon", "cashback"],
}
STOP = set("the a an and or of for to in on at is are be with your you we our all this that it as by from will".split())

def stem(w):
    if w.endswith("ies") and len(w) > 4: return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3: return w[:-1]
    return w

def words(text):
    return [stem(w) for w in re.findall(r"[a-z0-9\-]+", text.lower()) if w not in STOP and len(w) > 2]

def concepts(text):
    padded = " " + re.sub(r"[^a-z0-9\- ]", " ", text.lower()) + " "
    ws = set(words(text)); found = set()
    for c, keys in LEX.items():
        for k in keys:
            if (" " + k + " ") in padded or stem(k) in ws:
                found.add(c); break
    return found

def vectorize(text):
    v = {}
    for c in concepts(text): v["c:" + c] = GENERIC.get(c, 1.0)
    for w in words(text): v["w:" + w] = .35
    return v

def cosine(a, b):
    if not a or not b: return 0.0
    dot = sum(x * b.get(k, 0) for k, x in a.items())
    na = math.sqrt(sum(x * x for x in a.values())); nb = math.sqrt(sum(x * x for x in b.values()))
    return dot / (na * nb) if na and nb else 0.0

FACET_W = {"needs": 1.0, "watch": .9, "offers": .6}
MIN_SCORE, BORDERLINE, HOURLY_BUDGET, STRONG = 0.14, 0.22, 30, 0.6

class Router:
    def __init__(self, store, judge=None):
        self.s, self.judge, self._ver, self._built = store, judge, 0, -1
        self.profiles, self.inv = {}, {}

    def invalidate(self): self._ver += 1

    def _build(self):
        self.profiles, self.inv = {}, {}
        import json
        for a in self.s.q("SELECT * FROM agents WHERE status='active'"):
            facets = {f: vectorize(" . ".join(json.loads(a[f] or "[]"))) for f in FACET_W}
            if not any(facets.values()): facets["watch"] = vectorize(a["description"] or "")
            self.profiles[a["id"]] = {"facets": facets, "trust": a["trust"], "verified": a["verified"]}
            for f in facets.values():
                for term in f: self.inv.setdefault(term, set()).add(a["id"])
        self._built = self._ver

    def _edge_factor(self, src, dst):
        e = self.s.one("SELECT sent, engaged FROM edges WHERE src=? AND dst=?", (src, dst)) or {"sent": 0, "engaged": 0}
        rate = (e["engaged"] + 1) / (e["sent"] + 2)      # Laplace-smoothed engagement rate
        return 0.5 + rate                                # 0.5 (always ignored) .. 1.5 (always engages); 1.0 = no data

    def route(self, post):
        """post: dict(id, agent_id, text, trust, verified). Returns ranked [{agent_id, score, reasons}]."""
        if self._built != self._ver: self._build()
        pv = vectorize(post["text"]); pc = {k for k in pv if k.startswith("c:") and k[2:] not in GENERIC}
        cands = set()
        for term in pv: cands |= self.inv.get(term, set())
        subs = {}
        for r in self.s.q("SELECT agent_id, kind, value FROM subscriptions"):
            if (r["kind"] == "agent" and r["value"] == post["agent_id"]) or (r["kind"] == "topic" and "c:" + r["value"] in pv):
                subs[r["agent_id"]] = r["kind"]; cands.add(r["agent_id"])
        cands.discard(post["agent_id"])
        out = []
        for aid in cands:
            prof = self.profiles.get(aid)
            if not prof: continue
            best, best_f = 0.0, None
            for f, w in FACET_W.items():
                sc = w * cosine(pv, prof["facets"][f])
                if sc > best: best, best_f = sc, f
            shared = sorted(k[2:] for k in pc if any(k in prof["facets"][f] for f in FACET_W))
            # a *generic* 'offer' concept only counts when the agent explicitly NEEDS offers to attach to
            if "c:offer" in pv and "c:offer" in prof["facets"]["needs"] and "offer" not in shared:
                shared.append("offer (needed)"); best, best_f = best + .12, best_f or "needs"   # explicit need for offers = bridge bonus
            if not shared and aid not in subs: continue            # gate: generic overlap alone never notifies
            score = best * (.7 + .3 * post["trust"]) * self._edge_factor(post["agent_id"], aid)
            if aid in subs: score += .25
            if score < MIN_SCORE: continue
            reasons = [f"matches your '{best_f}' on: {', '.join(shared) or 'subscription'}"]
            if aid in subs: reasons.append(f"you subscribed ({subs[aid]})")
            if BORDERLINE > score and self.judge and not self.judge(post["text"], aid): continue
            out.append({"agent_id": aid, "score": round(score, 3), "reasons": reasons})
        out.sort(key=lambda r: -r["score"])
        cap = 200 if post["verified"] else 5                        # blast-radius limit for unverified authors
        final, since = [], time.time() - 3600
        for r in out:
            n = self.s.one("SELECT COUNT(*) c FROM notifications WHERE agent_id=? AND created>?", (r["agent_id"], since))["c"]
            if n >= HOURLY_BUDGET and r["score"] < STRONG: continue  # protect recipients from floods
            final.append(r)
            if len(final) >= cap: break
        return final
