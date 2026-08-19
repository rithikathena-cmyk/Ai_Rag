# Copilot Control Policy

**Version:** 1.0  
**Effective Date:** 2026-08-19  
**Owner:** Security & Compliance Team

---

## 1. Purpose

This policy defines guardrails, approval workflows, and access controls for the AI Guardrails copilot system to ensure:

- ✅ PII (Personally Identifiable Information) is never exposed without approval
- ✅ Sensitive data access is logged and audited
- ✅ Role-based access control (RBAC) is strictly enforced
- ✅ Managers retain control over employee data access
- ✅ All requests are tracked for compliance

---

## 2. Approval Workflow for Sensitive Data

### 2.1 PII Access Request Flow

```
HR User asks for employee contacts
    ↓
System detects PII extraction request
    ↓
Creates approval request (PENDING)
    ↓
HR Manager receives notification
    ↓
Manager reviews purpose & scope
    ↓
APPROVED → Access granted for 1 hour
    ↓
HR user receives employee contact data
    ↓
ALL ACCESS LOGGED FOR AUDIT
```

### 2.2 Request Types Requiring Approval

| Request Type | Example Query | Approval Duration | Scope |
|---|---|---|---|
| `employee_contacts` | "Show employee emails" | 1 hour | HR documents only |
| `grievance_reports` | "List HR incidents" | 1 hour | Restricted HR docs |
| `salary_information` | "Show compensation data" | 2 hours | Executive review only |
| `employee_records` | "Display all employee data" | 1 hour | Manager approved |

### 2.3 Auto-Approved (No Workflow)

**Admin Role:** Always has access (super-user)  
**Exceptions:**
- CEO cannot auto-approve salary data (separate approval)
- Audit log still recorded for admins

---

## 3. Role-Based Access Control Matrix

### Manufacturing (Employee)
- ✅ Manufacturing SOPs & procedures
- ✅ Quality guidelines
- ❌ HR data
- ❌ Employee contacts
- ❌ Salary/compensation

### HR
- ✅ HR policies & procedures
- ✅ Recruitment SOPs
- ⚠️ Employee contacts (requires approval)
- ⚠️ Grievance reports (requires approval)
- ❌ Salary data
- ❌ Executive strategies

### Project Manager (Engineering)
- ✅ Engineering procedures
- ✅ Equipment specs
- ❌ HR data
- ❌ Manufacturing data
- ❌ Executive strategies

### CEO (Executive)
- ✅ All executive reports
- ✅ KPI data
- ✅ Strategic plans
- ✅ Quarterly summaries
- ⚠️ Employee data (audit logged)
- ⚠️ Salary data (approval via board)

### Admin
- ✅ **Everything**
- ✅ All approvals override
- ✅ Full audit trail

---

## 4. PII Protection & Redaction

### 4.1 PII Types Protected

```
- Email addresses:       john.doe@company.com  → jo####@company.com
- Phone numbers:         555-123-4567          → 555-###-####
- SSN:                   123-45-6789           → ***-**-****
- Employee ID:           EMP-12345             → ***-*****
- Names (in context):    Redacted in output
- Addresses:             Partially redacted
```

### 4.2 Redaction Rules

**Default (All roles):** Redact PII in responses

**With Approval (HR/Admin):** 
- Can view unredacted PII
- Still logged & audited
- Limited to 1-hour window
- Requires business purpose

**Automatic Triggers:**
- Any email address detected → PII redaction check
- SSN pattern found → Immediate block (requires approval)
- Phone number extracted → Redaction applied

---

## 5. Guardrails Pipeline

### 5.1 Input Stage (Before LLM)

```
1. Length Check        → Reject overly long prompts
2. Secret Detection    → Block credential patterns
3. Injection Detection → Stop prompt manipulation
4. Destructive Intent  → Prevent harmful queries
5. PII Intent Check    → Flag employee data requests
6. Scope Check         → Verify within department bounds
7. Semantic Risk       → ML-based safety check
```

### 5.2 Output Stage (After LLM)

```
1. PII Redaction       → Mask sensitive data
2. Confidentiality     → Remove restricted doc refs
3. Length Validation   → Ensure reasonable response
4. Tone Check          → Prevent harmful language
```

---

## 6. Approval Workflow API

### 6.1 Request PII Access

```bash
POST /pii-approval/request

{
  "request_type": "employee_contacts",
  "purpose": "Verify correct contact info for annual review process",
  "scope": "HR documents only"
}

Response:
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "PENDING",
  "created_at": "2026-08-19T10:30:00Z",
  "expires_at": "2026-08-19T11:30:00Z",
  "message": "Request submitted to HR manager for approval"
}
```

### 6.2 Get Pending Requests (Manager View)

