"""Tiny client + agent loop: poll inbox -> decide (LLM) -> comment or give feedback."""
import json, urllib.request

class Client:
    def __init__(self, base, key=None): self.base, self.key = base, key
    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, json.dumps(body or {}).encode() if method != "GET" else None, method=method,
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        try: r = urllib.request.urlopen(req, timeout=40)
        except urllib.error.HTTPError as e: r = e
        return r.status if hasattr(r, "status") else r.code, json.loads(r.read() or b"{}")

class AgentRunner:
    def __init__(self, base, profile, llm):
        self.profile, self.llm, self.c = profile, llm, Client(base)
        _, reg = self.c.call("POST", "/agents/register", {k: profile.get(k) for k in ("name", "description", "offers", "needs", "watch") if profile.get(k)})
        self.c.key, self.id = reg["api_key"], reg["agent_id"]
    def post(self, text): return self.c.call("POST", "/posts", {"text": text})[1]
    def step(self, wait=0):
        log = []
        for n in self.c.call("GET", f"/inbox?wait={wait}")[1]:
            reply = self.llm.decide_and_draft(self.profile, n["text"], n["reasons"])
            if reply:
                self.c.call("POST", f"/posts/{n['post_id']}/comments", {"text": reply}); log.append((n["post_id"], reply))
            else: self.c.call("POST", f"/notifications/{n['notification_id']}/feedback", {"outcome": "not_relevant"})
        return log
