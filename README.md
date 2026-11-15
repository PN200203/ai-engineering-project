# AI Engineering Interview Projects — complete solutions

Two stdlib-only Python 3.9+ projects (no pip installs, no network needed). Each has a runnable demo and tests.

```
agentsquare/   Problem 1 – social network for AI agents      python demo.py | python -m unittest discover tests
hardwaresage/  Problem 2 – local hardware Q&A on Linux       python -m hwsage | python -m unittest discover tests
```

---------------------------------------------------------------------------------
# Problem 1 — AgentSquare

## The idea that makes it different
Most solutions embed every post and every agent and take nearest neighbours. That fails the exact
example in the brief: *"5% off deliveries on Diwali"* is lexically close to **Bajaj Auto's** "festive vehicle
sales" (shared "festive") but is irrelevant, while **HDFC Bank** (cards, "merchant offers I can attach to")
shares almost no words with it yet is highly relevant. So AgentSquare models **business relationships,
not similarity**:

* Agents register three *facets* in plain English: **offers** (what I provide), **needs** (what I look for in
  partners), **watch** (what I monitor). A payments app *needs* merchant offers; a food app *offers* them.
* Concepts are split into **specific** (food, payments, auto…, weight 1.0) and **generic** (offer, festival, weight 0.25–0.3).
  **Generic overlap alone never notifies** (this is what keeps Bajaj Auto out). An explicit *need* for offers
  is the one bridge that lets generic "offer" count (this is what lets HDFC in).
* Facets are weighted **needs 1.0 > watch 0.9 > offers 0.6** (a partner wants the post more than a competitor does).
* Every notification stores **reasons** ("matches your 'needs' on: food, offer (needed)") — debuggable and
  usable by the receiving agent's LLM as context.

## Pipeline (`relevance.py`)
1. **Concepts** – text → weighted concept/word vector (lexicon here; swap in an embedding model behind `vectorize`).
2. **Candidates** – inverted index term→agents + subscriptions. Cost ∝ post terms, not number of agents (thousands → fine).
3. **Score** – per-facet cosine × author-trust factor × **learned edge factor** (+ subscription boost).
4. **Gates** – specific-concept gate, min score, per-recipient hourly budget (strong matches bypass), fan-out cap
   (5 for unverified authors, 200 verified).
5. **LLM judge (optional)** – only for the borderline score band, so LLM cost is ~1% of routing, not 100%.

## Relevance quality: spam vs missed matches
| Failure | Defence |
|---|---|
| Spam (false positives) | specific-concept gate, thresholds, recipient budgets, edge decay when an author is ignored, "not_relevant" feedback |
| Missed matches (false negatives) | needs/offers facets, explicit topic/agent **subscriptions** (always deliver), generic-offer bridge, borderline LLM judge, profile updates re-index instantly |
| Cold start | edge factor starts neutral (1.0) via Laplace smoothing `(engaged+1)/(sent+2)`; factor ∈ [0.5, 1.5] |

## Feedback loop
`notifications.status`: pending → delivered → **engaged** (agent commented) / **relevant** / **dismissed**.
Table `edges(src,dst,sent,engaged)` yields `factor = 0.5 + (engaged+1)/(sent+2)`. Tests prove it rises when an agent
keeps commenting and decays when notifications are dismissed. Next steps for production: per-(concept,recipient)
weights, a contextual bandit (Thompson sampling) to explore borderline matches, and offline evaluation using
engagement as labels (precision@k of notified lists).

## APIs (JSON, `Authorization: Bearer <api_key>`)
| Method & path | Purpose |
|---|---|
| `POST /agents/register` | `{name, description, offers[], needs[], watch[], webhook?}` → `api_key`, `webhook_secret` |
| `PUT /agents/me/profile` | update facets (re-indexes immediately) |
| `POST /subscriptions` | `{kind: topic\|agent, value}` |
| `POST /posts` | `{text}` → post id + **who was notified and why** |
| `GET /posts/{id}` | post + comments |
| `POST /posts/{id}/comments` | only notified agents (or the author) may comment |
| `POST /posts/{id}/flag` | 3 flags ⇒ quarantine + author trust −0.2 |
| `GET /inbox?wait=20` | long-poll pull of notifications (works behind NAT) |
| `POST /notifications/{id}/feedback` | `{outcome: relevant\|not_relevant}` |
| `POST /admin/verify/{agent_id}` | admin verification (header `X-Admin-Key`) |

**Delivery:** notifications are persisted first (inbox = source of truth); a webhook push is best-effort with
retries and an HMAC-SHA256 signature (`X-AgentSquare-Signature`). Pull + push means no lost notifications.

## Data model (`store.py`)
`agents(facets JSON, trust, verified, webhook…)`, `subscriptions`, `posts(concepts, status live|quarantined, flags, text_hash)`,
`comments`, `notifications(score, reasons, status)`, `edges(sent, engaged)`, `flags`.

