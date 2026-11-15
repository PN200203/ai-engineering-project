import unittest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from agentsquare.service import Service, Err

def mk():
    s = Service(); out = {}
    def reg(n, **kw):
        r = s.register(n, **kw); s.verify(r["agent_id"]); out[n] = s.auth(r["api_key"]); return out[n]
    reg("Swiggy", offers=["food delivery"], needs=["payment partners"])
    reg("PhonePe", offers=["UPI payments"], needs=["merchant offers cashback food delivery"])
    reg("Bajaj", offers=["motorcycles EV"], watch=["festive vehicle sales"])
    return s, out

class T(unittest.TestCase):
    def test_routing_diwali(self):
        s, a = mk(); r = s.post(a["Swiggy"], "5% off all deliveries on Diwali.")
        names = [n["agent"] for n in r["notified"]]
        self.assertIn("PhonePe", names); self.assertNotIn("Bajaj", names)   # generic 'festive' overlap must not notify
    def test_comment_requires_notification(self):
        s, a = mk(); r = s.post(a["Swiggy"], "5% off all deliveries on Diwali.")
        with self.assertRaises(Err) as e: s.comment(a["Bajaj"], r["post_id"], "hi")
        self.assertEqual(e.exception.code, 403)
        s.comment(a["PhonePe"], r["post_id"], "Extra cashback!")
    def test_feedback_moves_edge(self):
        s, a = mk(); f0 = s.router._edge_factor(a["Swiggy"]["id"], a["PhonePe"]["id"])
        for i in range(5):
            r = s.post(a["Swiggy"], f"{i+1}% off all deliveries today, order food now")
            s.comment(a["PhonePe"], r["post_id"], f"c{i}")
        self.assertGreater(s.router._edge_factor(a["Swiggy"]["id"], a["PhonePe"]["id"]), f0)
    def test_ignored_edge_decays(self):
        s, a = mk(); f0 = s.router._edge_factor(a["Swiggy"]["id"], a["PhonePe"]["id"])
        for i in range(6):
            r = s.post(a["Swiggy"], f"{i+1}% off food deliveries today")
            for n in s.inbox(a["PhonePe"]): s.feedback(a["PhonePe"], n["notification_id"], "not_relevant")
        self.assertLess(s.router._edge_factor(a["Swiggy"]["id"], a["PhonePe"]["id"]), f0)
    def test_safety(self):
        s, a = mk(); u = s.auth(s.register("Shady", watch=["payments"])["api_key"])
        r = s.post(u, "Guaranteed returns! click here https://x.co")
        self.assertEqual(r["status"], "quarantined"); self.assertEqual(r["notified"], [])
        with self.assertRaises(Err): s.post(a["Swiggy"], "x" * 700)
    def test_duplicate_rejected(self):
        s, a = mk(); s.post(a["Swiggy"], "5% off food delivery")
        with self.assertRaises(Err): s.post(a["Swiggy"], "5% OFF food delivery!")
    def test_unverified_fanout_cap(self):
        s = Service()
        for i in range(10): s.register(f"p{i}", watch=["payments upi cashback"])
        u = s.auth(s.register("src", offers=["upi payments"])["api_key"])
        self.assertLessEqual(len(s.post(u, "UPI payments cashback offer")["notified"]), 5)
if __name__ == "__main__": unittest.main()
