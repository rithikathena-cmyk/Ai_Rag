from app.core.config import settings
from app.services.guardrails.custom_regex_check import check_custom_regex
from app.services.guardrails.custom_word_check import check_custom_word
from app.services.guardrails.deberta_injection_check import check_with_deberta
from app.services.guardrails.deberta_precedence import should_override_deberta_block
from app.services.guardrails.decisions import GuardrailDecision
from app.services.guardrails.destructive import check_destructive_intent
from app.services.guardrails.gliner_check import check_with_gliner
from app.services.guardrails.injection import check_prompt_injection
from app.services.guardrails.length import check_length
from app.services.guardrails.output import check_system_prompt_leak
from app.services.guardrails.pii import PIIOccurrenceRecord, redact_pii
from app.services.guardrails.presidio_check import check_with_presidio
from app.services.guardrails.response_generator import generate_user_response
from app.services.guardrails.scope import check_scope
from app.services.guardrails.scope_semantic_check import check_scope_semantic
from app.services.guardrails.secrets import check_secrets
from app.services.guardrails.semantic_check import check_semantic_risk
from app.services.guardrails.toxicity_check import check_toxicity
from app.services.guardrails.types import GuardrailResult, GuardrailStep
from app.services.monitoring.metrics import record_guardrail_event

# step.name -> GuardrailDecision. Wording itself lives in
# response_generator.py (generate_user_response()) — this map only says
# WHICH decision/reason a given check's block corresponds to; it never
# contains a user-facing sentence directly. Three "scope_unclear_*" names
# don't come from any check module's own NAME constant — scope_semantic_
# check.py deliberately reports one of these three instead of its usual
# "scope_semantic_check" name when a low-similarity message also lacks
# request structure (see that module for why), so this map is what turns
# "no clear request, contains what looks like an email" into a
# clarification instead of the same flat "outside scope" refusal a genuine
# off-topic question gets.
_DECISION_MAP: dict[str, GuardrailDecision] = {
    "length_check": GuardrailDecision("BLOCKED", "length"),
    "secret_detected_check": GuardrailDecision("BLOCKED", "secret_detected"),
    "prompt_injection_check": GuardrailDecision("BLOCKED", "prompt_injection"),
    "destructive_intent_check": GuardrailDecision("BLOCKED", "destructive_intent"),
    "semantic_risk_check": GuardrailDecision("BLOCKED", "semantic_risk"),
    "deberta_injection_check": GuardrailDecision("BLOCKED", "deberta_injection"),
    "scope_check": GuardrailDecision("OUT_OF_SCOPE", "scope_keyword"),
    "scope_semantic_check": GuardrailDecision("OUT_OF_SCOPE", "semantic_scope"),
    # SF-03 decomposition: emitted instead of scope_semantic_check's own name
    # when at least one clause is in scope and at least one is not — a
    # distinct reason so response_generator.py can render "I can help with X,
    # but not Y" instead of a flat refusal. See scope_semantic_check.py's
    # module docstring.
    "scope_semantic_mixed": GuardrailDecision("OUT_OF_SCOPE", "mixed_scope"),
    "scope_unclear_pii": GuardrailDecision("UNCLEAR", "pii_reference"),
    "scope_unclear_document": GuardrailDecision("UNCLEAR", "document_reference"),
    "scope_unclear_context": GuardrailDecision("UNCLEAR", "insufficient_context"),
    "toxicity_check": GuardrailDecision("BLOCKED", "toxicity_input"),
    "presidio_check": GuardrailDecision("BLOCKED", "pii_detected_input"),
    "gliner_check": GuardrailDecision("BLOCKED", "pii_detected_input"),
    "pii_redact": GuardrailDecision("BLOCKED", "pii_detected_input"),
    "custom_regex_check": GuardrailDecision("BLOCKED", "custom_policy_rule"),
    "custom_word_check": GuardrailDecision("BLOCKED", "custom_policy_rule"),
}
_DEFAULT_DECISION = GuardrailDecision("BLOCKED", "unknown")

