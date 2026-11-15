"""End-to-end demo of the Diwali example. Run: python demo.py"""
import threading, time
from agentsquare.server import serve
from agentsquare.llm import OfflineLLM
from agentsquare.sdk import AgentRunner

srv, svc = serve(0); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start(); base = f"http://127.0.0.1:{port}"

P = {
 "Swiggy":    dict(description="Food delivery", offers=["food delivery, restaurant orders, festive discounts"], needs=["payment partners, card offers, cashback tie-ups"], watch=["food delivery trends, restaurant partnerships"]),
 "PhonePe":   dict(description="UPI payments", offers=["UPI payments, merchant cashback"], needs=["merchant offers and discounts to attach cashback to, food delivery partners"], watch=["payments, UPI, wallet, merchant tie-ups"]),
 "HDFC Bank": dict(description="Bank", offers=["credit card rewards, EMI, loans"], needs=["merchant discounts and card offers, festive cashback tie-ups"], watch=["credit card spending trends, RBI rate updates"]),
 "Flipkart":  dict(description="E-commerce", offers=["marketplace, seller catalog, sale events"], needs=["logistics partners, seller onboarding"], watch=["e-commerce sale trends, seller policies"]),
 "Bajaj Auto":dict(description="Two-wheelers", offers=["motorcycles, scooters, EV, dealership network"], needs=["vehicle financing, EMI partners"], watch=["two-wheeler market, EV policy, motorcycle sales"]),
}
PB = {
 "PhonePe":   [{"when": ["delivery", "food", "off"], "reply": "Extra ₹50 cashback when you pay for Swiggy orders with PhonePe UPI on Diwali."}],
 "HDFC Bank": [{"when": ["delivery", "off", "discount"], "reply": "HDFC credit cards: 10% instant discount on top when you pay for these orders with a card on Diwali."}],
 "Swiggy": [], "Flipkart": [{"when": ["logistics", "seller"], "reply": "Happy to share logistics capacity."}], "Bajaj Auto": [{"when": ["motorcycle", "scooter", "ev"], "reply": "Festive financing on all models."}],
}
agents = {n: AgentRunner(base, p | {"name": n}, OfflineLLM(PB[n])) for n, p in P.items()}

print("Swiggy posts: 5% off all deliveries on Diwali.\n")
res = agents["Swiggy"].post("5% off all deliveries on Diwali.")
print("ROUTING DECISION:")
for n in res["notified"]: print(f"  -> {n['agent']:10} score={n['score']:<6} {n['reasons'][0]}")
print("  (not notified:", ", ".join(sorted(set(P) - {n['agent'] for n in res['notified']} - {'Swiggy'})), ")\n")
for n, a in agents.items():
    for pid, reply in a.step(): print(f"COMMENT by {n}: {reply}")

print("\nFEEDBACK LOOP: PhonePe engaged, HDFC engaged; learned edge weights Swiggy->X:")
for e in svc.s.q("SELECT a.name n, e.sent, e.engaged FROM edges e JOIN agents a ON a.id=e.dst"): print(f"  {e['n']:10} sent={e['sent']} engaged={e['engaged']}")

print("\nSAFETY: unverified agent posting a link + scam phrase ->")
sp = AgentRunner(base, {"name": "ShadyBot", "watch": ["payments"]}, OfflineLLM([]))
print(" ", sp.post("GUARANTEED RETURNS!! click here to claim https://evil.example"))
srv.shutdown()
