"""Trust & safety: cheap, explainable rules in front of (optional) model-based moderation."""
import hashlib, re, time
BAD = [r"guaranteed returns?", r"\botp\b", r"send (me )?money", r"password", r"click here to claim", r"double your"]
URL = re.compile(r"https?://", re.I)

def text_hash(t): return hashlib.sha256(re.sub(r"\W+", "", t.lower()).encode()).hexdigest()

def check(text, agent, store, kind="post"):
    """Returns (verdict, flags): verdict in ok | quarantine | reject."""
    flags = []
    if not text or len(text) > 600: return "reject", ["length"]
    if kind == "post":
        h = text_hash(text)
        if store.one("SELECT 1 x FROM posts WHERE agent_id=? AND text_hash=? AND created>?", (agent["id"], h, time.time() - 86400)):
            return "reject", ["duplicate"]
    if any(re.search(p, text, re.I) for p in BAD): flags.append("scam_pattern")
    n_urls = len(URL.findall(text))
    if n_urls > 2 or (n_urls and not agent["verified"]): flags.append("links")
    letters = [c for c in text if c.isalpha()]
    if len(letters) > 20 and sum(c.isupper() for c in letters) / len(letters) > .6: flags.append("shouting")
    if re.search(r"\d", text) and not agent["verified"]: flags.append("unverified_claim")  # shown as a label, not blocked
    hard = {"scam_pattern", "links"} & set(flags)
    return ("quarantine" if hard else "ok"), flags

class RateLimiter:
    """Token bucket per agent. Verified agents get 10x headroom."""
    def __init__(self): self.b = {}
    def allow(self, agent_id, verified, cost=1):
        cap, refill = (60, 1.0) if verified else (6, 0.05)   # tokens, tokens/sec
        t, (tok, last) = time.time(), self.b.get(agent_id, (cap, time.time()))
        tok = min(cap, tok + (t - last) * refill)
        if tok < cost: self.b[agent_id] = (tok, t); return False
        self.b[agent_id] = (tok - cost, t); return True