```bash
GET /pii-approval/pending

Response:
[
  {
    "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "requester_name": "Alice Johnson",
    "request_type": "employee_contacts",
    "purpose": "Annual review contact verification",
    "created_at": "2026-08-19T10:30:00Z",
    "status": "PENDING"
  }
]
```

### 6.3 Approve Request

```bash
POST /pii-approval/{request_id}/approve

{
  "duration_hours": 1,
  "notes": "Approved for annual review cycle"
}

Response:
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "APPROVED",
  "approved_by": "manager@company.com",
  "approved_at": "2026-08-19T10:35:00Z",
  "valid_until": "2026-08-19T11:35:00Z"
}
```

### 6.4 Reject Request

```bash
POST /pii-approval/{request_id}/reject

{
  "reason": "Purpose insufficient for annual review process"
}

Response:
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "REJECTED",
  "rejected_by": "manager@company.com",
  "reason": "Purpose insufficient for annual review process",
  "rejected_at": "2026-08-19T10:35:00Z"
}
```

---

## 7. Audit & Logging

### 7.1 Logged Events

**PII Access Requested:**
```json
{
  "timestamp": "2026-08-19T10:30:00Z",
  "user_id": "e3571501",
  "event": "pii_access_requested",
  "request_id": "f47ac10b",
  "request_type": "employee_contacts",
  "purpose": "Annual review",
  "status": "PENDING"
}
```

**PII Access Approved:**
```json
{
  "timestamp": "2026-08-19T10:35:00Z",
  "approver_id": "e3571502",
  "event": "pii_access_approved",
  "request_id": "f47ac10b",
  "requester_id": "e3571501",
  "valid_until": "2026-08-19T11:35:00Z",
  "duration_hours": 1
}
```

**PII Accessed:**
```json
{
  "timestamp": "2026-08-19T10:45:00Z",
  "user_id": "e3571501",
  "event": "pii_accessed",
  "pii_type": "email_addresses",
  "record_count": 45,
  "scope": "HR documents",
  "approval_id": "f47ac10b",
  "status": "success"
}
```

### 7.2 Audit Report Query

```bash
GET /audit?event_type=pii_access&from=2026-08-01&to=2026-08-31

Returns: All PII access events in CSV/JSON for compliance
```

---

## 8. Escalation & Blocking

### 8.1 Auto-Escalation Triggers

| Condition | Action | Details |
|---|---|---|
| 3+ failed auth attempts | BLOCK | 15-min lockout |
| 5+ guardrail blocks in 2 min | ESCALATE | Review by security |
| Repeated PII requests denied | ALERT | Manager notified |
| Pattern of scope violations | SUSPEND | Pending review |
| High-confidence injection | BLOCK | Immediate deny |

### 8.2 Security Team Review

- Daily escalation review
- Weekly pattern analysis
- Monthly compliance audit
- Quarterly policy review

---

## 9. Exceptions & Emergency Access

### 9.1 Emergency Override

**Only Admin Role:**
```
- Can bypass approval workflow
- Must have documented business reason
- Requires second admin confirmation
- Logged as "emergency_override"
```

**Process:**
```
1. First admin initiates emergency request
2. Second admin receives notification
3. Second admin reviews & approves (or rejects)
4. If approved: Access granted + Full audit trail
5. 24-hour incident review mandatory
```

### 9.2 Exception Requests

**For new roles or data types:**
```
1. Submit to Security & Compliance
2. Review meeting scheduled
3. Risk assessment completed
4. Policy updated (if approved)
5. New rules deployed
```

---

## 10. Compliance & Training

### 10.1 User Training

All HR users must complete:
- ✅ Data privacy training (annual)
- ✅ PII protection guidelines (annual)
- ✅ Approval workflow walkthrough (quarterly)
- ✅ Security incident reporting (annually)

### 10.2 Manager Training

All HR managers must complete:
- ✅ Approval decision framework
- ✅ Privacy risk assessment
- ✅ Audit trail interpretation
- ✅ Incident escalation procedures

### 10.3 Admin Training

All admins must complete:
- ✅ Emergency override procedures
- ✅ Audit log review & analysis
- ✅ Security incident response
- ✅ Compliance reporting

---

## 11. Policy Review & Updates

- **Review Frequency:** Quarterly
- **Update Process:** Security team + Legal + Compliance
- **Approval:** VP of HR + CISO
- **Communication:** Email + training updates
- **Effective Date:** Upon approval

---

## 12. Related Documents

- [Guardrails Architecture](./GUARDRAILS_ARCHITECTURE.md)
- [RBAC Configuration](../config/llm_rbac.yaml)
- [PII Redaction Service](../services/guardrails/pii.py)
- [Audit Logging](../services/audit/)
- [Privacy Policy](../../privacy-policy.md)

---

**Document ID:** POL-2026-COPILOT-001  
**Classification:** Internal - Confidential  
**Last Updated:** 2026-08-19  
**Next Review:** 2026-11-19
