"""
LLM layer for the *agent side* (the platform itself needs no LLM to route).
  OfflineLLM        deterministic playbook -> works with no network; used by demo/tests.
  OpenAICompatLLM   any /v1/chat/completions endpoint (OpenAI, vLLM, Ollama, Claude gateways...).
Contract: decide_and_draft(profile, post_text, reasons) -> comment text, or None to stay silent.
Staying silent is a first-class outcome: it becomes 'not_relevant' feedback for the router.
"""
import json, os, urllib.request

class OfflineLLM:
    def __init__(self, playbook): self.playbook = playbook      # [{"when": [kw...], "reply": "..."}]
    def decide_and_draft(self, profile, post, reasons):
        t = post.lower()
        for rule in self.playbook:
            if any(k in t for k in rule["when"]): return rule["reply"]
        return None

class OpenAICompatLLM:
    def __init__(self, url=None, key=None, model=None):
        self.url = url or os.environ.get("LLM_URL", "http://127.0.0.1:11434/v1/chat/completions")
        self.key, self.model = key or os.environ.get("LLM_KEY", ""), model or os.environ.get("LLM_MODEL", "llama3.1")
    def decide_and_draft(self, profile, post, reasons):
        sys = (f"You are the public-facing agent for {profile['name']}. Interests: {profile['needs']} {profile['watch']} {profile['offers']}. "
               "Reply with ONE short comment (<=200 chars) that adds concrete, accurate value to the post for your own business. "
               "Never invent numbers you were not given. If you have nothing useful to add, reply exactly: SKIP")
        body = {"model": self.model, "temperature": 0.2, "messages": [{"role": "system", "content": sys},
                {"role": "user", "content": f"Post: {post}\nWhy you were notified: {reasons}"}]}
        req = urllib.request.Request(self.url, json.dumps(body).encode(), {"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        out = json.load(urllib.request.urlopen(req, timeout=30))["choices"][0]["message"]["content"].strip()
        return None if out.upper().startswith("SKIP") else out
