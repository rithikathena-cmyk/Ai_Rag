# Guardrail Policy Research Agent

**An integrated extension to Policy Copilot for researching guardrail policies and proposing improvements.**

## Overview

The Guardrail Policy Research Agent allows admins and CEOs to:

1. **Analyze** current guardrail policy configuration and PII entity coverage
2. **Compare** policies across entities or roles to identify inconsistencies
3. **Optimize** existing policies based on best practices and gap analysis
4. **Audit** policies for compliance and consistency
5. **Design** new policies and detector patterns for new PII types

All research proposals feed into the existing Policy Copilot approval workflow. **No changes are applied automatically.** Every proposal requires human review and approval before taking effect.

## Architecture

### Module Structure

```
backend/app/services/policy_copilot/research/
├── __init__.py              # Public API exports
├── scope.py                 # GUARDRAIL_ONLY scope enforcement
├── request_classifier.py    # Intent classification (ANALYZE, COMPARE, etc.)
├── tools.py                 # Read-only guardrail tool allowlist
├── orchestrator.py          # Main research flow controller
```

### Integration Points

**Existing Copilot Components Reused:**
- `ApprovalRequestModel` — Store research proposals
- `validation.py` — Validate generated proposals
- `apply.py` — Apply approved research to registry
- `guardrail_policy/store.py` — Read current policies
- `audit/logger.py` — Log research activities
- `auth/rbac.py` — Permission gating (POLICY_PROPOSE)

**No Duplication:** The research agent shares the existing approval workflow, policy engine, and audit infrastructure with the standard Policy Copilot.

## Security Model

Multi-layered defense-in-depth:

### Layer 1: HTTP Authorization
- Router endpoint gated on `POLICY_PROPOSE` permission
- Same as Policy Copilot `/chat` endpoint
- Enforced by `require_permission()` dependency

### Layer 2: Request-Level Scope Check
- **Deterministic** (regex-based) classification
- Rejects queries about documents, users, conversations, audit logs
- Only allows guardrail-related queries
- No LLM involved — guaranteed deterministic rejection

### Layer 3: Tool Allowlist Enforcement
- **Strict allowlist** of read-only guardrail tools only
- Tools cannot write policies, access external APIs, or read user data
- Every tool invocation validated before execution
- Prevents LLM from bypassing allowlist

### Layer 4: Code-Layer Assertions
- `assert_guardrail_only_scope()` at every resource access
- Even if layers 1-3 somehow fail, code-level checks prevent data leaks
- Assertion failures are logged and bubble to caller

### Layer 5: Audit Logging
- Every research request logged separately (`POLICY_RESEARCHED` event)
- Includes scope classification result, intent, proposals generated
- Enables post-hoc security review

## API Reference

### POST /policy-copilot/research

**Request:**
```json
{
  "query": "Analyze email and phone masking policies"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Research complete: 2 proposal(s) generated",
  "scope": {
    "type": "pii_entity_config",
    "allowed": true,
    "reason": "Guardrail research query (pii_entity_config)"
  },
  "intent": {
    "type": "analyze",
    "entity": "EMAIL",
    "focus_area": "masking",
    "confidence": 0.92
  },
  "proposals": [
    {
      "entity": "EMAIL",
      "change_type": "POLICY_UPDATE",
      "description": "Standardize email masking...",
      "rationale": "Current policies inconsistent...",
      "impacts": ["5 roles affected", "2 policies updated"],
      "risks": ["May affect existing integrations"]
    }
  ],
  "trace": [
    {
      "phase": "scope_check",
      "status": "OK",
      "detail": "Guardrail research query (pii_entity_config)"
    },
    {
      "phase": "intent_classification",
      "status": "OK",
      "detail": "Intent: analyze, confidence: 0.92"
    }
  ]
}
```

### Query Examples

#### Analyze Intent
```
"What is our current email masking policy?"
"Analyze phone number detection and masking"
"How do we currently handle SSN?"
```

#### Compare Intent
```
"Compare email and phone masking policies"
"How do email and credit card policies differ?"
```

#### Optimize Intent
```
"How can we improve our SSN detection?"
"Suggest improvements to our masking strategies"
```

#### Audit Intent
```
"Check if our policies are consistent"
"Find gaps in our PII entity coverage"
```

#### Design Intent
```
"Design a new detector for API keys"
"Create a policy for vehicle plates"
```

## Scope Classification

### Allowed (GUARDRAIL_ONLY)

Queries mentioning:
- PII entities: email, phone, SSN, credit card, passport, address, API key, etc.
- Guardrail concepts: mask, redact, block, flag, policy, detector, pattern, entity, etc.
- Actions: analyze, compare, optimize, audit, check, review, examine

