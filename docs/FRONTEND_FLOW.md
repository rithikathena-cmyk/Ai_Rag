# ATHENA AI Frontend Flow Diagram

## User Journey & Component Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ATHENA AI Application Flow                         │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────┐
│   Landing    │  ◄─── Welcome page with "Continue to App" button
│   Page       │       (Architecture, Scenarios, Guardrails, Monitoring tabs)
└──────┬───────┘
       │
       ▼
┌──────────────────┐
│   Login Page     │  ◄─── Demo users: Employee, HR, Manager, CEO, Admin
│  (RBAC Roles)    │
└──────┬───────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Chat Interface (Main App)                    │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐              ┌──────────────────┐             │
│  │   Sidebar    │              │  Main Chat Area  │             │
│  │              │              │                  │             │
│  │ • New Chat   │              │ Welcome Message  │             │
│  │ • History    │              │ Suggested Prompts│             │
│  │ • Show 57+   │              │ Message Input    │             │
│  └──────────────┘              └──────────────────┘             │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    User Sends Message with PII                       │
│  "My email is john.doe@company.com, can you look up my balance?"    │
└─────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  Frontend API Call to Backend                        │
│  POST /chat with message, user_id, conversation_id                 │
└─────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│           Backend Processing (FastAPI + Guardrails)                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  1. INPUT GUARDRAILS                                               │
│     ✓ Jailbreak detection                                          │
│     ✓ PII Detection → REDACT: "My email is jo#####-..."           │
│     ✓ Secret detection                                             │
│     ✓ Scope checking                                               │
│                                                                      │
│  2. RETRIEVAL GUARDRAILS                                           │
│     ✓ Permission filtering (RBAC)                                  │
│     ✓ Document access control                                      │
│                                                                      │
│  3. LLM INFERENCE                                                   │
│     ✓ Process redacted message                                     │
│     ✓ Generate response                                            │
│                                                                      │
│  4. OUTPUT GUARDRAILS                                              │
│     ✓ PII redaction in response                                    │
│     ✓ System prompt leak detection                                 │
│     ✓ Citation verification                                        │
│                                                                      │
│  5. AUDIT LOGGING                                                   │
│     ✓ Log all guardrail decisions                                  │
│     ✓ Track confidence scores                                      │
│     ✓ Store trace steps                                            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│              Backend Returns Response with Trace Data                │
│  {                                                                   │
│    "response": "I'm not sure I understood...",                     │
│    "confidence": "n/a",                                            │
│    "trace": [                                                       │
│      {"check": "pii_detection", "status": "REDACTED"},            │
│      {"check": "jailbreak", "status": "PASSED"},                   │
│      {"check": "permission_filter", "status": "PASSED"}           │
│    ]                                                                │
│  }                                                                   │
└─────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│           Frontend Renders Response + Guardrails Status              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Chat Message Display:                                             │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │ User: "My email is jo#####-..., can you look up balance?"   │ │
│  │                                                              │ │
│  │ ATHENA: "I'm not sure I understood..."                      │ │
│  │                                                              │ │
│  │ 🛡️ Security & Activity · n/a confidence                     │ │
│  │    ✓ PII Detection (REDACTED)                               │ │
│  │    ✓ Jailbreak Check (PASSED)                               │ │
│  │    ✓ Permission Filter (PASSED)                             │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  Sidebar Update:                                                   │
│  "My email is jo#####-..." (redacted title in history)            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│              User Can Explore Guardrails Details                     │
│                                                                      │
│  • Click "Security & Activity" panel                               │
│  • View full trace of each check                                   │
│  • See confidence scores                                           │
│  • Review PII occurrences                                          │
│  • Check audit logs (Admin only)                                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Component Architecture

```
App.tsx (Root)
│
├─── AuthContext (JWT, Role, Permissions)
│
└─── Router
     │
     ├─── LoginPage
     │    ├─── Demo User Selection
     │    └─── Auth API Integration
     │
     ├─── LandingPage
     │    ├─── Navigation (Architecture, Scenarios, Guardrails, Monitoring)
     │    └─── Welcome Messaging
     │
     ├─── ChatPage (Protected)
     │    ├─── Sidebar
     │    │    ├─── New Chat Button
     │    │    ├─── Chat History
     │    │    └─── Workspace Settings
     │    │
     │    ├─── Chat Area
     │    │    ├─── Messages Display
     │    │    ├─── SuggestedPrompts
     │    │    ├─── ModelSelector
     │    │    └─── Message Input
     │    │
     │    └─── Guardrails Panel
     │         ├─── GuardrailsStatus
     │         │    └─── ShieldIcon (Pass/Block)
     │         │
     │         └─── SecurityActivityPanel
     │              ├─── Input Guardrails (Collapsible)
     │              │    ├─── PII Detection
     │              │    ├─── Jailbreak Check
     │              │    ├─── Secret Detection
     │              │    └─── Scope Check
     │              │
     │              ├─── RAG & Access (Always Visible)
     │              │    ├─── Permission Filter
     │              │    ├─── Document Access
     │              │    └─── Citation Check
     │              │
     │              └─── Output Guardrails (Collapsible)
     │                   ├─── PII Redaction
     │                   └─── System Prompt Leak Check
     │
     ├─── DocumentsPage
     │    ├─── Upload Interface
     │    ├─── File List
     │    └─── Access Control (RBAC)
     │
     ├─── AdminPage (Admin Only)
     │    ├─── User Management
     │    ├─── Document Access Control
     │    └─── Audit Logs
     │
     └─── Additional Pages
          ├─── Dashboard
          ├─── Audit Logs
          ├─── Traces
          ├─── Guardrail Policies
          ├─── Policy Copilot
          └─── Settings

```

