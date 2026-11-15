"""
Optional LOCAL model via Ollama (loopback only - hardware data never leaves the machine).
The model has two narrow jobs, both validated: (a) classify the question into an allow-listed
intent, (b) optionally re-phrase a deterministic answer. No free-form tool/argv output is accepted.
"""
import json, urllib.request, urllib.parse
from .answers import INTENTS

class LocalLLM:
    def __init__(self, model, host="http://127.0.0.1:11434"):
        if urllib.parse.urlparse(host).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("refusing non-local LLM endpoint: hardware data must stay on this machine")
        self.model, self.host = model, host
    def _chat(self, system, user, as_json=False):
        body = {"model": self.model, "stream": False, "options": {"temperature": 0}, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if as_json: body["format"] = "json"
        r = urllib.request.urlopen(urllib.request.Request(self.host + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=60)
        return json.load(r)["message"]["content"]
    def classify(self, question, history):
        sys = ("Map the user's hardware question to ONE intent. Allowed intents: " + ", ".join(INTENTS) +
               ", unknown. Use memory_upgrade when asking about adding/maximum RAM or free slots. "
               "If the question is a follow-up, use the previous intents. Reply JSON: {\"intent\": \"...\"}")
        try: return json.loads(self._chat(sys, f"previous intents: {history}\nquestion: {question}", True)).get("intent")
        except Exception: return None
    def rephrase(self, question, draft):
        return self._chat("Rewrite the answer in friendly plain language. Keep EVERY number and fact exactly as given; add no new facts.", f"Question: {question}\nAnswer: {draft}")
