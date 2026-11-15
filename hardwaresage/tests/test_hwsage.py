import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(__file__)); sys.path.insert(0, ROOT)
from hwsage.engine import Engine
from hwsage.probes import Runner
from hwsage.facts import Ledger
from hwsage.grounding import ungrounded
FX = os.path.join(ROOT, "fixtures", "laptop_a")

def eng(fx=FX): return Engine(Runner(fixtures=fx))
class T(unittest.TestCase):
    def test_ram_upgrade_matches_spec_example(self):
        a = eng().ask("Can I add more RAM, and how much?")["answer"]
        self.assertIn("16 GB in 1 of 2 slots", a); self.assertIn("up to 64 GB", a); self.assertIn("up to 32 GB", a)
    def test_nvme(self): self.assertTrue(eng().ask("Is my SSD NVMe?")["answer"].startswith("Yes - 1 NVMe"))
    def test_followup_gpu_and_pronoun(self):
        e = eng(); e.ask("How much RAM do I have?")
        self.assertIn("GeForce MX150", e.ask("and what about the GPU?")["answer"])
        e.ask("how much ram"); self.assertIn("add", e.ask("can I add more?")["answer"])      # follow-up keeps topic
    def test_needs_root_says_so(self):
        import tempfile, shutil
        d = tempfile.mkdtemp(); [shutil.copy(os.path.join(FX, f), d) for f in os.listdir(FX)]
        os.remove(os.path.join(d, "mem_slots.txt")); open(os.path.join(d, "mem_slots.NEEDS_ROOT"), "w").close()
        a = eng(d).ask("Can I add more RAM?")["answer"]
        self.assertIn("can't", a); self.assertIn("root", a); self.assertNotIn("64", a)         # must not invent a maximum
    def test_missing_tool_reported(self):
        import tempfile, shutil
        d = tempfile.mkdtemp(); [shutil.copy(os.path.join(FX, f), d) for f in os.listdir(FX) if f != "gpu.txt"]
        self.assertIn("pciutils", eng(d).ask("what GPU do I have")["answer"])
    def test_unknown_question_refuses_to_guess(self):
        self.assertIn("won't guess", eng().ask("what's the weather in Paris")["answer"])
    def test_allowlist_blocks_arbitrary_probe(self):
        with self.assertRaises(KeyError): Runner().run("rm -rf /")
        with self.assertRaises(KeyError): Runner().run("dmidecode -t 1")
    def test_grounding_rejects_invented_numbers(self):
        from hwsage.answers import memory_upgrade
        e = eng(); L = e.ingest(["memory", "mem_slots", "mem_max"]); memory_upgrade(L)   # composers register derived facts
        self.assertEqual(ungrounded("You have 16 GB in 1 of 2 slots, max 64 GB", L), [])
        self.assertEqual(ungrounded("You have 24 GB and a 128 GB max", L), ["128", "24"])
    def test_serials_never_collected(self):
        r = eng().r.run("mem_slots"); self.assertNotIn("DEADBEEF", str(r.data))
    def test_local_only_llm(self):
        from hwsage.llm import LocalLLM
        with self.assertRaises(ValueError): LocalLLM("m", host="https://api.example.com")
if __name__ == "__main__": unittest.main()
