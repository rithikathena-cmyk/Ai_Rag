"""Deterministic answers to the Copilot's read-only questions.

Every answer is assembled from live data — `resolve_pii_policy()`,
`policy_loader.role_config()`, the entity registry, the check table — and
never from a model. This is the whole point: an administrator asking "what can
HR see?" is asking a question with one correct answer that already exists in
configuration. A generated answer would be persuasive whether or not it
matched, which is worse than refusing.

If the data cannot answer the question, these functions say so rather than
filling the gap.
"""

from __future__ import annotations

from app.core.permissions import Permission
from app.services.guardrail_policy.entities import ENTITY_REGISTRY, enforceability_warning
from app.services.guardrail_policy.pii_policy import resolve_pii_policy
from app.services.guardrail_policy.validation import CRITICAL_PII_ENTITIES
from app.services.llm_rbac import policy_loader
from app.services.policy_copilot import entities_view
from app.services.policy_copilot.knowledge import (
    ALL_CHECKS, INPUT_CHECKS, KNOWN_ROLES, OTHER_CONTROLS, OUTPUT_CHECKS,
    PERMISSION_MEANING, POST_CHECKS, ROLE_LABELS, find_check,
)


def _row(cells: list[str], widths: list[int]) -> str:
    return "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()


# --------------------------------------------------------------------------
# Policies
# --------------------------------------------------------------------------

def list_policies(entity: str | None = None) -> str:
    """The active PII policy for every entity, or one entity if named."""
    names = [entity.strip().upper()] if entity else sorted(ENTITY_REGISTRY)
    unknown = [n for n in names if n not in ENTITY_REGISTRY]
    if unknown:
        return f"I don't recognise the entity {unknown[0]!r}. Ask me to \"show all PII policies\" to see the full list."

    widths = [16, 8, 8, 9, 12]
    lines = [
        _row(["ENTITY", "INPUT", "OUTPUT", "SOURCE", "DETECTION"], widths),
        _row(["-" * 16, "-" * 8, "-" * 8, "-" * 9, "-" * 12], widths),
    ]
    caveats: list[str] = []
    for name in names:
        spec = ENTITY_REGISTRY[name]
        res = resolve_pii_policy(name)
        source = res.source + ("*" if res.disabled_row_present else "")
        lines.append(_row([name, res.input_action, res.output_action, source, spec.detection.value.lower()], widths))
        if not spec.is_enforceable:
            caveats.append(f"{name}: no detector emits this entity, so its policy has no runtime effect.")
        if res.disabled_row_present:
            caveats.append(f"{name}: a disabled rule exists — the safe default is what is actually in force.")

    out = ["Active PII policy:", "", *lines]
    if caveats:
        out += ["", "Worth knowing:"] + [f"  - {c}" for c in caveats]
    out += ["", "\"source\" is where the action came from: custom = an enabled rule, default = the built-in safe default."]
    return "\n".join(out)