# check_scope_semantic's five possible step names (its own NAME, the mixed-
# scope variant, and the three scope_unclear_* variants it reports instead —
# see that module) are all a GENERIC verdict: "this message doesn't clearly
# match a configured topic," never a specific safety finding. Every other
# check's block is specific (injection, destructive intent, toxicity, a
# named PII type). run_input_guardrails() below defers a block from any of
# these five names instead of returning immediately on it, precisely so a
# later, more specific check running in the SAME pass can still override it
# — e.g. an out-of-scope clause that's ALSO toxic should be reported as
# toxicity, not as scope.
_DEFERRABLE_SCOPE_STEP_NAMES = frozenset(
    {
        "scope_semantic_check", "scope_semantic_mixed",
        "scope_unclear_pii", "scope_unclear_document", "scope_unclear_context",
    }
)

# The subset of _DEFERRABLE_SCOPE_STEP_NAMES that means "no identifiable
# request was found at all" (scope_semantic_check.py's _judge_unclear() —
# reached only when has_request_structure() was already False for the whole
# message). Deliberately EXCLUDES "scope_semantic_check"/"scope_semantic_
# mixed": those mean the opposite — a REAL, distinct request was found, it
# just didn't match any configured topic. The "entirely accounted for by
# validated PII" override below must never apply to that case: "My SSN is
# 123-45-6789, can you look up my file?" has real request structure ("can
# you look up my file?") that is NOT part of the SSN disclosure and is NOT
# in scope — it must stay blocked as out-of-scope, not be waived just
# because the message also happens to contain PII. Only a message with NO
# request at all (a bare "123-45-6789") has nothing left to be "unclear"
# about once its PII is accounted for. Regression: found live via tests/
# test_chat_input_pii_block_persistence.py::test_scope_shadowed_pii_still_
# gets_redacted_before_storage, which the broader (unscoped) version of this
# override silently defeated.
_CONTENT_FREE_SCOPE_STEP_NAMES = frozenset(
    {"scope_unclear_pii", "scope_unclear_document", "scope_unclear_context"}
)


def _record(direction: str, step: GuardrailStep) -> None:
    record_guardrail_event(direction, step.name, step.action, step.detail)


def _blocked_result(
    current: str, step: GuardrailStep, steps: list[GuardrailStep],
    occurrences: list[PIIOccurrenceRecord] | None = None,
) -> GuardrailResult:
    decision = _DECISION_MAP.get(step.name, _DEFAULT_DECISION)
    reason = generate_user_response(decision, detail=step.detail)
    return GuardrailResult(
        text=current, blocked=True, block_reason=reason, steps=steps, blocking_step_name=step.name,
        pii_occurrences=occurrences or [],
    )


