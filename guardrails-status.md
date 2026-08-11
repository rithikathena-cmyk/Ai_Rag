# Guardrails & Access Control — Status

**RAG Platform · Local Validation · 2026-08-11**

A working summary of the message-safety and data-access rules, tested live against the running backend — for handoff to the project lead.

---

## 30-second version

> We tested the guardrails and role-based access rules live against the real backend today, not just read the config. Every core rail held: injected instructions, destructive commands, and personal data all get caught before they matter. Each role only ever sees its own department's documents — verified by trying to break it, not just by trusting the settings. Along the way we found and fixed two real bugs: a request that could silently fail instead of answering, and a session bug that could let one user's message land inside another user's conversation. One open item needs a call from you: a document label called "restricted" isn't currently backed by any actual lock.

---

## Message-level guardrails

**What every chat message passes through.** Ten checks run in a fixed order on every request. The first one that trips ends the turn immediately — before any document search or model call happens.

| Step | Rail | Stops |
|---|---|---|
| 1 | Length | Empty or oversized messages |
| 2–3 | Injection / destructive intent | "Ignore your instructions," "drop table," "rm -rf"-style commands |
| 4 | Semantic risk | Reworded attempts that dodge the exact wording above |
| 5 | Scope `not configured` | Off-topic questions — currently a no-op; the model self-polices instead. See open item below. |
| 6 | Second-pass model check | Anything subtle enough to slip past 2–5 |
| 7 | PII in the question | SSNs, emails, phone numbers typed by the user |
| 8 | System-prompt leak | A reply that echoes the assistant's own instructions |
| 9 | PII in the answer | Redacts emails/phone numbers pulled from a document — in the reply text and in every source shown |
| 10 | Citation check | Flags (doesn't block) an answer with no source marker |

---

## Data-access policy

**Who can see what.** Access is scoped by department, enforced before a search ever runs — a role's blocked departments never reach the model, regardless of how a question is phrased.

| Role | Model tier | Can search | Can't do |
|---|---|---|---|
| Employee | Fastest only | Manufacturing docs only | Anything HR, engineering, executive, or admin |
| HR | Fast + standard | HR docs only | Manufacturing, engineering, project tools |
| Project Manager | Standard + advanced | Engineering docs only | HR admin, payroll, executive dashboards |
| CEO | Any | Every department | System configuration only |
| Admin | Any | Every department | Nothing withheld |

---

## Verified today

Live tests, not just a config read:

- ✅ **Verified** — Prompt-injection and "delete everything"-style requests were both blocked before reaching the model.
- ✅ **Verified** — A real HR record's emails and phone numbers came back redacted — in the answer text and in every underlying source shown to the user.
- ✅ **Verified** — An Employee-role account could not retrieve an HR document under any phrasing; an HR account could, with the same redaction applied.
- ✅ **Verified** — After the session fix below, one user's leftover conversation could no longer be continued by a different logged-in user — confirmed with a live attempt: the second user now gets a clean "not found."

---

## Fixed today

Two real bugs, found and closed:

### 🔧 Fixed — Silent non-answer under load

**What happened:** On the cheapest model tier, a legitimate question could exhaust its search budget chasing a document that wasn't in the corpus, and return "I wasn't able to finish" instead of an honest partial answer — even with the right information already in hand.

**Fix:** Raised the search budget and added an explicit instruction to stop searching and answer once results stop changing.

### 🔧 Fixed — Cross-user conversation continuity

**What happened:** Signing out didn't clear the chat screen. If a second person logged in on the same browser tab, their first message could silently attach to the previous person's conversation — carrying that person's prior messages into the new reply, and logging the new message under the old account.

**Fix:** The app now clears the screen on sign-out, and — the more important half — the server independently refuses to continue a conversation that doesn't belong to the logged-in user, confirmed live with a real cross-account attempt.

---

## Needs a decision: a "restricted" label that isn't backed by a lock

Some documents carry a classification tag — internal, confidential, restricted — set at upload time. Right now that tag is descriptive only: nothing in the system reads it to gate access. The only enforcement that actually exists is department-level, above. A document tagged "restricted" is exactly as visible as one tagged "internal," as long as they're in the same department.

**Option A — Leave as-is.** Department-level scoping may be sufficient for now, and the label stays purely informational for whoever uploads or reviews documents.

**Option B — Enforce it.** Hide "restricted" documents from a department's own members unless individually granted, not just gated at the department level.

---

## Needs a decision: off-topic requests aren't deterministically blocked

Rail 5 (Scope, in the message-level guardrails table above) exists in the pipeline but is currently a no-op — `GUARDRAIL_SCOPE_DENY_KEYWORDS` and `GUARDRAIL_SCOPE_ALLOW_KEYWORDS` are both unset in `.env`. Live-tested: asking the assistant to "write a poem about cats" passed all 10 rails cleanly and got a real poem back, with a self-redirect to document Q&A tacked on by the model itself — not by any enforced rule. That self-redirect is a model behavior, not deterministic enforcement, and isn't guaranteed on every phrasing or model tier.

**Option A — Leave as-is.** Rely on the model to self-police off-topic requests, same as today. No false-positive risk from a keyword filter, but no hard guarantee either.

**Option B — Deny-list.** Set `GUARDRAIL_SCOPE_DENY_KEYWORDS` to a comma-separated list of known-off-topic terms (poems, jokes, recipes, etc.). Lower risk of blocking legitimate document questions, but not exhaustive — anything not on the list still reaches the model.

**Option C — Allow-list.** Set `GUARDRAIL_SCOPE_ALLOW_KEYWORDS` so only messages matching an approved topic/keyword list pass. Strictest option; risks false-blocking legitimate questions that don't happen to contain a listed keyword.

Both are simple substring/keyword matches (`backend/app/services/guardrails/scope.py`), not semantic — same tradeoff class as rail 2/3's regex checks, not rail 4/6's embedding- or model-based ones.

---

*Scope note: reflects live testing against a local development build on 2026-08-11 — a functional check of the rules as configured, not a formal security audit.*