def explain_policy(entity: str | None) -> str:
    """Why a specific entity is handled the way it is."""
    if not entity:
        return (
            "Which entity would you like explained? For example: "
            "\"why are credit cards redacted?\" or \"explain the SSN policy\"."
        )
    name = entity.strip().upper()
    spec = ENTITY_REGISTRY.get(name)
    if spec is None:
        return f"I don't recognise the entity {name!r}."

    res = resolve_pii_policy(name)
    lines = [
        f"{name} — currently {res.input_action} on input, {res.output_action} on output.",
        "",
    ]
    if res.source == "custom":
        lines.append("This comes from an enabled policy rule, not the built-in default.")
    else:
        lines.append("No custom rule is in force, so this is the built-in safe default.")
    if res.disabled_row_present:
        lines.append(
            "A rule for this entity exists but is switched OFF. Disabling a rule does not permit the "
            "entity — protection reverts to the safe default, which is what you see above."
        )
    if res.dry_run:
        lines.append("The rule is in DRY_RUN, so it is detected and recorded but never acted on.")
    if res.reveal_last:
        lines.append(
            f"Where the action is MASK, the last {res.reveal_last} characters stay visible; "
            "everything before them is replaced."
        )

    overrides = entities_view.role_overrides(name)
    if overrides:
        lines += ["", "Per-role exceptions — every other role gets the actions above:"]
        for role, actions in overrides.items():
            lines.append(
                f"  {ROLE_LABELS.get(role, role)}: {actions['input_action']} on input, "
                f"{actions['output_action']} on output."
            )

    lines += ["", f"Detected by: {spec.detector} ({spec.detection.value.lower()})."]
    warning = enforceability_warning(name)
    if warning:
        lines.append(warning)
    if name in CRITICAL_PII_ENTITIES:
        lines.append(
            "This is a critical entity: weakening it requires explicit approval rather than applying "
            "immediately."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Guardrails
# --------------------------------------------------------------------------

def explain_guardrails(check_name: str | None = None) -> str:
    if check_name:
        check = find_check(check_name)
        if check is None:
            return (
                f"I don't have a check called {check_name!r}. Ask \"what guardrails do you have?\" "
                "for the full list."
            )
        return "\n".join([
            f"{check.name}",
            "",
            f"  Runs on   : {check.direction}",
            f"  Type      : {check.kind}",
            f"  Catches   : {check.catches}",
            f"  On a hit  : {check.on_hit}",
        ])

    widths = [26, 11, 11]
    out = [
        f"{len(INPUT_CHECKS)} input checks, {len(OUTPUT_CHECKS)} output checks, "
        f"plus {len(POST_CHECKS) + len(OTHER_CONTROLS)} that run outside those two passes.",
        "",
        "INPUT — in execution order:",
    ]
    for c in INPUT_CHECKS:
        out.append("  " + _row([c.name, c.kind, c.on_hit], widths))
    out += ["", "OUTPUT — in execution order:"]
    for c in OUTPUT_CHECKS:
        out.append("  " + _row([c.name, c.kind, c.on_hit], widths))
    out += ["", "Outside the sequential passes:"]
    for c in POST_CHECKS + OTHER_CONTROLS:
        out.append("  " + _row([c.name, c.kind, c.on_hit], widths))
    out += ["", "Ask about any one of them by name for what it catches."]
    return "\n".join(out)


# --------------------------------------------------------------------------
# Access / RBAC
# --------------------------------------------------------------------------

def _label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def explain_access(role: str | None = None, permission: str | None = None) -> str:
    """Answers "what can HR see?" and "who can access audit logs?" from the
    real role configuration — read-only, and never inferred."""
    if permission:
        perm = permission.strip().upper()
        holders = []
        for r in KNOWN_ROLES:
            granted = policy_loader.role_config(r).granted_permissions
            if perm in granted or "*" in granted:
                holders.append(_label(r))
        meaning = PERMISSION_MEANING.get(perm)
        if meaning is None:
            return f"I don't recognise the permission {perm!r}."
        if not holders:
            return f"No role currently holds {perm} ({meaning})."
        return f"{perm} — {meaning}.\n\nHeld by: {', '.join(holders)}."

    if not role:
        return (
            "Which role? For example: \"what can HR see?\" or \"who can approve policy changes?\"."
        )

    key = role.strip().lower().replace(" ", "_")
    if key not in KNOWN_ROLES:
        return f"I don't recognise the role {role!r}. Roles are: " + ", ".join(_label(r) for r in KNOWN_ROLES) + "."

    cfg = policy_loader.role_config(key)
    granted = set(cfg.granted_permissions)
    wildcard = "*" in granted

    can = [PERMISSION_MEANING[p] for p in PERMISSION_MEANING if wildcard or p in granted]
    cannot = [PERMISSION_MEANING[p] for p in PERMISSION_MEANING if not wildcard and p not in granted]

    lines = [
        f"{_label(key)} —",
        "",
        f"  Documents    : {', '.join(cfg.knowledge_departments) or 'none'}",
        f"  Model tiers  : {', '.join(cfg.tiers_allowed) or 'none'}",
        f"  Tools        : {', '.join(cfg.tools) or 'none'}",
        "",
        "  Can:",
    ]
    lines += [f"    - {c}" for c in can] or ["    - nothing"]
    if cannot:
        lines += ["", "  Cannot:"] + [f"    - {c}" for c in cannot]

    denied = sorted(cfg.permissions_deny)[:6]
    if denied:
        lines += ["", f"  Explicitly denied capabilities: {', '.join(denied)}"]
    # PII handling is a separate axis from permissions — a role can hold no
    # special permission at all and still see more of an entity than everyone
    # else, if a policy row exempts it. Answering "what can HR see?" without
    # that would leave out the one thing the question most often means.
    exceptions = [
        (entity, slots)
        for entity in sorted(ENTITY_REGISTRY)
        for slots in [entities_view.role_overrides(entity).get(key)]
        if slots
    ]
    if exceptions:
        lines += ["", "  PII exceptions — this role is treated differently from the others:"]
        for entity, slots in exceptions:
            detail = ", ".join(f"{slot.replace('_', ' ')} {value}" for slot, value in slots.items())
            lines.append(f"    - {entity}: {detail}")
    else:
        lines += [
            "",
            "  PII: handled the same as every other role — no entity has an exception for this role.",
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Simulation with a literal value
# --------------------------------------------------------------------------

def simulate_literal_value(value: str, role: str | None, entity_hint: str | None = None) -> str:
    """"Test what an employee sees for +91 9876543210" — runs the literal,
    admin-supplied value through the REAL detector (`find_pii_labels`) and
    the REAL masking engine (`redact_pii`) under the named role's CURRENT
    resolved policy — the exact code path that would run for a real message,
    never an approximation. Read-only: no proposal, nothing persisted, and
    the value never leaves this one reply."""
    from app.services.guardrails.pii import find_pii_labels, redact_pii

    labels = sorted(set(find_pii_labels(value)))
    if not labels:
        hint = f" (expected {entity_hint}-shaped content)" if entity_hint else ""
        return (
            f"No PII pattern was recognised in {value!r}{hint}, so no PII policy applies to it — "
            "it would pass through unchanged."
        )

    role_label = _label(role) if role else "the base policy (no specific role)"
    rendered, _ = redact_pii(f"value: {value}", direction="output", role=role)
    rendered = rendered.replace("value: ", "", 1)

    lines = [f"As {role_label}, that value would be shown as:", "", f"  {rendered}", "", "Per entity:"]
    for label in labels:
        resolution = resolve_pii_policy(label, role)
        reveal_note = f", last {resolution.reveal_last} visible" if resolution.reveal_last else ""
        lines.append(f"  {label}: {resolution.output_action}{reveal_note}")
    return "\n".join(lines)


def access_matrix() -> str:
    """Every role against the permissions that most affect what they see."""
    interesting = [
        Permission.VIEW_DOCUMENTS.value, Permission.VIEW_ANALYTICS.value,
        Permission.VIEW_AUDIT_LOGS.value, Permission.MANAGE_GUARDRAIL_POLICIES.value,
        Permission.POLICY_APPROVE.value, Permission.SYSTEM_SETTINGS.value,
    ]
    header = ["ROLE"] + [p.replace("VIEW_", "").replace("MANAGE_", "")[:10] for p in interesting]
    widths = [18] + [11] * len(interesting)
    lines = [_row(header, widths), _row(["-" * w for w in widths], widths)]
    for r in KNOWN_ROLES:
        granted = policy_loader.role_config(r).granted_permissions
        cells = [_label(r)] + [
            ("yes" if (p in granted or "*" in granted) else "-") for p in interesting
        ]
        lines.append(_row(cells, widths))
    return "Who can access what:\n\n" + "\n".join(lines)
