import argparse, os, subprocess, sys
from .engine import Engine
from .probes import Runner, PROBES
from .llm import LocalLLM

def main(argv=None):
    ap = argparse.ArgumentParser(prog="hwsage", description="Ask questions about this Linux machine's hardware (read-only, local).")
    ap.add_argument("--sudo", action="store_true", help="authorise sudo once; only allow-listed dmidecode probes use it")
    ap.add_argument("--dmi-file", help="use a saved `sudo dmidecode` snapshot instead of root")
    ap.add_argument("--model", help="local Ollama model for intent + phrasing (optional), e.g. qwen2.5:7b")
    ap.add_argument("--fixtures", help="replay a recorded machine (testing/demos)")
    ap.add_argument("--trace", action="store_true", help="show which probes ran and evidence ids")
    ap.add_argument("-q", "--question", help="single question, non-interactive")
    ns = ap.parse_args(argv)
    if ns.sudo and os.geteuid() != 0 and not ns.fixtures:
        if subprocess.call(["sudo", "-v"]) != 0: print("sudo not granted; continuing without root.")
    eng = Engine(Runner(ns.sudo, ns.dmi_file, ns.fixtures), LocalLLM(ns.model) if ns.model else None)
    def show(q):
        r = eng.ask(q); print("\n" + r["answer"])
        if ns.trace: print(f"  [intents={r['intents']} probes={r['trace']} evidence={r['evidence']}]")
    if ns.question: return show(ns.question)
    print("HardwareSage - ask about RAM, CPU, disks, GPU, battery, model. Commands: :tools  :facts  :quit")
    while True:
        try: q = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt): break
        if q in (":quit", ":q"): break
        if q == ":tools": print("\n".join(f"  {p.name:10} {p.desc}" + ("  [needs root]" if p.needs_root else "") for p in PROBES.values())); continue
        if q: show(q)
