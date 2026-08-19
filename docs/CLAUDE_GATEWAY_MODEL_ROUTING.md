# Claude Gateway Model Routing (LLM RBAC)

Extends `docs/CLAUDE_GATEWAY_ARCHITECTURE.md` §4 ("Model routing") with the role-driven routing LLM
RBAC adds on top of the existing tier mechanism. Nothing about the gateway's tier→model resolution
(`gateway/model_router.py::resolve()`) changed — this document is about *who picks the tier*.

## 1. Tiers

`backend/config/models.yaml` now defines four tier keys, two real models. **Opus is not used anywhere
in this deployment** — `opus`/`reasoning` were repointed to Sonnet (from `claude-opus-5`) so no tier
resolves to it; Sonnet is the ceiling model here.

| Tier key | Model | Used by |
|---|---|---|
| `haiku` | `claude-haiku-4-5-20251001` | The only tier an Employee-role request can ever resolve to. |
| `sonnet` | `claude-sonnet-5` | Default tier for HR/PM. |
| `opus` | `claude-sonnet-5` | HR/PM action-based escalation; CEO/Admin's default escalation target. Same model as `sonnet` — the name is an RBAC permission tier, not a model promise. |
| `fast` | `claude-haiku-4-5-20251001` | Internal, non-role-driven callers (see §3) — router classification, memory summarization, query rewrite, generation judge. |
| `reasoning` | `claude-sonnet-5` | Reserved for a future caller that needs the ceiling tier. |

`sonnet`/`opus` are not a rename of `fast`/`reasoning` — they're separate config keys, kept distinct so
the two vocabularies (capability tier vs. role-driven model choice) can diverge later without one
accidentally assuming the other never will. That distinction is also why swapping `fast` from Opus to
Haiku (this pass) only touched `config/models.yaml`, not `llm_rbac.yaml` or any call site that names a
tier — the tier names are stable; only what they resolve to changed.

## 2. Who picks the tier

**Never the client.** `ChatRequest`/`SearchRequest` have no model/tier field — there never was one,
and LLM RBAC keeps it that way structurally: `services/llm_rbac/engine.py::authorize_llm_request()`
resolves the tier from role + (optional) action, and that's the tier `run_agent()` binds the model to.

| Role | Tiers allowed | Default | Escalates to Opus when |
|---|---|---|---|
| Employee (`user`) | `sonnet` only | `sonnet` | Never — `tiers_allowed` makes it structurally impossible, not just a default. |
| HR | `sonnet`, `opus` | `sonnet` | `action` is `workforce_planning` or `leave_analytics` |
| Project Manager | `sonnet`, `opus` | `sonnet` | `action` is `engineering_planning` or `risk_assessment` |
| CEO/Admin | `sonnet`, `opus` | `sonnet` | `action` matches *any* other role's escalation trigger (the union, computed at config-load time — see `policy_loader.py`'s `dynamic: true` handling) |

## 3. Action-based escalation, not a complexity classifier

`ChatRequest.action` / `SearchRequest.action` is an optional field a client can set to a capability
name from `llm_rbac.yaml`'s permission catalog (e.g. `"workforce_planning"`) — think a role-specific
"quick action" button in the UI, not free-text intent parsing. When present, it does two things:
the fine-grained permission allow/deny check, and tier escalation.

This is deliberately **not** a free-text complexity classifier (no NLP call decides "this chat
message sounds like workforce planning"). Two reasons: first, this repo has no existing
complexity-scoring mechanism, and building an ML classifier is out of scope for a governance task;
second, a naive keyword-based classifier would be worse than no classifier at all — false negatives
silently deny escalation a role should get, false positives silently escalate cost for no reason,
and either failure mode is invisible to the person debugging it. A deterministic, caller-supplied
action name is honest about what v1 actually does. Omitting `action` (the common case — a normal chat
turn) still gets full role/department/tool/quota governance at the role's default tier.

**Named extension point**: replacing the caller-supplied `action` with an inferred one (NLP intent
classification, or tier escalation keyed off which tool the planner actually invoked mid-turn) can
slot in without changing `engine.py`'s public interface — `authorize_llm_request()` already takes
`action` as a parameter; only what supplies that string would change.

**`report_type` is a separate field, not folded into `action`.** `ChatRequest.report_type`
(`services/llm_rbac/report_policy.py::authorize_report()`) answers "may this role generate this
report type," a distinct question from `action`'s "does this request qualify for Opus escalation."
Model tier resolution for a report-generation turn is unaffected by `report_type` — it's still driven
entirely by `action`, exactly as any other chat turn. See `docs/REPORT_AUTHORIZATION.md` §6 for why
the two weren't merged into one field.

## 4. Who is NOT governed by this

`services/evaluation/generation_judge.py` (LLM-as-judge eval scoring) and
`services/memory/store.py::maybe_summarize()` (conversation summarization) call
`claude_gateway.generate()` directly with a hardcoded tier — they are internal system processes, not
driven by an end-user's role, and stay outside `services/llm_rbac/engine.py`'s policy loop
entirely. Their tier choice is unaffected by anything in `llm_rbac.yaml`.

## 5. Pricing

`backend/config/models.yaml`'s `pricing` block now has a `claude-sonnet-5` entry
(`$3`/`$15` per million input/output tokens, list pricing) alongside the existing `claude-opus-5`
entry (`$15`/`$75`) — used only for the `cost_usd` estimate on each audit-log row and the
monthly-cost-budget check (`services/llm_rbac/quotas.py`), not for billing. Update both to match your
actual Anthropic pricing agreement.
