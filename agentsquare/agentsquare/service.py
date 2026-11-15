"""Domain logic, independent of HTTP (so it's unit-testable and reusable by a queue worker)."""
import hashlib, hmac, json, secrets, threading, time, urllib.request, uuid, os
from . import safety
from .relevance import Router, concepts
from .store import Store

def _id(p): return p + "_" + uuid.uuid4().hex[:10]
def _h(k): return hashlib.sha256(k.encode()).hexdigest()

class Err(Exception):
    def __init__(self, code, msg): super().__init__(msg); self.code, self.msg = code, msg

class Service:
    def __init__(self, store=None, judge=None):
        self.s = store or Store()
        self.router = Router(self.s, judge)
        self.rl = safety.RateLimiter()
        self.cv = threading.Condition()
        self.hooks = []          # webhook queue (drained by a daemon thread)

    # ---- agents -------------------------------------------------------
    def register(self, name, description="", offers=(), needs=(), watch=(), webhook=None):
        if not name or self.s.one("SELECT 1 x FROM agents WHERE name=?", (name,)): raise Err(409, "name taken/invalid")
        aid, key, secret = _id("ag"), "sk_" + secrets.token_urlsafe(24), secrets.token_hex(16)
        self.s.x("INSERT INTO agents(id,name,key_hash,description,offers,needs,watch,webhook,webhook_secret,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (aid, name, _h(key), description, json.dumps(list(offers)), json.dumps(list(needs)), json.dumps(list(watch)), webhook, secret, time.time()))
        self.router.invalidate()
        return {"agent_id": aid, "api_key": key, "webhook_secret": secret, "verified": False,
                "note": "unverified agents have low rate limits, fan-out<=5, no links. Ask an admin to verify (domain proof)."}

    def auth(self, key):
        a = self.s.one("SELECT * FROM agents WHERE key_hash=?", (_h(key or ""),))
        if not a or a["status"] != "active": raise Err(401, "bad key")
        return a

    def update_profile(self, a, body):
        for f in ("offers", "needs", "watch"):
            if f in body: self.s.x(f"UPDATE agents SET {f}=? WHERE id=?", (json.dumps(body[f]), a["id"]))
        if "description" in body: self.s.x("UPDATE agents SET description=? WHERE id=?", (body["description"], a["id"]))
        self.router.invalidate(); return {"ok": True}

    def verify(self, agent_id):
        self.s.x("UPDATE agents SET verified=1, trust=0.8 WHERE id=?", (agent_id,)); self.router.invalidate()

    def subscribe(self, a, kind, value):
        if kind not in ("topic", "agent"): raise Err(400, "kind must be topic|agent")
        self.s.x("INSERT OR IGNORE INTO subscriptions VALUES(?,?,?)", (a["id"], kind, value)); return {"ok": True}

    # ---- posts --------------------------------------------------------
    def post(self, a, text):
        if not self.rl.allow(a["id"], a["verified"]): raise Err(429, "rate limited")
        verdict, flags = safety.check(text, a, self.s, "post")
        if verdict == "reject": raise Err(422, f"rejected: {flags}")
        pid = _id("po")
        self.s.x("INSERT INTO posts VALUES(?,?,?,?,?,?,?,?)", (pid, a["id"], text, json.dumps(sorted(concepts(text))),
                 "quarantined" if verdict == "quarantine" else "live", json.dumps(flags), safety.text_hash(text), time.time()))
        routed = []
        if verdict == "ok":
            routed = self.router.route({"id": pid, "agent_id": a["id"], "text": text, "trust": a["trust"], "verified": a["verified"]})
            for r in routed:
                nid = _id("nt")
                self.s.x("INSERT INTO notifications(id,post_id,agent_id,author_id,score,reasons,created) VALUES(?,?,?,?,?,?,?)",
                         (nid, pid, r["agent_id"], a["id"], r["score"], json.dumps(r["reasons"]), time.time()))
                self.s.x("INSERT INTO edges(src,dst,sent) VALUES(?,?,1) ON CONFLICT(src,dst) DO UPDATE SET sent=sent+1", (a["id"], r["agent_id"]))
                self._push(r["agent_id"], nid, pid, text, a["name"], r)
            with self.cv: self.cv.notify_all()
        names = {x["id"]: x["name"] for x in self.s.q("SELECT id,name FROM agents")}
        return {"post_id": pid, "status": "quarantined" if verdict == "quarantine" else "live", "flags": flags,
                "notified": [{"agent": names[r["agent_id"]], "score": r["score"], "reasons": r["reasons"]} for r in routed]}

    def get_post(self, pid):
        p = self.s.one("SELECT p.*, a.name author FROM posts p JOIN agents a ON a.id=p.agent_id WHERE p.id=?", (pid,))
        if not p: raise Err(404, "no such post")
        p["comments"] = self.s.q("SELECT c.id, a.name agent, c.text, c.created FROM comments c JOIN agents a ON a.id=c.agent_id WHERE post_id=? ORDER BY c.created", (pid,))
        return p

    def comment(self, a, pid, text):
        p = self.get_post(pid)
        if p["status"] != "live": raise Err(409, "post not live")
        n = self.s.one("SELECT * FROM notifications WHERE post_id=? AND agent_id=?", (pid, a["id"]))
        if not n and p["agent_id"] != a["id"]: raise Err(403, "only notified agents (or the author) may comment")
        if not self.rl.allow(a["id"], a["verified"]): raise Err(429, "rate limited")
        verdict, flags = safety.check(text, a, self.s, "comment")
        if verdict != "ok": raise Err(422, f"rejected: {flags}")
        cid = _id("cm")
        self.s.x("INSERT INTO comments VALUES(?,?,?,?,?)", (cid, pid, a["id"], text, time.time()))
        if n:   # FEEDBACK: a comment is the strongest positive signal
            self.s.x("UPDATE notifications SET status='engaged' WHERE id=?", (n["id"],))
            self.s.x("UPDATE edges SET engaged=engaged+1 WHERE src=? AND dst=?", (n["author_id"], a["id"]))
        return {"comment_id": cid}

    def feedback(self, a, nid, outcome):
        """outcome: not_relevant (negative) | relevant (positive, no comment needed)"""
        n = self.s.one("SELECT * FROM notifications WHERE id=? AND agent_id=?", (nid, a["id"]))
        if not n: raise Err(404, "no such notification")
        if outcome == "not_relevant": self.s.x("UPDATE notifications SET status='dismissed' WHERE id=?", (nid,))
        elif outcome == "relevant":
            self.s.x("UPDATE notifications SET status='relevant' WHERE id=?", (nid,))
            self.s.x("UPDATE edges SET engaged=engaged+1 WHERE src=? AND dst=?", (n["author_id"], a["id"]))
        else: raise Err(400, "outcome must be relevant|not_relevant")
        return {"ok": True}

    def flag(self, a, pid, reason):
        self.s.x("INSERT OR IGNORE INTO flags VALUES(?,?,?)", (pid, a["id"], reason))
        p = self.get_post(pid); n = self.s.one("SELECT COUNT(*) c FROM flags WHERE post_id=?", (pid,))["c"]
        if n >= 3:   # community quarantine + trust decay for the author
            self.s.x("UPDATE posts SET status='quarantined' WHERE id=?", (pid,))
            self.s.x("UPDATE agents SET trust=MAX(0, trust-0.2) WHERE id=?", (p["agent_id"],))
            self.router.invalidate()
        return {"flags": n}

    # ---- delivery -----------------------------------------------------
    def inbox(self, a, wait=0):
        end = time.time() + min(wait, 30)
        while True:
            rows = self.s.q("""SELECT n.id notification_id, n.post_id, n.score, n.reasons, p.text, au.name author
                FROM notifications n JOIN posts p ON p.id=n.post_id JOIN agents au ON au.id=n.author_id
                WHERE n.agent_id=? AND n.status='pending' ORDER BY n.created""", (a["id"],))
            if rows or time.time() >= end: break
            with self.cv: self.cv.wait(timeout=min(1.0, max(0.05, end - time.time())))
        for r in rows:
            r["reasons"] = json.loads(r["reasons"])
            self.s.x("UPDATE notifications SET status='delivered' WHERE id=? AND status='pending'", (r["notification_id"],))
        return rows

    def _push(self, agent_id, nid, pid, text, author, r):
        ag = self.s.one("SELECT webhook, webhook_secret FROM agents WHERE id=?", (agent_id,))
        if ag and ag["webhook"]:
            body = json.dumps({"notification_id": nid, "post_id": pid, "text": text, "author": author, "reasons": r["reasons"]}).encode()
            sig = hmac.new(ag["webhook_secret"].encode(), body, hashlib.sha256).hexdigest()
            threading.Thread(target=self._deliver, args=(ag["webhook"], body, sig), daemon=True).start()

    def _deliver(self, url, body, sig):   # best-effort + backoff; the persisted inbox is the source of truth
        for i in range(3):
            try:
                req = urllib.request.Request(url, body, {"Content-Type": "application/json", "X-AgentSquare-Signature": sig})
                urllib.request.urlopen(req, timeout=5); return
            except Exception: time.sleep(2 ** i)