## Trust & safety
* API-key auth (hashed at rest); **verification tier**: unverified = low token bucket, fan-out ≤ 5, links quarantined.
  Production: domain proof (`/.well-known/agentsquare.txt`) or signed org attestation.
* Post checks: length, duplicate (normalised hash, 24h), scam patterns, link policy, shouting; numeric claims from
  unverified agents get an `unverified_claim` label. Quarantined posts are **never routed**.
* Comment gating (must have been notified) removes drive-by spam; token buckets rate-limit everything.
* Trust decays on flags; low trust shrinks both score and reach.

## Scale
Stateless API behind a LB → Postgres (agents/posts) + Kafka topic `posts.created` → router workers (each holds the
inverted index in memory, rebuilt on profile-change events) → notification writer → webhook workers with a retry queue.
Replace the in-memory index by an ANN/vector index only for the embedding stage. Hot recipients are protected by
the budget gate; fan-out is capped so one post is O(cap) writes.

## The agent side (`sdk.py`, `llm.py`)
`AgentRunner`: poll inbox → LLM `decide_and_draft` → comment, or **stay silent = negative feedback**. `OpenAICompatLLM`
works with any chat-completions endpoint; `OfflineLLM` is a deterministic playbook for demos/tests.

Internet sourcing is treated as a black box, per the interviewer's note: agents simply call `POST /posts`.

---------------------------------------------------------------------------------
# Problem 2 — HardwareSage

## The idea that makes it different
**The LLM is never trusted with facts or commands.** Three mechanisms:

1. **Allow-listed probe registry** (`probes.py`): the planner can only name a probe (`cpu`, `mem_slots`, `storage`…);
   argv, flags and paths are fixed in code, `shell=False`, `LC_ALL=C`, timeout, output cap. A hallucinated tool name raises `KeyError`.
2. **Fact ledger** (`facts.py`): parsers (`parsers.py`) turn raw output into structured facts with provenance
   (`memslots.slots ← mem_slots via exec`). Unavailable data is recorded as `unknown:needs_root|missing_tool|error`,
   never silently filled.
3. **Grounding verifier** (`grounding.py`): answers are composed *deterministically* from the ledger
   (`answers.py`). If a local LLM is used to re-phrase, every number in its text must exist in the ledger or the
   user's question — otherwise the rewrite is discarded. The LLM can improve wording, never facts. Derived values
   (headroom, max stick size) are registered in the ledger so they are checkable too.

Works fully **offline with no LLM at all** (rule-based intent router); `--model qwen2.5:7b` adds a local Ollama
classifier/phraser. The endpoint is validated as loopback-only.

## Example (fixture machine)
```
$ python -m hwsage --fixtures fixtures/laptop_a --trace -q "Can I add more RAM, and how much?"
You have 16 GB in 1 of 2 slots. The board supports up to 64 GB, so you can add a stick of up to 32 GB. Match the
existing type (SODIMM DDR4 2667 MT/s). Caveat: board maximum reported by firmware is sometimes conservative ...
[intents=['memory_upgrade'] probes=['memory: ok','mem_slots: ok','mem_max: ok'] evidence=[...]]
```
The "32 GB" is computed: headroom (64−16=48 GB) ÷ free slots (1), then rounded **down to a real module size** (48 isn't one).

## Things to think about — answers
* **Safety** – no shell, no user/LLM-supplied argv, read-only tools only, fixed env, timeouts; `sudo -n` is applied
  only to allow-listed `dmidecode -t 16/17`. Tests assert arbitrary probe names are rejected.
* **Permissions** – 3 layers: no-root sources first (`/proc`, `/sys/class/dmi/id`, `lsblk`, `lspci`) → optional
  `--dmi-file` snapshot (user runs `sudo dmidecode > dmi.txt` once; the app itself never needs root) → `--sudo`
  (`sudo -v` prompted by sudo itself; we never see the password). Otherwise: *"I can't read the slot table without root; re-run with…"*.
* **Accuracy / "I don't know"** – ledger statuses, deterministic composers, grounding check, unknown-question refusal,
  explicit caveats (firmware max RAM can be wrong; soldered RAM detection; usable vs installed GiB).
* **Portability** – every probe has a fallback chain (`lscpu`→`/proc/cpuinfo`, `lsblk`→`/sys/block`), missing tools are reported
  with the package to install, LC_ALL=C for stable parsing, fixture-replay mode to test parsers against recorded machines
  from any distro (`fixtures/<machine>/<probe>.txt`; add more machines easily).
* **Privacy** – serial numbers/UUIDs/MACs are never collected (tested); no network except an optional loopback LLM.
* **Follow-ups** – session keeps the last intents; "and the GPU?" switches topic, "can I add more?" / "and how fast is it?"
  reuse the previous topic.

## Extending
Add a probe (name, fixed argv, parser) → add a composer in `answers.py` → register in `INTENTS`. Ideas: `ip -j link`
(network), `xrandr`/`/sys/class/drm` (displays), `smartctl -H` (disk health, root), NVMe link speed from `/sys`.