def run_input_guardrails(text: str, role: str | None = None) -> GuardrailResult:
    """Check order (relative to each other) is UNCHANGED from before this
    function's shadowing fix, other than check_secrets' addition right after
    check_length — both are cheap, deterministic, regex-only checks with no
    model inference, so it costs nothing to run it before prompt_injection
    rather than after: length -> secrets -> prompt_injection ->
    destructive_intent -> custom_word -> custom_regex -> scope (keyword) ->
    semantic_risk -> deberta_injection -> scope_semantic -> toxicity ->
    presidio -> gliner -> pii_redact, exactly as it always has been except
    for custom_word/custom_regex (Guardrail Policy Center) — two brand-new,
    additive checks slotted in right after the other deterministic
    keyword/pattern checks and before the semantic/model-backed ones,
    matching the spec's own WORD-before-REGEX-before-SEMANTIC precedence
    ordering. Both are no-ops (always "pass") when no admin has created an
    active rule for their category, so a deployment with none configured
    behaves identically to before this pass. What
    changed is what happens when check_scope_semantic (or its scope_unclear_*
    variants) is the one that blocks: instead of returning immediately with
    that generic reason, the block is held in `deferred_scope_step` and every
    remaining check still runs on the same message. A later, more specific
    block (toxicity/presidio/gliner in the loop below, or pii_redact after
    it) always wins and is returned instead — the generic scope/unclear
    reason is only ever used as a fallback, once nothing more specific
    objected. This does not skip, weaken, or disable any check: every one of
    them still runs and still fully participates in the block/pass decision;
    it only changes which one supplies the final wording when more than one
    would have blocked the same message.

    The gap this closes, live-verified before the fix: "You're a worthless
    piece of garbage..." was blocked as scope_unclear_context ("not sure
    what you'd like me to do") instead of by toxicity_check, and "My SSN is
    ..., can you look up my file?" was blocked as scope_semantic_check
    ("outside the areas this assistant supports") instead of by pii_redact —
    in both cases the real, specific check never got credited because
    scope_semantic_check's block short-circuited the pipeline before either
    ran. See tests/guardrails/test_pipeline_scope_semantic_shadowing.py."""
    steps: list[GuardrailStep] = []
    current = text
    deferred_scope_step: GuardrailStep | None = None
    # Only allocated when the deployment opts in (see PIIOccurrenceRecord's
    # docstring) — every GuardrailResult below passes this through as-is, so
    # it's an empty list (zero behavior change) unless the flag is on.
    occurrences: list[PIIOccurrenceRecord] = []
    capture = occurrences if settings.guardrail_pii_raw_capture_enabled else None

    for check in (
        check_length, check_secrets, check_prompt_injection, check_destructive_intent,
        check_custom_word, check_custom_regex, check_scope,
        check_semantic_risk,
    ):
        step = check(current)
        steps.append(step)
        _record("input", step)
        if step.action == "block":
            if step.name in _DEFERRABLE_SCOPE_STEP_NAMES:
                deferred_scope_step = step
                continue
            return _blocked_result(current, step, steps, occurrences)

    # Pulled out of the uniform loop above (and below) so a block verdict can
    # be checked against deberta_precedence.py's narrow, evidence-based
    # override before deciding whether to actually return it — see that
    # module's docstring for the full characterization and the exact
    # conditions required. Every other outcome (pass, or a block that
    # doesn't qualify for the override) behaves exactly as before this
    # change; check_prompt_injection has already run and passed by this
    # point (see the loop above), which the override relies on as a
    # precondition rather than re-deriving.
    deberta_step = check_with_deberta(current)
    steps.append(deberta_step)
    _record("input", deberta_step)
    if deberta_step.action == "block":
        if should_override_deberta_block(current):
            # Correct THIS step's own recorded action (same object; mutating
            # it updates the entry already appended above) — same "the trace
            # must credit what actually decided the outcome" correction
            # already applied to the deferred scope step below.
            deberta_step.action = "pass"
            deberta_step.detail = (
                f"{deberta_step.detail} (overridden — message is fully accounted for "
                "by validated PII disclosure, see deberta_precedence.py)"
            )
        else:
            return _blocked_result(current, deberta_step, steps, occurrences)

    for check in (check_scope_semantic, check_toxicity, check_with_presidio):
        step = check(current)
        steps.append(step)
        _record("input", step)
        if step.action == "block":
            if step.name in _DEFERRABLE_SCOPE_STEP_NAMES:
                deferred_scope_step = step
                continue
            return _blocked_result(current, step, steps, occurrences)

    # Not in the uniform loop above: check_with_gliner() also returns
    # (possibly-modified text, step) — same dual-return shape as redact_pii()
    # right below, since it redacts its own validated findings rather than
    # blocking on them (see that module's docstring). Runs at the same
    # position it always has (last, immediately before redact_pii()) — only
    # the calling convention changed, not the order.
    current, gliner_step = check_with_gliner(current, capture=capture)
    steps.append(gliner_step)
    _record("input", gliner_step)
    if gliner_step.action == "block":
        # Only reachable via the detector's own failure path (fail_closed on
        # exception) — a successful detection never lands here; see
        # gliner_check.py's docstring.
        return _blocked_result(current, gliner_step, steps, occurrences)
    if gliner_step.action == "redact" and settings.guardrail_pii_block_input:
        # Same policy pii_redact's own redact result gets a few lines below:
        # on input, the user themselves is the source, so a detected PII
        # span is blocked outright by default rather than forwarded
        # redacted (guardrail_pii_block_input=True is the default) — applying
        # the identical gate here keeps every input-side PII detector under
        # one consistent policy instead of GLiNER quietly being softer than
        # the regex-based check right next to it. "gliner_check" (not
        # "pii_redact") as the blocking step name: chat.py's
        # _WITHHELD_PLACEHOLDERS already has a dedicated entry for it.
        reason = generate_user_response(_DECISION_MAP["pii_redact"])
        return GuardrailResult(
            text=current, blocked=True, block_reason=reason, steps=steps, blocking_step_name="gliner_check",
            pii_occurrences=occurrences,
        )

    current, pii_step = redact_pii(current, direction="input", role=role, capture=capture)
    steps.append(pii_step)
    _record("input", pii_step)
    # An explicit Guardrail Policy Center BLOCK/ESCALATE action for a
    # matched entity (pii.py::_resolve_match()) always blocks, regardless of
    # the guardrail_pii_block_input toggle below — a per-entity admin policy
    # decision is more specific than that blanket setting and must win.
    if pii_step.action == "block":
        reason = generate_user_response(_DECISION_MAP["pii_redact"])
        return GuardrailResult(
            text=current, blocked=True, block_reason=reason, steps=steps, blocking_step_name="pii_redact",
            pii_occurrences=occurrences,
        )
    # Unlike output-side PII (below), input PII containing a request is
    # blocked outright by default (guardrail_pii_block_input) rather than
    # forwarded redacted — the user themselves is the source here, so there
    # is no "the model already generated it, redaction is what's left"
    # rationale the output path has. Set guardrail_pii_block_input=False to
    # restore the original redact-and-continue behavior. This still wins
    # over a deferred scope block, same as every other specific check above.
    if pii_step.action == "redact" and settings.guardrail_pii_block_input:
        reason = generate_user_response(_DECISION_MAP["pii_redact"])
        return GuardrailResult(
            text=current, blocked=True, block_reason=reason, steps=steps, blocking_step_name="pii_redact",
            pii_occurrences=occurrences,
        )

    if deferred_scope_step is not None:
        # Scope was judged on text that still contained PII. Redaction has
        # since replaced those spans with placeholders that carry no topical
        # meaning of their own, and their presence in the embedded string
        # actively drags the similarity score down — so a question whose
        # *subject* is perfectly in scope can be rejected for the contact
        # details it merely happened to mention alongside it.
        #
        # Live-verified before this fix: "My email is <address> and my phone
        # is <number> - what is the PPE policy for my shift?" scored
        # best=0.54 and was refused as out-of-scope, while the PPE question
        # on its own is plainly in scope. Re-judging the redacted text asks
        # the question that actually matters: is what they're ASKING in
        # scope, ignoring what they incidentally included.
        #
        # redact_pii() WITHOUT direction= is the long-standing
        # policy-unaware path (see its docstring: audit-log sanitization,
        # blocked-input storage, the eval harness all use it) — every
        # detected entity is masked regardless of the per-entity action. That
        # matters here: the enforcement call above runs with direction="input"
        # and so honors FLAG, which deliberately leaves the value in place,
        # meaning `current` still contains the very spans distorting the
        # score. This masked copy is used ONLY to re-score scope — it is
        # never assigned to `current`, never persisted, never sent to the
        # model, and never returned to the user.
        #
        # Guarded on the masked text actually differing from what scope
        # originally judged, so the extra embedding call is confined to
        # messages that really do carry PII rather than added to every
        # request. Emitted under its own step name so the trace records both
        # judgements honestly — the first block genuinely happened, and this
        # is what overrode it.
        #
        # mode_override="placeholder" is load-bearing, not cosmetic. This
        # deployment runs guardrail_pii_mode="mask", whose token is a partial
        # reveal ("ja######.com") — to an embedding model that is noise, and
        # measured against the real configured topics it scores no better
        # than the raw value: 0.544 raw -> 0.549 masked, both under the 0.55
        # threshold. "[REDACTED_EMAIL]" reads as a clean type marker and
        # scores 0.588, which clears it. The scoring copy is discarded
        # immediately, so this affects nothing a user or the model ever sees.
        scoring_text, _ = redact_pii(current, mode_override="placeholder")
        if scoring_text == text:
            return _blocked_result(current, deferred_scope_step, steps, occurrences)
        recheck = check_scope_semantic(scoring_text)
        recheck_step = GuardrailStep("scope_semantic_recheck", recheck.action, recheck.detail)
        steps.append(recheck_step)
        _record("input", recheck_step)
        if recheck.action != "block":
            # The deferred block is being overridden — correct ITS OWN entry
            # in `steps` (same object; mutating it here updates the one
            # already appended above, no need to find/replace by index) so
            # the trace stops reporting "block" for a verdict that was
            # reconsidered and dropped. Left as "block", any caller crediting
            # "whichever step's action is block/redact" for this outcome
            # (e.g. an audit view, or tests/security/framework.py's
            # primary_guardrail()) would misattribute a message that was
            # ultimately allowed/redacted to a check that, in the end,
            # decided nothing — the same shadowing this function's deferred-
            # block design exists to prevent, just recurring on the
            # "cleared" branch instead of the "still blocked" one.
            deferred_scope_step.action = "pass"
            deferred_scope_step.detail = (
                f"{deferred_scope_step.detail} (superseded — cleared by redacted-text re-check: {recheck.detail})"
            )
            deferred_scope_step = None

    if (
        deferred_scope_step is not None
        and deferred_scope_step.name in _CONTENT_FREE_SCOPE_STEP_NAMES
        and (pii_step.action == "redact" or gliner_step.action == "redact")
    ):
        # The rescoring above still didn't clear the deferred scope block —
        # expected when the message has no other content once its PII is
        # redacted (e.g. a bare "123-45-6789" has nothing left to score as
        # in-scope; see _judge_unclear()/looks_like_pii() in
        # scope_semantic_check.py for why this specific message even reaches
        # scope_unclear_pii in the first place). Reuses deberta_precedence.
        # should_override_deberta_block()'s own "is the whole message
        # accounted for by validated PII disclosure" check (see that module)
        # for the identical reason it exists there: a message that is
        # ENTIRELY a validated PII value has no separate "topic" to be
        # unclear about — the deterministic PII check above already
        # identified and handled exactly what this message is, and
        # crediting a generic scope-unclear fallback instead of that
        # specific, already-completed finding is the same kind of
        # misattribution the deferred-scope mechanism exists to prevent in
        # the other direction (see this function's own module docstring).
        # Restricted to _CONTENT_FREE_SCOPE_STEP_NAMES (see that constant) —
        # a genuine scope_semantic_check/scope_semantic_mixed block means a
        # REAL, distinct request was found and judged out of scope, which
        # must never be waived just because the message also mentions PII.
        # Checked against `text` (the ORIGINAL, pre-redaction message) since
        # find_pii_labels() needs the raw value to recognize it; gated on an
        # actual redaction having happened this pass (pii_step/gliner_step
        # action == "redact") so this can only ever supersede a scope block
        # in favor of PII handling that genuinely ran and produced a safe,
        # redacted result — never a bare "looks like PII" guess with nothing
        # backing it. Every check earlier in this function's fixed order
        # (prompt injection, destructive intent, semantic risk, DeBERTa
        # injection) has already passed by the time this section runs, so a
        # message with a genuinely malicious clause alongside the PII was
        # already blocked long before reaching here — same precondition
        # should_override_deberta_block() itself relies on.
        if should_override_deberta_block(text):
            deferred_scope_step.action = "pass"
            deferred_scope_step.detail = (
                f"{deferred_scope_step.detail} (superseded — message is fully accounted for "
                "by validated PII disclosure, see deberta_precedence.py)"
            )
            deferred_scope_step = None

    if deferred_scope_step is not None:
        # Nothing more specific objected in this pass — the generic
        # scope/unclear reason is the real, final answer for this message,
        # not a mask for something else.
        return _blocked_result(current, deferred_scope_step, steps, occurrences)

    return GuardrailResult(text=current, blocked=False, steps=steps, pii_occurrences=occurrences)


