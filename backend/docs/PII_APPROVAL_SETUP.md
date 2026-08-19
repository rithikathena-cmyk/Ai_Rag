# PII Approval Workflow - Setup Guide

## Overview

This guide sets up the **Approval Workflow** for PII access (Option 4). HR users must request approval from managers before accessing employee contact information.

---

## Features

✅ **Manager Approval Required** - HR requests → Manager reviews → Access granted (1-hour window)  
✅ **Automatic Audit Logging** - All PII access tracked for compliance  
✅ **Time-Limited Access** - Approval expires after 1 hour  
✅ **API Endpoints** - Request, approve, reject PII access requests  
✅ **Rejection Reasons** - Managers provide context when denying requests  

---

## Architecture

```
HR User Query: "Show employee contacts"
    ↓
System detects PII request
    ↓
Creates PIIAccessRequest (PENDING)
    ↓
Sends to approval queue
    ↓
Manager reviews in Admin Dashboard
    ↓
Manager clicks APPROVE (1 hour)
    ↓
Access granted + Audited
    ↓
HR receives unredacted employee data
```

---

## Components

### 1. PII Approval Service
**File:** `backend/app/services/guardrails/pii_approval.py`

Functions:
- `request_pii_access()` - Create approval request
- `check_pii_access()` - Check if user has valid approval
- `approve_pii_request()` - Manager approves request
- `reject_pii_request()` - Manager rejects request
- `get_pending_requests()` - List requests awaiting approval

### 2. Chat Router Integration
**File:** `backend/app/routers/chat.py`

Integrated at line 368-379:
- Detects PII intent in user message
- Checks approval status
- Blocks access if not approved
- Returns approval request ID

### 3. Admin Dashboard
**File:** `frontend-react/src/pages/AdminPage.tsx`

New sections:
- PII Requests queue
- Approval/Rejection buttons
- Audit trail viewer

---

## API Endpoints

### Request PII Access
```bash
POST /chat

{
  "message": "Show employee contacts for HR review",
  "request_type": "employee_contacts"
}

Response:
{
  "status": "PENDING",
  "approval_request_id": "f47ac10b-58cc...",
  "message": "This request requires manager approval"
}
```

### Manager: Get Pending Requests
```bash
GET /admin/pii-requests/pending

Response:
[
  {
    "id": "f47ac10b-58cc...",
    "requester_name": "Alice Johnson",
    "request_type": "employee_contacts",
    "purpose": "Annual review contact verification",
    "scope": "HR documents only",
    "created_at": "2026-08-19T10:30:00Z",
    "expires_in_seconds": 3600
  }
]
```

### Manager: Approve Request
```bash
POST /admin/pii-requests/{request_id}/approve

{
  "duration_hours": 1,
  "approval_notes": "Approved for Q3 annual review"
}

Response:
{
  "status": "APPROVED",
  "approved_at": "2026-08-19T10:35:00Z",
  "valid_until": "2026-08-19T11:35:00Z"
}
```

### Manager: Reject Request
```bash
POST /admin/pii-requests/{request_id}/reject

{
  "reason": "Purpose too vague - please specify which employees"
}

Response:
{
  "status": "REJECTED",
  "rejected_at": "2026-08-19T10:35:00Z"
}
```

---

## Configuration

### 1. Enable PII Approval in YAML
**File:** `backend/config/guardrails.yaml`

```yaml
pii_approval:
  enabled: true
  
  # PII request types that require approval
  request_types:
    employee_contacts:
      allowed_roles: ["hr", "admin"]
      approval_duration_hours: 1
      requires_purpose: true
      
    grievance_reports:
      allowed_roles: ["hr"]
      approval_duration_hours: 1
      requires_purpose: true
      
    salary_information:
      allowed_roles: ["admin", "ceo"]
      approval_duration_hours: 2
      requires_purpose: true

  # Escalation thresholds
  escalation:
    denied_requests_threshold: 3
    denied_requests_window_hours: 2
    auto_escalate_to: ["security@company.com"]
```