### Forbidden

Queries mentioning:
- Documents, document content, file access
- Users, employees, user data
- Conversations, messages, chat history
- Audit logs, activity traces
- Credentials, payments, billing
- Database queries, code execution, system commands

**Example Rejections:**
- "What documents contain sensitive information?" → FORBIDDEN
- "Show me which users accessed confidential data" → FORBIDDEN
- "Display all conversations with SSNs" → FORBIDDEN

**Deterministic Rejection:** Forbidden keywords are detected via regex before any LLM call or data access. The rejection happens at the HTTP layer, preventing any downstream processing.

## Intent Classification

Deterministic classification (regex-based, no LLM) identifies research type:

| Intent | Keywords | Use Case |
|--------|----------|----------|
| **ANALYZE** | analyze, examine, assess, review | Understand current state |
| **COMPARE** | compare, versus, difference, contrast | Find inconsistencies |
| **OPTIMIZE** | improve, optimize, enhance, suggest | Propose enhancements |
| **AUDIT** | audit, check, verify, validate, compliance | Ensure consistency |
| **DESIGN** | create, design, add, new, build, strategy | Create new policies |

Each query also extracts:
- **Entity**: Specific PII type mentioned (EMAIL, PHONE, SSN, etc.)
- **Focus Area**: Aspect being discussed (masking, blocking, roles, detectors, compliance)
- **Confidence**: 0.0-1.0 score (≥0.5 required to proceed)

## Allowed Tools (Read-Only Access)

Research tools read the guardrail registry only. No writes, no external APIs:

- `GET_ACTIVE_POLICIES` — List policies by category
- `GET_POLICY_DETAILS` — Get full policy config
- `LIST_PII_ENTITIES` — List all PII entity types
- `GET_ENTITY_POLICY` — Get policy for one entity
- `GET_DETECTOR_CONFIG` — Get detector pattern
- `COMPARE_POLICIES` — Compare across entities/roles
- `SIMULATE_POLICY_CHANGE` — Dry-run a change (no execution)

### Forbidden Tools (Always Rejected)

- Any write operation: `WRITE_POLICY`, `DELETE_POLICY`, `CREATE_DETECTOR`, `UPDATE_DETECTOR`, `APPLY_POLICY`
- Any execution: `EXECUTE_ACTION`, `EXECUTE_CODE`, `RUN_SQL`
- Any external access: `CALL_EXTERNAL_API`
- Any data access outside guardrail scope: `ACCESS_AUDIT_LOG`, `ACCESS_MESSAGES`, `ACCESS_USERS`, `ACCESS_DOCUMENTS`

Tool allowlist is enforced at three points:
1. HTTP layer (router dependency)
2. Request layer (enforce_tool_allowlist() before execution)
3. Code layer (assertion before resource access)

## Implementation Status

### Phase 1: Foundation ✅
- [x] Module structure and imports
- [x] Scope classification (GUARDRAIL_ONLY enforcement)
- [x] Intent classification (ANALYZE, COMPARE, OPTIMIZE, AUDIT, DESIGN)
- [x] Tool allowlist and execution
- [x] Orchestrator flow coordination
- [x] Router endpoint integration
- [x] Audit logging integration
- [x] Comprehensive test suite (39 tests, all passing)

### Phase 2: Research Capability (Planned)
- [ ] LLM-assisted policy gap analysis
- [ ] Multi-policy comparison across entities and roles
- [ ] Policy optimization recommendations
- [ ] Detector pattern generation for new formats
- [ ] Impact simulation for proposed changes
- [ ] Role-audience consistency checking

### Phase 3: Advanced Features (Future)
- [ ] Historical policy comparison (across versions)
- [ ] Compliance checking against guardrail policies
- [ ] Automated rollback recommendations
- [ ] Policy consolidation suggestions

## Testing

### Test Coverage
- **39 tests** across 7 categories
- **100% pass rate**

#### Test Categories
1. **Scope Enforcement (11 tests)**
   - Allowed queries (guardrail, PII entity, detector, analysis)
   - Forbidden queries (documents, users, conversations)
   - Empty/short query rejection
   - Code-layer assertion enforcement

2. **Intent Classification (8 tests)**
   - All 5 intent types (ANALYZE, COMPARE, OPTIMIZE, AUDIT, DESIGN)
   - Entity extraction
   - Focus area extraction
   - Unclear intent handling

3. **Tool Allowlist Enforcement (10 tests)**
   - All 7 allowed tools
   - All forbidden tool rejections
   - Unknown tool rejection
   - Invalid input handling