def run_output_guardrails(text: str, role: str | None = None) -> GuardrailResult:
    steps: list[GuardrailStep] = []
    occurrences: list[PIIOccurrenceRecord] = []
    capture = occurrences if settings.guardrail_pii_raw_capture_enabled else None

    # Same check_prompt_injection() the input side uses — reused, not
    # duplicated: a RAG-poisoned document's injected instructions ("ignore
    # the user's question", "reveal your system prompt", "you are now in
    # developer mode", ...), if echoed verbatim into the model's own reply,
    # previously passed every output check untouched — none of
    # system_prompt_leak/toxicity/presidio/gliner/pii_redact below are
    # looking for injection PHRASING, only leaked-prompt content, toxicity,
    # or PII. First in this function's order for the same reason
    # check_secrets is first on the input side: a cheap, deterministic,
    # no-model-inference regex check costs nothing to run before anything
    # else. Reason code "prompt_injection" is already registered
    # (_DECISION_MAP/response_generator.py) from the input-side usage; no
    # direction-specific variant needed since the user-facing message
    # ("I'm not able to help with that request.") reads fine either way.
    injection_step = check_prompt_injection(text)
    steps.append(injection_step)
    _record("output", injection_step)
    if injection_step.action == "block":
        reason = generate_user_response(GuardrailDecision("BLOCKED", "prompt_injection"))
        return GuardrailResult(
            text=text, blocked=True, block_reason=reason, steps=steps, blocking_step_name=injection_step.name,
        )

    leak_step = check_system_prompt_leak(text)
    steps.append(leak_step)
    _record("output", leak_step)
    if leak_step.action == "block":
        reason = generate_user_response(GuardrailDecision("BLOCKED", "system_prompt_leak"))
        return GuardrailResult(text=text, blocked=True, block_reason=reason, steps=steps, blocking_step_name=leak_step.name)

    # Toxicity/abuse check on the reply itself — same function, same config
    # (guardrails.yaml's toxicity_check:) as the input-side call above;
    # mirrors presidio_check/gliner_check's dual-sided wiring. Distinct
    # reason ("toxicity_output" vs input's "toxicity_input") since the two
    # directions need different wording — one's about the user's message,
    # the other's about the assistant's own reply.
    toxicity_step = check_toxicity(text)
    steps.append(toxicity_step)
    _record("output", toxicity_step)
    if toxicity_step.action == "block":
        reason = generate_user_response(GuardrailDecision("BLOCKED", "toxicity_output"))
        return GuardrailResult(text=text, blocked=True, block_reason=reason, steps=steps, blocking_step_name=toxicity_step.name)

    # Second-pass semantic PII check on the reply itself — same function,
    # same config (guardrails.yaml's presidio_check:), same allowlist as the
    # input-side call in run_input_guardrails(); see presidio_check.py's
    # module docstring for why a generated reply can carry the same
    # structurally-precise identifier types (passport/IBAN/bank account/...)
    # this check targets on input, which pii.py's regex layer below has no
    # recognizer for either way.
    presidio_step = check_with_presidio(text)
    steps.append(presidio_step)
    _record("output", presidio_step)
    if presidio_step.action == "block":
        reason = generate_user_response(GuardrailDecision("BLOCKED", "pii_detected_output"))
        return GuardrailResult(text=text, blocked=True, block_reason=reason, steps=steps, blocking_step_name=presidio_step.name)

    # Second semantic PII pass on the reply — same complementary relationship
    # to presidio_step as on the input side (see gliner_check.py's module
    # docstring): a curated natural-language label set catching PII shapes
    # neither Presidio's allowlist nor pii.py's regex below recognize.
    # Redacts its own validated findings and chains the result into
    # redact_pii() next, same as input side — a reply Claude generated gets
    # redacted-and-returned, never blocked, on a successful GLiNER
    # detection; only the detector's own failure path still blocks (see
    # gliner_check.py's docstring).
    gliner_redacted, gliner_step = check_with_gliner(text, capture=capture)
    steps.append(gliner_step)
    _record("output", gliner_step)
    if gliner_step.action == "block":
        reason = generate_user_response(GuardrailDecision("BLOCKED", "pii_detected_output"))
        return GuardrailResult(
            text=text, blocked=True, block_reason=reason, steps=steps, blocking_step_name=gliner_step.name,
            pii_occurrences=occurrences,
        )

    redacted, pii_step = redact_pii(gliner_redacted, direction="output", role=role, capture=capture)
    steps.append(pii_step)
    _record("output", pii_step)
    # New this pass: a Guardrail Policy Center PII policy can set the
    # OUTPUT action for an entity to BLOCK/ESCALATE (e.g. "SSN in Claude's
    # own reply must never be returned, not just redacted") — pii.py's
    # redact_pii() now surfaces that as action="block" the same way
    # presidio_check/gliner_check above already can. Before this pass,
    # output-side PII could only ever be redacted-and-returned, never
    # block the whole reply — this is the one deliberate behavior change
    # the approved plan makes to this function.
    if pii_step.action == "block":
        reason = generate_user_response(GuardrailDecision("BLOCKED", "pii_detected_output"))
        return GuardrailResult(
            text=text, blocked=True, block_reason=reason, steps=steps, blocking_step_name=pii_step.name,
            pii_occurrences=occurrences,
        )

    return GuardrailResult(text=redacted, blocked=False, steps=steps, pii_occurrences=occurrences)