### 2. Set Manager Approvers
**File:** `backend/app/core/permissions.py`

Add to HR role:
```python
class Permission(str, Enum):
    APPROVE_PII_ACCESS = "APPROVE_PII_ACCESS"
    VIEW_PII_REQUESTS = "VIEW_PII_REQUESTS"

# Assign to HR managers
ROLE_PERMISSIONS["hr"] = {
    "APPROVE_PII_ACCESS",
    "VIEW_PII_REQUESTS",
    # ... other HR permissions
}
```

---

## Testing

### Test 1: Request PII Access (HR User)

```bash
# Login as HR
curl -X POST http://localhost:8011/auth/demo-login \
  -H "Content-Type: application/json" \
  -d '{"demo_role":"hr"}'

# Request employee contacts
curl -X POST http://localhost:8011/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Show employee contacts for annual review"
  }'

# Expected: PENDING approval request
```

### Test 2: Approve Request (Manager)

```bash
# Login as HR manager
curl -X POST http://localhost:8011/auth/demo-login \
  -H "Content-Type: application/json" \
  -d '{"demo_role":"hr"}'

# Check pending requests
curl -X GET http://localhost:8011/admin/pii-requests/pending \
  -H "Authorization: Bearer <manager_token>"

# Approve request
curl -X POST http://localhost:8011/admin/pii-requests/{REQUEST_ID}/approve \
  -H "Authorization: Bearer <manager_token>" \
  -H "Content-Type: application/json" \
  -d '{"duration_hours": 1}'

# Expected: Approval granted, HR user can now access
```

### Test 3: Audit Trail

```bash
# View PII access audit events
curl -X GET "http://localhost:8011/audit?event_type=pii_*&limit=50" \
  -H "Authorization: Bearer <admin_token>"

# Expected: See all request, approval, access events
```

---

## User Experience Flow

### For HR User:

1. **Ask Question:**
   ```
   "Show me employee contact information for annual review"
   ```

2. **System Response:**
   ```
   "This request requires manager approval. 
    Request ID: f47ac10b-58cc-4372-a567-0e02b2c3d479
    Waiting for review..."
   ```

3. **After Manager Approval (within 1 hour):**
   ```
   "✅ Request approved! Here are the contacts:
   - Jordan Kessler: jordan.kessler@company.com
   - Alan Petrov: alan.petrov@company.com
   ..."
   ```

### For HR Manager:

1. **Dashboard Notification:**
   - "New PII access request from Alice Johnson"
   - "Purpose: Annual review contact verification"
   - "2 buttons: APPROVE | REJECT"

2. **Click APPROVE:**
   - Sets 1-hour window
   - Alice can now run her query
   - Audit logged

3. **Or Click REJECT:**
   - Enter reason
   - Alice notified
   - Audit logged

---

## Compliance

✅ **GDPR:** PII access tracked and auditable  
✅ **CCPA:** Right to know who accessed data  
✅ **SOC 2:** Approval audit trail maintained  
✅ **Internal Policy:** Manager control over sensitive data  

---

## Troubleshooting

### Issue: "Request already processed"
**Cause:** Manager approved/rejected, HR trying again  
**Fix:** Create new request (previous approval expired)

### Issue: "Only HR and Admin can request"
**Cause:** Non-HR role tried to request PII  
**Fix:** Only HR and Admin roles can use approval workflow

### Issue: "Approval expired"
**Cause:** 1-hour window passed  
**Fix:** Request new approval from manager

---

## Next Steps

1. ✅ Deploy pii_approval.py
2. ✅ Update guardrails.yaml with config
3. ✅ Add APPROVE_PII_ACCESS permission
4. ✅ Update AdminPage.tsx with PII requests UI
5. ✅ Run integration tests
6. ✅ Deploy to Vercel
7. ✅ Train HR team on new workflow

---

**Document ID:** SETUP-2026-PII-APPROVAL-001  
**Last Updated:** 2026-08-19
