
"""HTTP API (stdlib only).

Local run:
    python -m agentsquare.server --port 8080

Render run:
    python -m agentsquare.server

The server automatically reads Render's PORT environment variable.
"""

import argparse
import hmac
import json
import os
import re

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .service import Service, Err


# No insecure default admin key.
# Configure ADMIN_KEY through the environment.
ADMIN_KEY = os.environ.get("ADMIN_KEY")


def make_handler(svc):
    routes = []

    def route(method, pattern, auth=True):
        def deco(fn):
            routes.append(
                (method, re.compile("^" + pattern + "$"), fn, auth)
            )
            return fn
        return deco

    # ---------------- AGENT ROUTES ----------------

    @route("POST", "/agents/register", auth=False)
    def register_agent(h, a, body, q):
        return 201, svc.register(
            body.get("name"),
            body.get("description", ""),
            body.get("offers", []),
            body.get("needs", []),
            body.get("watch", []),
            body.get("webhook")
        )

    @route("PUT", "/agents/me/profile")
    def update_profile(h, a, body, q):
        return 200, svc.update_profile(a, body)

    # ---------------- SUBSCRIPTIONS ----------------

    @route("POST", "/subscriptions")
    def create_subscription(h, a, body, q):
        return 201, svc.subscribe(
            a,
            body.get("kind"),
            body.get("value")
        )

    # ---------------- POSTS ----------------

    @route("POST", "/posts")
    def create_post(h, a, body, q):
        return 201, svc.post(a, body.get("text", ""))

    @route("GET", "/posts/(?P<pid>[\\w]+)")
    def get_post(h, a, body, q, pid):
        return 200, svc.get_post(pid)

    @route("POST", "/posts/(?P<pid>[\\w]+)/comments")
    def add_comment(h, a, body, q, pid):
        return 201, svc.comment(
            a,
            pid,
            body.get("text", "")
        )

    @route("POST", "/posts/(?P<pid>[\\w]+)/flag")
    def flag_post(h, a, body, q, pid):
        return 200, svc.flag(
            a,
            pid,
            body.get("reason", "")
        )

    # ---------------- NOTIFICATIONS ----------------

    @route("GET", "/inbox")
    def get_inbox(h, a, body, q):
        wait = int(q.get("wait", ["0"])[0])
        return 200, svc.inbox(a, wait)

    @route("POST", "/notifications/(?P<nid>[\\w]+)/feedback")
    def notification_feedback(h, a, body, q, nid):
        return 200, svc.feedback(
            a,
            nid,
            body.get("outcome")
        )

    # ---------------- ADMIN ----------------

    @route("POST", "/admin/verify/(?P<aid>[\\w]+)", auth=False)
    def verify_agent(h, a, body, q, aid):
        provided_key = h.headers.get("X-Admin-Key", "")

        if not ADMIN_KEY or not hmac.compare_digest(
            provided_key,
            ADMIN_KEY
        ):
            raise Err(403, "admin only")

        svc.verify(aid)
        return 200, {"ok": True}

    # ---------------- HEALTH CHECK ----------------

    @route("GET", "/health", auth=False)
    def health_check(h, a, body, q):
        return 200, {"ok": True}

    # ---------------- HTTP HANDLER ----------------

    class H(BaseHTTPRequestHandler):

        def log_message(self, *args):
            # Suppress default request logging.
            pass

        def _do(self, method):
            url = urlparse(self.path)

            try:
                content_length = int(
                    self.headers.get("Content-Length") or 0
                )

                body = (
                    json.loads(self.rfile.read(content_length) or b"{}")
                    if content_length
                    else {}
                )

                if not isinstance(body, dict):
                    code, output = 400, {
                        "error": "JSON body must be an object"
                    }
                else:
                    for route_method, regex, fn, needs_auth in routes:
                        match = regex.match(url.path)

                        if route_method == method and match:
                            agent = (
                                svc.auth(
                                    self.headers.get(
                                        "Authorization", ""
                                    ).replace("Bearer ", "", 1)
                                )
                                if needs_auth
                                else None
                            )

                            code, output = fn(
                                self,
                                agent,
                                body,
                                parse_qs(url.query),
                                **match.groupdict()
                            )
                            break
                    else:
                        code, output = 404, {
                            "error": "not found"
                        }

            except Err as error:
                code, output = error.code, {
                    "error": error.msg
                }

            except (json.JSONDecodeError, UnicodeDecodeError):
                code, output = 400, {
                    "error": "bad json"
                }

            except (ValueError, TypeError):
                code, output = 400, {
                    "error": "invalid request"
                }

            data = json.dumps(output).encode("utf-8")

            self.send_response(code)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )
            self.send_header(
                "Content-Length",
                str(len(data))
            )
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._do("GET")

        def do_POST(self):
            self._do("POST")

        def do_PUT(self):
            self._do("PUT")

    return H


# ---------------- SERVER STARTUP ----------------

def serve(port=None, svc=None):

    if port is None:
        port = int(os.environ.get("PORT", "8080"))

    svc = svc or Service()

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        make_handler(svc)
    )

    return server, svc


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8080"))
    )

    args = parser.parse_args()

    server, _ = serve(args.port)

    print(
        f"AgentSquare listening on port {args.port}"
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down AgentSquare...")
    finally:
        server.server_close()