4. **Tool Execution (3 tests)**
   - Allowed tool execution
   - Forbidden tool error handling
   - Missing required arguments handling

5. **Orchestrator Integration (4 tests)**
   - Full research flow for allowed queries
   - Forbidden query rejection at scope phase
   - Unclear intent rejection
   - Phase trace completeness

6. **Security Boundary (3 tests)**
   - Scope check before tool execution
   - Allowlist enforcement before LLM
   - Code-layer assertion enforcement

### Running Tests
```bash
cd backend
python -m pytest tests/test_guardrail_research_agent.py -v
```

## Audit Logging

Every research request is logged with `AuditEventType.POLICY_RESEARCHED`:

```json
{
  "event_type": "POLICY_RESEARCHED",
  "outcome": "SUCCESS|DENIED",
  "actor_id": "user-uuid",
  "actor_role": "admin|ceo",
  "resource_type": "POLICY_RESEARCH",
  "action": "research",
  "metadata": {
    "raw_query": "Analyze email masking policies",
    "success": true,
    "message": "Research complete: 2 proposal(s) generated",
    "scope_type": "pii_entity_config",
    "scope_allowed": true,
    "intent_type": "analyze",
    "proposal_count": 2
  }
}
```

Audit logs enable:
- Security review of who researched what
- Identification of forbidden query attempts
- Tracking of research leading to proposals
- Investigation of policy changes

## Integration with Policy Copilot Approval Workflow

Research proposals feed into the existing approval workflow:

```
Research Query
      ↓
Scope Check → Intent Classification → Tool Execution
      ↓
Proposal Generation → Validation
      ↓
ApprovalRequestModel created
      ↓
Human Review (Admin/CEO)
      ↓
Approve → Apply (via guardrail_policy/service.py)
   or
Reject → No changes
```

Same approval endpoints:
- `POST /policy-copilot/proposals/{id}/approve`
- `POST /policy-copilot/proposals/{id}/reject`

Research proposals have `target_type="policy_research_proposal"` to distinguish them from standard Policy Copilot proposals in audit logs and queries.

## Security Considerations

### Design Principles

1. **Zero Trust Scope** — Scope classification happens first, unconditionally
2. **Defense in Depth** — Multiple independent security checks
3. **No LLM in Security Decisions** — Scope and intent use deterministic regex
4. **Read-Only Access** — All tools are read-only guardrail registry access
5. **Audit Everything** — Every action logged separately
6. **Human Approval Required** — No automatic policy application

### Known Limitations

- **Scope Classification:** Regex-based; may have false positives/negatives on novel phrasing
- **Tool Simulation:** SIMULATE_POLICY_CHANGE is dry-run only (no actual impact calculation yet)
- **LLM Integration:** Not yet wired for Phase 2; current implementation has placeholder LLM calls

### Future Hardening

- Add LLM-based scope validation (secondary check only)
- Implement rate limiting on research endpoints
- Add query length validation and tokenization
- Implement tool execution timeouts
- Add resource consumption limits

## Troubleshooting

### Query Rejected as Out of Scope

**Symptom:** "Research rejected: Query does not mention guardrail, PII, or detection concepts"

**Solution:** Include PII entity names (EMAIL, PHONE, SSN) or guardrail keywords (mask, detect, policy, pattern) in your query.

**Example Fix:**
- ❌ "Analyze sensitive data" → ✅ "Analyze email and phone masking"
- ❌ "Review user policies" → ✅ "Review PII policies"

### Intent Not Classified

**Symptom:** "Could not determine research intent. Please be more specific."

**Solution:** Add action verbs (analyze, compare, optimize, audit, design) to your query.

**Example Fix:**
- ❌ "Email masking" → ✅ "Analyze email masking"
- ❌ "SSN and credit cards" → ✅ "Compare SSN and credit card policies"

### Forbidden Query Attempt

**Symptom:** "Research rejected: Query mentions non-guardrail topics..."

**Solution:** Remove references to documents, users, conversations, or external systems.

**Example Fix:**
- ❌ "Which users accessed sensitive documents?" → ✅ "How do we detect sensitive data?"

## Related Documentation

- [Policy Copilot Architecture](./POLICY_COPILOT_ARCHITECTURE.md)
- [Guardrail Policy Engine](./POLICY_COPILOT_SECURITY.md)
- [Audit Logging](./docs/REQUEST_PIPELINE.md)
- [RBAC and Permissions](./ROLE_PERMISSION_MATRIX.md)

## Contact

For questions or issues with the research agent:
1. Check the troubleshooting section above
2. Review audit logs for detailed rejection reasons
3. Consult the test suite for usage examples
4. Open an issue with request logs and expected outcome
