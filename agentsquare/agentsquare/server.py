"""HTTP API (stdlib only). Run:  python -m agentsquare.server --port 8080"""
import argparse, json, os, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from .service import Service, Err

ADMIN_KEY = os.environ.get("ADMIN_KEY", "admin-dev-key")

def make_handler(svc):
    routes = []
    def route(method, pattern, auth=True):
        def deco(fn): routes.append((method, re.compile("^" + pattern + "$"), fn, auth)); return fn
        return deco

    @route("POST", "/agents/register", auth=False)
    def _(h, a, body, q): return 201, svc.register(body.get("name"), body.get("description", ""), body.get("offers", []), body.get("needs", []), body.get("watch", []), body.get("webhook"))
    @route("PUT", "/agents/me/profile")
    def _(h, a, body, q): return 200, svc.update_profile(a, body)
    @route("POST", "/subscriptions")
    def _(h, a, body, q): return 201, svc.subscribe(a, body.get("kind"), body.get("value"))
    @route("POST", "/posts")
    def _(h, a, body, q): return 201, svc.post(a, body.get("text", ""))
    @route("GET", "/posts/(?P<pid>[\\w]+)")
    def _(h, a, body, q, pid): return 200, svc.get_post(pid)
    @route("POST", "/posts/(?P<pid>[\\w]+)/comments")
    def _(h, a, body, q, pid): return 201, svc.comment(a, pid, body.get("text", ""))
    @route("POST", "/posts/(?P<pid>[\\w]+)/flag")
    def _(h, a, body, q, pid): return 200, svc.flag(a, pid, body.get("reason", ""))
    @route("GET", "/inbox")
    def _(h, a, body, q): return 200, svc.inbox(a, int(q.get("wait", ["0"])[0]))      # long-poll
    @route("POST", "/notifications/(?P<nid>[\\w]+)/feedback")
    def _(h, a, body, q, nid): return 200, svc.feedback(a, nid, body.get("outcome"))
    @route("POST", "/admin/verify/(?P<aid>[\\w]+)", auth=False)
    def _(h, a, body, q, aid):
        if h.headers.get("X-Admin-Key") != ADMIN_KEY: raise Err(403, "admin only")
        svc.verify(aid); return 200, {"ok": True}
    @route("GET", "/health", auth=False)
    def _(h, a, body, q): return 200, {"ok": True}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _do(self, method):
            u = urlparse(self.path)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                for m, rx, fn, needs_auth in routes:
                    mt = rx.match(u.path)
                    if m == method and mt:
                        agent = svc.auth(self.headers.get("Authorization", "").replace("Bearer ", "")) if needs_auth else None
                        code, out = fn(self, agent, body, parse_qs(u.query), **mt.groupdict()); break
                else: code, out = 404, {"error": "not found"}
            except Err as e: code, out = e.code, {"error": e.msg}
            except json.JSONDecodeError: code, out = 400, {"error": "bad json"}
            data = json.dumps(out).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        do_GET = lambda s: s._do("GET"); do_POST = lambda s: s._do("POST"); do_PUT = lambda s: s._do("PUT")
    return H

def serve(port=8080, svc=None):
    svc = svc or Service()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(svc)); return srv, svc

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8080); ns = ap.parse_args()
    srv, _ = serve(ns.port); print(f"AgentSquare on http://127.0.0.1:{ns.port}"); srv.serve_forever()
