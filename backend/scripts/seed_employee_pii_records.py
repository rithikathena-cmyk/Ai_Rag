"""One-off seeding script: populates EmployeePIIRecordModel with a small set
of realistic, clearly-synthetic employee records (status="active", real
field values already set) — so the chat-triggered employee-PII approval
workflow (docs/GUARDRAILS_ARCHITECTURE.md §14) has actual data to demo
against, rather than only the empty "pending" placeholder rows
_get_or_create_placeholder() creates on first mention of a new EMP-ID.

Direct DB write via new_session(), not an API call: this model has no CRUD
endpoint at all by design (services/employee_pii/service.py's own docstring
— "no general CRUD UI... this flow is entirely request-driven from a chat
message"), so there is no API path to seed through the way
scripts/seed_department_dataset.py seeds documents through the real upload
endpoint. A direct write is the only option here, not a shortcut around one.

All names/emails/phones are fictitious, same convention the seeded document
corpus already uses (see e.g. mfg_incident_report_line7_stoppage.md's own
"This is a synthetic test record" disclaimer) — EMP001 deliberately reuses
"Diego Marsh," the same name already established as the Line 7 incident
reporter in that document, purely for demo narrative continuity (asking
"who is EMP001" and "who reported the Line 7 stoppage" now point at the
same fictional person) — NOT a claim that this table and that document's own
"Employee ID: STF-MFG-41220" are the same ID scheme; they're deliberately
unrelated systems (this table's EMP-prefixed IDs are what
services/guardrails/pii_intent.py's regex actually matches on).

Usage:

    python scripts/seed_employee_pii_records.py
"""

import sys

sys.path.insert(0, ".")

from app.db.postgres import new_session
from app.models.employee_pii_record import EmployeePIIRecordModel

RECORDS = [
    dict(
        employee_id="EMP001", full_name="Diego Marsh", email="diego.marsh@example.com",
        phone="555-0142", address="42 Riverside Ave, Springfield", department="manufacturing",
    ),
    dict(
        employee_id="EMP002", full_name="Priya Sharma", email="priya.sharma@example.com",
        phone="555-0198", address="118 Maple Street, Springfield", department="hr",
    ),
    dict(
        employee_id="EMP003", full_name="Marcus Chen", email="marcus.chen@example.com",
        phone="555-0173", address="7 Birchwood Court, Springfield", department="engineering",
    ),
    dict(
        employee_id="EMP004", full_name="Angela Whitfield", email="angela.whitfield@example.com",
        phone="555-0110", address="900 Summit Drive, Springfield", department="executive",
    ),
]


def main() -> None:
    db = new_session()
    try:
        created, updated = 0, 0
        for data in RECORDS:
            row = db.query(EmployeePIIRecordModel).filter(
                EmployeePIIRecordModel.employee_id == data["employee_id"]
            ).first()
            if row is None:
                row = EmployeePIIRecordModel(employee_id=data["employee_id"])
                db.add(row)
                created += 1
            else:
                updated += 1
            row.full_name = data["full_name"]
            row.email = data["email"]
            row.phone = data["phone"]
            row.address = data["address"]
            row.department = data["department"]
            row.status = "active"
        db.commit()
        print(f"Seeded {len(RECORDS)} employee_pii_records rows ({created} created, {updated} updated).")
        for data in RECORDS:
            print(f"  {data['employee_id']:8s} {data['full_name']:18s} {data['department']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