---

## State Management Flow

```
User Action (Send Message)
    ↓
ChatPage State Update
    ├─── Draft Message
    ├─── Attachments
    └─── Selected Model
    ↓
useMutation (sendChatMessage)
    ├─── Call Backend API
    ├─── Send message + user_id + conversation_id
    └─── Return response + trace data
    ↓
Update Chat State
    ├─── Add user message to thread
    ├─── Add assistant response
    ├─── Update trace/guardrails data
    └─── Update confidence score
    ↓
React Query Cache Update
    ├─── Invalidate conversation
    ├─── Refresh chat history
    └─── Sync sidebar
    ↓
UI Re-render
    ├─── Display new messages
    ├─── Show guardrails status
    ├─── Highlight redacted content
    └─── Update activity panel
```

---

## Guardrails Visibility Flow

```
Message Response Received
    ↓
Extract Trace Data
    ├─── Input guardrails results
    ├─── Retrieval guardrails results
    ├─── Output guardrails results
    └─── Confidence score
    ↓
GuardrailsStatus Component
    ├─── Determine if blocked (icon: 🛡️ alert or ✓ pass)
    ├─── Show response time
    └─── Toggle Security & Activity panel
    ↓
SecurityActivityPanel (Expanded)
    ├─── Collapsible Input Section
    │    ├─── PII Detection → REDACTED
    │    ├─── Jailbreak → PASSED
    │    ├─── Secret → PASSED
    │    └─── Scope Check → PASSED
    │
    ├─── Always-Visible RAG Section
    │    ├─── Permission Filter → PASSED
    │    ├─── Citation Check → PASSED
    │    └─── Document Access → ALLOWED
    │
    └─── Collapsible Output Section
         ├─── PII Redaction → PASSED
         ├─── System Prompt Leak → PASSED
         └─── Response Safety → PASSED
```

---

## Key Features by Role

### **Employee**
- ✓ Chat with guardrails protection
- ✓ Upload personal documents
- ✓ Access manufacturing knowledge base
- ✗ No admin functions

### **HR**
- ✓ All Employee features
- ✓ View employee PII (with audit trail)
- ✓ Access HR documents
- ✗ Cannot modify policies

### **Manager**
- ✓ All Employee features
- ✓ View team documents
- ✓ Department-scoped access
- ✗ No cross-department access

### **CEO/Admin**
- ✓ All features
- ✓ Manage users & roles
- ✓ Set guardrail policies
- ✓ View audit logs
- ✓ Cross-organization access

---

## API Integration Points

| Endpoint | Method | Purpose | Frontend Component |
|----------|--------|---------|-------------------|
| `/auth/login` | POST | Authenticate user | LoginPage |
| `/chat` | POST | Send message & get response | ChatPage |
| `/conversations` | GET | Load chat history | Sidebar |
| `/documents/upload` | POST | Upload files | DocumentsPage |
| `/documents` | GET | List accessible documents | DocumentsPage |
| `/users` | GET | User management (Admin) | AdminPage |
| `/audit/events` | GET | Audit logs (Admin) | AuditLogsPage |
| `/traces` | GET | View guardrail traces | TracesPage |
| `/guardrail-policies` | GET/POST | Manage policies (Admin) | GuardrailPolicyPage |
| `/pii-occurrences` | GET | PII tracking (Admin) | PiiOccurrencesPanel |

---

## Error Handling Flow

```
API Call Fails
    ↓
isBlockedResponse() Check
    ├─── Status: BLOCKED → Show red shield icon
    ├─── Status: ERROR → Show error toast
    └─── Status: TIMEOUT → Show retry option
    ↓
User Sees:
    ├─── Error message in chat
    ├─── Reason for block (if guardrails triggered)
    ├─── Retry button
    └─── Report option (for support)
```

---

**Note:** This flow diagram documents the user journey through ATHENA AI without modifying any homepage content or React components.
