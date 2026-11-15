"""
Anti-hallucination guard for any LLM-phrased text: every number in the reply must be traceable
to the fact ledger (or be in the user's own question). Otherwise the reply is discarded and the
deterministic composer's text is used instead. The LLM can improve wording, never facts.
"""
import re

def numbers_in(text): return set(re.findall(r"\d+(?:\.\d+)?", text))

def ungrounded(text, ledger, question=""):
    allowed = ledger.numbers() | numbers_in(question)
    return sorted(n for n in numbers_in(text) if n not in allowed and n.rstrip("0").rstrip(".") not in allowed)
