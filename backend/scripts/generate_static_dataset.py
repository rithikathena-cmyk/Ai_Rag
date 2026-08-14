"""Static dataset generator for the RAG/RBAC test corpus.

Generates a second, independent 19-document synthetic corpus under
scripts/_generated_dataset/, structured by department into subdirectories,
mirroring the department/classification shape of scripts/_seed_corpus_v2/
(see seed_corpus_v2.py) without touching that corpus at all.

This script does ONLY file generation:
  - no ingestion (no calls to /documents/upload)
  - no embeddings / Qdrant writes
  - no PostgreSQL writes
  - no changes to backend/config/llm_rbac.yaml or RBAC logic

Every document's content is a fixed Python string literal below — there is
no randomness, no Faker, no external API/network call, and no timestamp
generated at run time, so re-running this script produces byte-identical
output every time (idempotent: overwriting existing files with the same
content, never appending or renaming).

Usage:
    python scripts/generate_static_dataset.py
    python scripts/generate_static_dataset.py --check   # validate only, no write
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / "_generated_dataset"

EXPECTED_COUNTS = {"manufacturing": 5, "hr": 5, "engineering": 5, "executive": 4}
VALID_CLASSIFICATIONS = {"internal", "confidential", "restricted"}


@dataclass(frozen=True)
class DocSpec:
    filename: str
    department: str
    classification: str
    body: str  # markdown body, WITHOUT the YAML frontmatter (render() adds it)


# ---------------------------------------------------------------------------
# Document bodies
#
# These describe the same topics as scripts/_seed_corpus_v2/'s 19 files (same
# filenames, same department/classification per the spec), but are original
# text: different document IDs (GEN- prefix), different dates/figures, and
# different synthetic names/contact info, so this is a distinct dataset
# rather than a copy of v2 — useful for RAG tests that need two
# non-identical corpora to distinguish retrieval results from.
# ---------------------------------------------------------------------------

MFG_SOP_PRODUCTION_LINE7 = """
# SOP: Production Line 7 Filling and Packaging Operation

**Document ID:** GEN-SOP-MFG-101 | **Filename:** mfg_sop_production_line7.md | **Department:** Manufacturing
**Document Type:** SOP | **Version:** 3.0 | **Effective Date:** 2026-02-10
**Owner:** Manufacturing Operations Manager | **Access Classification:** Internal

## PURPOSE

This SOP defines the standard operating steps for running Production Line 7, which fills and
packages liquid product into 1.75-liter containers using the FX-2200 filling machine (see
GEN-ENG-SPEC-210 for the machine specification).

## TARGET PERFORMANCE

Line 7's standard rate is **40 units per minute** at full speed, with a target Overall Equipment
Effectiveness (OEE) of **80%**. Any shift averaging below 72% OEE must be logged in the shift
handoff report with a stated root cause.

## STARTUP SEQUENCE

1. Confirm the previous shift's handoff report shows no open quality holds on Line 7.
2. Verify product tank level is above the 15% low-level sensor before starting the fill pump.
3. Run 8 units in manual mode and inspect fill volume against the target of 1.75L ± 0.03L.
4. Once 8 consecutive units pass, switch to automatic mode and ramp to full line speed over 4
   minutes.

## IN-PROCESS CHECKS

- Fill volume is spot-checked every 20 minutes per GEN-WI-QA-101 (In-Process Quality Inspection).
- Seal integrity is checked every 45 minutes using the pull-test fixture; minimum acceptable seal
  strength is 10N (see GEN-WI-QA-101 for the full test procedure).
- Line speed deviations greater than 8% from the 40 units/minute target for more than 10 minutes
  must be reported to the Shift Lead.

## SHUTDOWN AND HANDOFF

At end of shift, complete the Line 7 handoff report including current OEE, any quality holds, and
remaining product tank level. See GEN-SOP-MFG-104 (Machine Shutdown Procedure) for full shutdown
steps if the line is stopping for more than 3 hours.

## RELATED DOCUMENTS

GEN-ENG-SPEC-210 (FX-2200 Machine Specification), GEN-WI-QA-101 (Quality Inspection SOP),
GEN-SOP-MFG-104 (Machine Shutdown Procedure), GEN-SOP-MFG-108 (Shift Attendance and Tardiness
Reporting).
"""

MFG_SOP_MACHINE_SHUTDOWN = """
# SOP: Emergency and Planned Machine Shutdown Procedure

**Document ID:** GEN-SOP-MFG-104 | **Filename:** mfg_sop_machine_shutdown.md | **Department:** Manufacturing
**Document Type:** SOP | **Version:** 1.0 | **Effective Date:** 2026-02-01
**Owner:** Manufacturing Operations Manager | **Access Classification:** Internal

## PURPOSE

This procedure covers both emergency (E-STOP) and planned shutdown of any production line machine
in the Manufacturing department, including Line 7's FX-2200 filling machine.

## EMERGENCY SHUTDOWN (E-STOP)

1. Press the nearest E-STOP button — do not attempt to reach a farther one.
2. Evacuate personnel from the immediate machine zone.
3. Notify the Shift Lead and Maintenance immediately.
4. **Resetting an E-STOP requires sign-off from a Level 2 Maintenance Technician or higher** —
   operators may not reset an E-STOP themselves under any circumstance.
5. Allow a minimum **10-minute cooldown** before any inspection of hot components (fill nozzles,
   seal heads) following an E-STOP event.

## PLANNED SHUTDOWN

1. Complete the current production run to the nearest natural stopping point (do not stop
   mid-cycle unless instructed).
2. Reduce line speed by 40% for the final 3 minutes before full stop to avoid product hang-up in
   the filling manifold.
3. Purge and flush the fill lines per the product-specific flush chart posted at the machine.
4. De-energize per lockout/tagout procedure GEN-EHS-SAFE-005 if maintenance access is required.

## RESTART AFTER SHUTDOWN

Restart follows the standard startup sequence in GEN-SOP-MFG-101. Any restart following an E-STOP
event additionally requires the Level 2 Maintenance Technician's sign-off to be logged in the CMMS
before production resumes.

## ESCALATION

Maintenance hotline: extension 3382. For a shutdown lasting more than 3 hours, notify the Plant
Manager so it can be reflected in the daily production report.

## RELATED DOCUMENTS

GEN-SOP-MFG-101 (Production Line 7 Operation), GEN-EHS-SAFE-005 (Lockout/Tagout Procedure),
GEN-ENG-MAINT-301 (Equipment Maintenance Manual — FX-2200).
"""

MFG_SOP_QUALITY_INSPECTION = """
# SOP: In-Process Quality Inspection — Line 7 Packaging

**Document ID:** GEN-WI-QA-101 | **Filename:** mfg_sop_quality_inspection.md | **Department:** Manufacturing
**Document Type:** SOP | **Version:** 2.0 | **Effective Date:** 2026-02-14
**Owner:** Quality Assurance Manager | **Access Classification:** Internal

## PURPOSE

This SOP defines in-process inspection steps for Line 7 packaging to ensure fill volume, seal
integrity, and label placement meet specification before cases are palletized.

## SAMPLING PLAN

Acceptance Quality Limit (AQL) for Line 7 is **1.0**, sampled per the standard AQL table for lot
sizes up to 2,500 units. First-article inspection requires 100% check of the first 15 units after
any changeover.

## FILL VOLUME CHECK

Target fill volume is 1.75L with a tolerance of ±0.03L, checked every 20 minutes using the
calibrated volumetric fixture. Any unit outside tolerance triggers a re-check of the prior 20
minutes of production before release.

## SEAL INTEGRITY CHECK

Seal strength is checked every 45 minutes using the pull-test fixture. **Minimum acceptable seal
strength is 10N.** A failing seal test requires immediate notification to the Shift Lead and a
hold on all product since the last passing check.

## LABEL AND CODE VERIFICATION

Verify lot code, best-by date, and label placement on 4 consecutive units at the start of each
hour. A misprinted or missing lot code is an automatic reject — do not release for override without
Quality Engineer approval.

## NONCONFORMANCE HANDLING

Any lot exceeding the AQL 1.0 defect rate is placed on Quality Hold, tagged, and logged in the NCR
system within the shift. See GEN-SOP-MFG-104 if the finding requires a machine shutdown for
investigation.

## RELATED DOCUMENTS

GEN-SOP-MFG-101 (Line 7 Operation), GEN-SOP-MFG-104 (Machine Shutdown Procedure), GEN-ENG-SPEC-210
(FX-2200 Machine Specification).
"""

MFG_INCIDENT_REPORT_LINE7_STOPPAGE = """
# Incident Report: Line 7 Packaging Line Unplanned Stoppage

**Document ID:** GEN-INC-MFG-2026-014 | **Filename:** mfg_incident_report_line7_stoppage.md | **Department:** Manufacturing
**Document Type:** Incident Report | **Version:** 1.0 | **Effective Date:** 2026-05-08
**Owner:** Manufacturing Operations Manager | **Access Classification:** Confidential

> This is a synthetic test record. All names, contact details, and identifiers below are
> fictitious and generated for RAG/RBAC/PII-redaction testing only.

## INCIDENT SUMMARY

On 2026-05-06 at 10:47, Line 7 experienced an unplanned stoppage lasting **2 hours 15 minutes**.
Root cause was identified as a failed infeed proximity sensor that produced a false jam-detect
trip, causing an automatic line stop.

## REPORTED BY

- **Name:** Diego Marsh (test data)
- **Employee ID:** STF-MFG-41220
- **Role:** Shift Lead, Line 7
- **Contact Phone:** (206) 555-0138
- **Contact Email:** diego.marsh.test@harborline-test.internal

## TIMELINE

- 10:47 — Line 7 auto-stops on jam-detect sensor; Shift Lead Diego Marsh notified Maintenance.
- 10:58 — Maintenance Technician confirms a faulty infeed proximity sensor, no physical jam
  present.
- 11:20 — Replacement sensor retrieved from spares crib; lockout/tagout applied per
  GEN-EHS-SAFE-005.
- 12:45 — Sensor replacement and alignment complete; line restarted per GEN-SOP-MFG-101 startup
  sequence.
- 13:02 — Line back to full speed (40 units/minute); stoppage closed. No injuries reported.

## ROOT CAUSE AND CORRECTIVE ACTION

Root cause: the infeed proximity sensor's alignment had drifted out of tolerance and was not
covered by any existing quarterly inspection item — the sensor was not on the current preventive
maintenance schedule (see GEN-ENG-PM-2026 for the schedule now updated to include this component).

Corrective action: Engineering has added infeed proximity sensor alignment checks to the quarterly
preventive maintenance checklist for all filling lines, effective the next PM cycle.

## PRODUCTION IMPACT

Estimated production loss: approximately 5,400 units at standard line speed. This stoppage is
reflected in the Q2 2026 Plant Performance Report's OEE figure for Line 7.

## RELATED DOCUMENTS

GEN-SOP-MFG-101 (Line 7 Operation), GEN-SOP-MFG-104 (Machine Shutdown Procedure), GEN-ENG-PM-2026
(Preventive Maintenance Schedule), GEN-EHS-SAFE-005 (Lockout/Tagout Procedure).
"""

MFG_PROCEDURE_SHIFT_ATTENDANCE = """
# Procedure: Shift Attendance and Tardiness Reporting — Manufacturing

**Document ID:** GEN-SOP-MFG-108 | **Filename:** mfg_procedure_shift_attendance.md | **Department:** Manufacturing
**Document Type:** Procedure | **Version:** 1.0 | **Effective Date:** 2026-01-20
**Owner:** Manufacturing Operations Manager | **Access Classification:** Internal

## PURPOSE

This procedure defines how Manufacturing shift supervisors track and report attendance and
tardiness for hourly production employees, applying the enterprise Employee Attendance Policy
(GEN-HR-POL-101) at the shift-floor level.

## CLOCK-IN AND GRACE PERIOD

Employees clock in at the badge terminal at each line entrance. An **8-minute grace period** is
applied after the scheduled shift start before a clock-in is marked tardy.

## TARDINESS ESCALATION

- **1st tardy in a rolling 45 days:** Verbal reminder from the Shift Lead, logged in the shift
  notebook only.
- **2nd tardy in the same 45-day window:** Written note in the employee's attendance file.
- **3rd tardy in the same 45-day window:** A documented coaching conversation with the Shift Lead
  and a copy filed with HR per GEN-HR-POL-101's progressive discipline steps.

## UNEXCUSED ABSENCE

An unplanned absence with no call-in before shift start is logged as unexcused. Per
GEN-HR-POL-101, **4 or more unexcused absences within a rolling 75-day period** trigger an
automatic HR case file and manager notification.

## SHIFT COVERAGE

When an employee calls in absent, the Shift Lead first checks the on-call coverage list before
requesting a voluntary shift extension from the outgoing shift. Coverage gaps that cannot be
filled must be noted in the shift handoff report and may affect that day's Line 7 OEE figure.

## REPORTING TO HR

Manufacturing shift attendance data feeds the enterprise Attendance KPI Summary (see
GEN-EXEC-KPI-101) on a weekly basis via the HR portal's automated sync — supervisors do not need
to submit a separate attendance report to HR unless escalating a specific case.

## RELATED DOCUMENTS

GEN-HR-POL-101 (Employee Attendance Policy), GEN-EXEC-KPI-101 (Attendance KPI Summary —
Enterprise), GEN-SOP-MFG-101 (Production Line 7 Operation).
"""

HR_POLICY_ATTENDANCE = """
# Employee Attendance Policy

**Document ID:** GEN-HR-POL-101 | **Filename:** hr_policy_attendance.md | **Department:** HR
**Document Type:** Policy | **Version:** 2.0 | **Effective Date:** 2026-01-01
**Owner:** VP of Human Resources | **Access Classification:** Internal

## PURPOSE

This policy establishes enterprise-wide attendance and tardiness standards. Department-specific
procedures (e.g. Manufacturing's GEN-SOP-MFG-108) implement this policy at the floor/shift level
but may not set a lower bar than what's defined here.

## SCOPE

Applies to all full-time and part-time employees across Manufacturing, Engineering, HR, and
corporate/Executive functions. Contractors follow their staffing agency's attendance terms.

## TARDINESS

A grace period of up to 12 minutes may be applied at department discretion (Manufacturing applies
an 8-minute grace period per GEN-SOP-MFG-108). Beyond the grace period, an arrival is logged as
tardy in the HR portal.

## UNEXCUSED ABSENCE THRESHOLD

**Four or more unexcused absences within a rolling 75-day period** trigger an automatic
notification to the employee's manager and the opening of an HR case file, enterprise-wide. This
threshold is the same figure department procedures must reference (see GEN-SOP-MFG-108's
tardiness escalation section).

## PROGRESSIVE DISCIPLINE FOR ATTENDANCE

1. Verbal reminder (documented locally by the manager).
2. Written warning, copy filed with HR.
3. Documented coaching conversation, copy filed with HR — this is the step at which HR becomes
   formally involved regardless of department.
4. Final written warning with a defined improvement period.
5. Termination for continued non-compliance after the improvement period, subject to HR Business
   Partner review.

## EXCUSED ABSENCE CATEGORIES

Approved PTO, sick leave, bereavement leave, and parental leave (see GEN-HR-POL-104 Leave
Management Policy) are excused and do not count toward the unexcused-absence threshold above.

## REPORTING AND KPI ROLLUP

Attendance data from all departments rolls up weekly into the enterprise Attendance KPI Summary
(GEN-EXEC-KPI-101), which reports the enterprise-wide unexcused absence rate to executive
leadership. Individual case details are never included in that summary — only aggregate rates.

## RELATED DOCUMENTS

GEN-SOP-MFG-108 (Shift Attendance and Tardiness Reporting — Manufacturing), GEN-HR-POL-104 (Leave
Management Policy), GEN-EXEC-KPI-101 (Attendance KPI Summary — Enterprise).
"""

HR_BENEFITS_GUIDE = """
# Employee Benefits Guide

**Document ID:** GEN-HR-POL-102 | **Filename:** hr_benefits_guide.md | **Department:** HR
**Document Type:** Policy | **Version:** 1.3 | **Effective Date:** 2026-01-01
**Owner:** VP of Human Resources | **Access Classification:** Internal

## PURPOSE

This guide summarizes the core benefits available to full-time employees. It is a summary only —
the plan documents on file with HR govern in the event of any conflict.

## HEALTH INSURANCE

Medical, dental, and vision coverage begins on the first of the month following a **45-day
waiting period** from date of hire. The company covers 75% of the employee-only premium and 55%
of dependent premiums.

## RETIREMENT PLAN

The company 401(k) plan matches employee contributions **up to 3.5% of base salary**, with
immediate vesting on the match. Enrollment is available at any time; auto-enrollment at 2% begins
after 45 days of employment unless the employee opts out.

## PAID TIME OFF

See GEN-HR-POL-104 (Leave Management Policy) for PTO accrual, sick leave, and parental leave
detail. This guide covers only the benefits enrollment side of PTO, not accrual mechanics.

## DISABILITY AND LIFE INSURANCE

Short-term disability pays 65% of base salary for up to 10 weeks; long-term disability begins at
week 11. Basic life insurance of 1.5x annual salary is provided at no cost; supplemental coverage
up to 4x salary is available at employee expense.

## EMPLOYEE ASSISTANCE PROGRAM (EAP)

A confidential EAP is available to all employees and their household members at no cost, covering
up to 5 counseling sessions per issue per year. EAP usage is never reported to managers or HR in
any identifiable form.

## BENEFITS ENROLLMENT WINDOWS

Open enrollment runs each October for coverage effective the following January 1. Qualifying life
events (marriage, birth/adoption, loss of other coverage) open a 30-day special enrollment window.

## QUESTIONS

Benefits questions can be directed to the HR Benefits mailbox at hr-benefits@harborline-test.internal.

## RELATED DOCUMENTS

GEN-HR-POL-104 (Leave Management Policy), GEN-HR-POL-101 (Employee Attendance Policy).
"""

HR_SOP_RECRUITMENT = """
# Recruitment SOP

**Document ID:** GEN-HR-SOP-201 | **Filename:** hr_sop_recruitment.md | **Department:** HR
**Document Type:** SOP | **Version:** 1.0 | **Effective Date:** 2026-02-01
**Owner:** Talent Acquisition Manager | **Access Classification:** Confidential

> This is a synthetic test record. The sample candidate data below is fictitious and generated for
> RAG/RBAC/PII-redaction testing only — it does not describe a real person.

## PURPOSE

This SOP defines the standard steps for requisition approval, candidate screening, offer, and
onboarding handoff for all open roles.

## REQUISITION APPROVAL

A hiring manager submits a requisition through the HR portal; approval requires sign-off from the
department head and HR Business Partner before the role is posted externally.

## SCREENING AND INTERVIEW STAGES

1. Resume screen by Talent Acquisition against the role's must-have criteria.
2. Phone screen (25 minutes) — logged in the applicant tracking system (ATS).
3. Panel interview (2–3 rounds depending on level).
4. Reference check — minimum 2 professional references contacted.
5. Offer approval — Talent Acquisition Manager and department head sign off on final compensation.

## SAMPLE CANDIDATE RECORD (TEST DATA ONLY)

The following is a fully synthetic sample record used to validate that candidate PII fields are
captured and later redacted correctly in any downstream system, including this RAG system:

- **Candidate Name:** Owen Baptiste (test data — fictitious)
- **Test SSN-format value:** 456-78-9123 (NOT a real Social Security Number — format only, for
  redaction testing)
- **Email:** owen.baptiste.candidate.test@harborline-test.internal
- **Phone:** (206) 555-0164
- **Position Applied For:** Production Engineer I, Requisition REQ-2026-0142
- **Assigned Employee ID upon hire (test data):** STF-ENG-41590

## OFFER AND ONBOARDING HANDOFF

Once an offer is accepted, Talent Acquisition transfers the candidate's PII fields (name, contact
info, SSN, background check results) to the HR onboarding system and purges the ATS copy of the
SSN field within 3 business days per data retention policy.

## BACKGROUND CHECKS

Background checks are initiated only after a verbal offer acceptance and are conducted by a
third-party vendor; results are stored in the onboarding system, not in the ATS or this document
repository.

## RELATED DOCUMENTS

GEN-HR-POL-101 (Employee Attendance Policy), GEN-HR-POL-102 (Employee Benefits Guide) — provided
to new hires at onboarding.
"""

HR_POLICY_LEAVE_MANAGEMENT = """
# Leave Management Policy

**Document ID:** GEN-HR-POL-104 | **Filename:** hr_policy_leave_management.md | **Department:** HR
**Document Type:** Policy | **Version:** 2.0 | **Effective Date:** 2026-01-01
**Owner:** VP of Human Resources | **Access Classification:** Internal

## PURPOSE

This policy defines PTO accrual, sick leave, bereavement leave, and parental/family leave, and how
each interacts with the Employee Attendance Policy (GEN-HR-POL-101).

## PTO ACCRUAL

Full-time employees accrue 1.5 days of PTO per completed month of service, capped at 18 days per
calendar year, prorated for mid-month hires.

## SICK LEAVE

7 days per calendar year, non-carryover. A doctor's note is required beyond 2 consecutive sick
days.

## FAMILY AND MEDICAL LEAVE

Eligible employees receive **10 weeks of job-protected leave** for a qualifying family or medical
event, consistent with the company's FMLA-equivalent policy. The first 4 weeks of parental leave
are paid at 100% base salary; the remainder may be covered by short-term disability at 65% pay
(see GEN-HR-POL-102 Benefits Guide).

## BEREAVEMENT LEAVE

Up to 4 paid days for immediate family, 1 paid day for extended family, per the Employee Handbook
Appendix A.

## REQUESTING LEAVE

Planned leave requests go through the HR portal at least 7 business days in advance. Emergency
leave should be reported to the manager as soon as possible, with the portal entry completed
within 2 business days of return.

## INTERACTION WITH ATTENDANCE POLICY

Approved leave under this policy is always excused and never counts toward GEN-HR-POL-101's
unexcused-absence threshold. A leave request submitted after an absence has already been logged as
unexcused may be retroactively reclassified by HR once approved.

## APPROVAL AUTHORITY

Direct managers approve routine PTO and sick leave. Family/medical leave and bereavement leave are
processed directly by HR, with the manager notified but not required to approve.

## RELATED DOCUMENTS

GEN-HR-POL-101 (Employee Attendance Policy), GEN-HR-POL-102 (Employee Benefits Guide).
"""

HR_INCIDENT_REPORT_GRIEVANCE = """
# Incident Report: Workplace Grievance Investigation Summary

**Document ID:** GEN-INC-HR-2026-009 | **Filename:** hr_incident_report_grievance.md | **Department:** HR
**Document Type:** Incident Report | **Version:** 1.0 | **Effective Date:** 2026-04-06
**Owner:** HR Compliance Manager | **Access Classification:** Restricted

> This is a synthetic test record. All names, contact details, and identifiers below are
> fictitious and generated for RAG/RBAC/PII-redaction testing only — this does not describe a real
> investigation or real individuals.

## SUMMARY

A workplace conduct concern was reported through the confidential ethics hotline on 2026-03-14 and
investigated by HR Compliance per GEN-HR-POL-103 (Workplace Conduct and Harassment Reporting
Policy). The case was closed on 2026-04-05 — **22 days** from intake to closure.

## PARTIES (TEST DATA)

- **Complainant:** Nadia Volkov (test data), Employee ID STF-OPS-40877, contact
  nadia.volkov.test@harborline-test.internal, (206) 555-0121.
- **Respondent:** Samuel Fitzgerald (test data), Employee ID STF-OPS-40655, contact
  samuel.fitzgerald.test@harborline-test.internal, (206) 555-0133.
- **Lead Investigator:** Grace Petrosyan (test data), HR Compliance Manager, contact
  grace.petrosyan.test@harborline-test.internal, (206) 555-0147.

## INVESTIGATION STEPS

1. Intake interview with the complainant (2026-03-16).
2. Notice provided to the respondent and initial response gathered (2026-03-19).
3. Three witness interviews conducted (2026-03-21 through 2026-03-24).
4. Review of relevant email and badge-access records for the period in question.
5. Findings drafted and reviewed by HR Compliance leadership (2026-04-02).

## FINDING

**No policy violation was substantiated.** The investigation found the underlying conduct did not
meet the policy's definition of harassment, though a communication-style coaching recommendation
was made to the respondent's manager as a developmental note, unrelated to any disciplinary
action.

## OUTCOME AND FOLLOW-UP

Both parties were notified of the outcome in accordance with policy. No retaliation concerns have
been raised as of this report's effective date; HR Compliance will conduct a standard 60-day
check-in with the complainant per policy.

## CONFIDENTIALITY NOTE

This report is restricted to HR Compliance and the VP of HR. It is not shared with the parties'
management chain beyond the developmental coaching note referenced above, and is not shared with
any other department under any circumstance.

## RELATED DOCUMENTS

GEN-HR-POL-103 (Workplace Conduct and Harassment Reporting Policy).
"""

ENG_MANUAL_EQUIPMENT_MAINTENANCE = """
# Equipment Maintenance Manual — Line 7 Filling Machine FX-2200

**Document ID:** GEN-ENG-MAINT-301 | **Filename:** eng_manual_equipment_maintenance.md | **Department:** Engineering
**Document Type:** Maintenance Manual | **Version:** 1.5 | **Effective Date:** 2026-02-05
**Owner:** Engineering Manager | **Access Classification:** Internal

## PURPOSE

This manual covers routine maintenance procedures for the FX-2200 filling machine on Production
Line 7 (see GEN-ENG-SPEC-210 for the full machine specification).

## LUBRICATION SCHEDULE

Main drive gearbox: check oil level biweekly, full oil change every 2,500 operating hours using
ISO VG 150 gear oil. Fill-head actuator rails: grease every 400 operating hours with the
lithium-complex grease specified on the machine's lubrication chart.

## BEARING REPLACEMENT

The outfeed conveyor bearing and the main drive shaft bearings are rated for **3,500 operating
hours**. Replace proactively at this interval rather than waiting for failure.

## FILL VALVE MAINTENANCE

Inspect fill valve seals monthly for wear; replace at the first sign of drip-through during the
seal integrity check (see GEN-WI-QA-101). Fill valve seal kits are stocked in the Line 7 spares
crib, part number FX2200-SEAL-KIT-07.

## SENSOR CALIBRATION

The jam-detect sensor, infeed proximity sensor, and volumetric fill sensor must be calibrated
quarterly against the certified reference standard. Calibration records are logged in the CMMS
against the machine's asset tag — see incident report GEN-INC-MFG-2026-014, in which a drifted
infeed proximity sensor caused a 2-hour-15-minute line stoppage, which is why this sensor is now
included in the quarterly calibration checklist.

## TROUBLESHOOTING QUICK REFERENCE

- Repeated jam-detect false trips: check infeed proximity sensor alignment first (most common
  cause).
- Fill volume drifting high: check fill valve seal wear.
- Line speed unable to reach 40 units/minute: check main drive gearbox oil level and drive belt
  tension.

## SAFETY

All maintenance requiring guard removal follows lockout/tagout per GEN-EHS-SAFE-005. Hot-component
work (fill nozzles, seal heads) requires the 10-minute cooldown specified in GEN-SOP-MFG-104.

## RELATED DOCUMENTS

GEN-ENG-SPEC-210 (FX-2200 Machine Specification), GEN-ENG-PM-2026 (Preventive Maintenance
Schedule), GEN-SOP-MFG-104 (Machine Shutdown Procedure), GEN-INC-MFG-2026-014 (Line 7 Stoppage
Incident Report).
"""

ENG_PROCEDURE_ENGINEERING_CHANGE = """
# Engineering Change Procedure

**Document ID:** GEN-ENG-PROC-401 | **Filename:** eng_procedure_engineering_change.md | **Department:** Engineering
**Document Type:** Procedure | **Version:** 1.0 | **Effective Date:** 2026-01-25
**Owner:** Engineering Manager | **Access Classification:** Internal

## PURPOSE

This procedure governs how a change to a machine specification, drawing, or process parameter is
requested, reviewed, and released across Manufacturing and Engineering.

## ENGINEERING CHANGE REQUEST (ECR)

Any employee may submit an ECR through the engineering change system, describing the proposed
change and the reason (quality issue, cost reduction, safety improvement, or capability upgrade).

## REVIEW AND APPROVAL

1. Engineering reviews technical feasibility within 4 business days.
2. If feasible, the ECR becomes an Engineering Change Order (ECO) and is routed for approval.
3. **Approval requires sign-off from both the Engineering Manager and the Quality Manager**,
   completed within **4 business days** of ECO creation.
4. Changes affecting safety systems additionally require EHS review before release.

## IMPLEMENTATION

Once approved, the ECO is scheduled with Manufacturing for implementation, typically during a
planned shutdown window (see GEN-SOP-MFG-104) to minimize production impact. The affected SOP,
specification, or drawing is updated and re-issued with an incremented version number.

## EMERGENCY CHANGES

A change needed to address an active safety or quality issue may be implemented under an
Emergency ECO with verbal approval from the Engineering Manager, followed by full documentation
within 3 business days after the fact.

## TRAINING AND ROLLOUT

Manufacturing supervisors are responsible for briefing affected shifts on any change before it
takes effect on the floor, and for confirming the updated document is posted at the workstation.

## RECORD KEEPING

All ECRs and ECOs are retained in the engineering change system for the life of the affected
equipment plus 5 years, matching the quality record retention requirement in GEN-WI-QA-101.

## RELATED DOCUMENTS

GEN-ENG-SPEC-210 (FX-2200 Machine Specification), GEN-ENG-MAINT-301 (Equipment Maintenance
Manual), GEN-WI-QA-101 (Quality Inspection SOP), GEN-SOP-MFG-104 (Machine Shutdown Procedure).
"""

ENG_SPEC_FX2200 = """
# Engineering Specification: Line 7 Filling Machine — Model FX-2200

**Document ID:** GEN-ENG-SPEC-210 | **Filename:** eng_spec_fx2200.md | **Department:** Engineering
**Document Type:** Specification | **Version:** 1.0 | **Effective Date:** 2026-01-30
**Owner:** Engineering Manager | **Access Classification:** Internal

> Vendor contact details below are synthetic test data generated for RAG/PII-redaction testing
> only and do not correspond to a real person or company representative.

## MACHINE OVERVIEW

The FX-2200 is a rotary volumetric filling machine used on Production Line 7 for 1.75-liter liquid
product containers.

## RATED PERFORMANCE

- **Rated throughput:** 44 units per minute (design maximum); operating target per
  GEN-SOP-MFG-101 is 40 units per minute to maintain the specified fill tolerance.
- **Fill volume range:** 0.4L to 1.8L, factory-configured for 1.75L ± 0.03L on Line 7.
- **Fill accuracy:** ±0.6% of nominal volume under normal operating conditions.

## ELECTRICAL AND UTILITY REQUIREMENTS

- **Power:** 460V, 60Hz, 3-phase, 40 kVA connected load.
- **Compressed air:** 85 psi minimum, dry and filtered to ISO 8573-1 Class 2.
- **Footprint:** 3.0m x 2.2m, minimum 1.2m clearance on all sides for maintenance access.

## CONSTRUCTION AND MATERIALS

Product-contact surfaces are 316L stainless steel. The main drive gearbox and outfeed conveyor
bearings are the components covered by GEN-ENG-MAINT-301's lubrication and replacement schedule.

## CONTROL SYSTEM

The FX-2200 integrates with the plant PLC via Ethernet/IP for line-speed setpoint and jam-detect
signaling. Sensor calibration requirements are defined in GEN-ENG-MAINT-301.

## VENDOR AND WARRANTY

Manufactured by Castellan Fill Technologies (test vendor name). Standard warranty is 20 months on
mechanical components, 10 months on electronics, from date of installation.

**Vendor Sales Engineer (test data):** Priyanka Deshmukh, priyanka.deshmukh.test@castellanfill-test.example,
+1 (206) 555-0171. Provided for spare-parts ordering and warranty claims only.

## RELATED DOCUMENTS

GEN-ENG-MAINT-301 (Equipment Maintenance Manual), GEN-SOP-MFG-101 (Line 7 Operation),
GEN-WI-QA-101 (Quality Inspection SOP), GEN-ENG-PROC-401 (Engineering Change Procedure — required
for any spec deviation).
"""

ENG_SCHEDULE_PREVENTIVE_MAINTENANCE = """
# Preventive Maintenance Schedule — Q1–Q4 2026

**Document ID:** GEN-ENG-PM-2026 | **Filename:** eng_schedule_preventive_maintenance.md | **Department:** Engineering
**Document Type:** Maintenance Schedule | **Version:** 1.0 | **Effective Date:** 2026-03-01
**Owner:** Engineering Manager | **Access Classification:** Internal

## PURPOSE

This schedule defines the quarterly preventive maintenance (PM) windows for all filling and
packaging lines, updated following the corrective action from incident report
GEN-INC-MFG-2026-014.

## PM WINDOW

Each line receives a standard **6-hour PM window** per quarter, scheduled during a planned
production stop to minimize output impact.

## LINE 7 PM DATES (2026)

- Q1: 2026-01-22 (completed)
- Q2: 2026-04-19 (completed)
- Q3: **2026-07-20**
- Q4: 2026-10-19

## LINE 7 PM CHECKLIST HIGHLIGHTS

1. Infeed proximity sensor alignment check (added to this checklist following
   GEN-INC-MFG-2026-014 — previously this sensor was not on the quarterly checklist).
2. Outfeed conveyor bearing and main drive shaft bearing inspection against the 3,500-operating-
   hour limit.
3. Main drive gearbox oil level check and quarterly top-off.
4. Fill valve seal inspection across all fill heads.
5. Jam-detect and volumetric fill sensor calibration verification.
6. Fill-head actuator rail greasing per GEN-ENG-MAINT-301.

## OTHER LINES

Lines 2, 3, 5, 6, and 9 follow the same 6-hour quarterly PM structure with checklists specific to
their installed equipment, maintained separately by the Engineering PM team.

## SCHEDULING COORDINATION

PM windows are coordinated with Manufacturing at least 3 weeks in advance and reflected in the
weekly production plan so shift staffing can be adjusted accordingly.

## RELATED DOCUMENTS

GEN-ENG-MAINT-301 (Equipment Maintenance Manual), GEN-INC-MFG-2026-014 (Line 7 Stoppage Incident
Report), GEN-ENG-SPEC-210 (FX-2200 Machine Specification).
"""

ENG_INCIDENT_REPORT_HYDRAULIC_LEAK = """
# Incident Report: Hydraulic Leak — Line 5 Conveyor Motor

**Document ID:** GEN-INC-ENG-2026-005 | **Filename:** eng_incident_report_hydraulic_leak.md | **Department:** Engineering
**Document Type:** Incident Report | **Version:** 1.0 | **Effective Date:** 2026-04-24
**Owner:** Engineering Manager | **Access Classification:** Confidential

> This is a synthetic test record. All names, contact details, and identifiers below are
> fictitious and generated for RAG/RBAC/PII-redaction testing only.

## INCIDENT SUMMARY

On 2026-04-22 at 08:40, a hydraulic fluid leak was discovered at the Line 5 conveyor drive motor
during a routine walk-through, three days after that line's scheduled PM window. Approximately
**1.2 gallons** of hydraulic fluid leaked before the line was stopped and the leak contained
within **10 minutes**. No injuries occurred.

## REPORTED BY

- **Name:** Wei-Lin Tran (test data)
- **Employee ID:** STF-ENG-40912
- **Role:** Mechanical Engineer, Manufacturing Engineering
- **Contact Phone:** (206) 555-0149
- **Contact Email:** wei-lin.tran.test@harborline-test.internal

## RESPONSE ACTIONS

1. Line 5 stopped via planned shutdown per GEN-SOP-MFG-104 (not an E-STOP, since no immediate
   hazard to personnel was present).
2. Spill contained with absorbent booms per the facility spill response kit.
3. Hydraulic line fitting identified as the leak source — a worn crimped fitting, not a hose
   failure.
4. Fitting replaced and system pressure-tested before Line 5 was returned to service the same day.

## ENVIRONMENTAL AND SAFETY NOTES

The spill was fully contained within the floor drain berm and did not reach any storm drain. The
facility's EHS coordinator was notified per standard spill-reporting requirements; no external
regulatory reporting threshold was met (spill volume was below the facility's reportable quantity
for hydraulic fluid).

## ROOT CAUSE

The crimped fitting had exceeded its typical service interval without being on the current
preventive maintenance checklist for Line 5. Engineering is reviewing whether other lines have
similar fittings that should be added to their respective PM checklists (see GEN-ENG-PM-2026).

## CORRECTIVE ACTION

Engineering will evaluate all crimped hydraulic fittings across Lines 2–9 for inclusion in the
next preventive maintenance schedule revision.

## RELATED DOCUMENTS

GEN-SOP-MFG-104 (Machine Shutdown Procedure), GEN-ENG-PM-2026 (Preventive Maintenance Schedule),
GEN-INC-MFG-2026-014 (Line 7 Stoppage Incident Report — similar root-cause pattern of a component
missing from the PM checklist).
"""

EXEC_REPORT_PLANT_PERFORMANCE = """
# Plant Performance Report — Q2 2026

**Document ID:** GEN-EXEC-RPT-201 | **Filename:** exec_report_plant_performance.md | **Department:** Executive
**Document Type:** Report | **Version:** 1.0 | **Effective Date:** 2026-06-30
**Owner:** Plant Manager | **Access Classification:** Internal

## OVERVIEW

This report summarizes plant-wide manufacturing performance for Q2 2026 across all production
lines.

## OVERALL EQUIPMENT EFFECTIVENESS

Enterprise-wide OEE for Q2 2026 was **77.6%**, below the 80% target used by individual lines (e.g.
Line 7's target per GEN-SOP-MFG-101). The gap is attributed primarily to the Line 7 unplanned
stoppage in May (see GEN-INC-MFG-2026-014, 2 hours 15 minutes, root cause a drifted infeed
proximity sensor) and the Line 5 hydraulic leak event (GEN-INC-ENG-2026-005).

## SAFETY PERFORMANCE

The Q2 safety incident rate was **0.6 recordable incidents per 100 employees**, within the annual
target range. Neither the Line 7 stoppage nor the Line 5 hydraulic leak resulted in any injuries.

## QUALITY PERFORMANCE

Customer-reported quality escapes remained low across all lines, consistent with the AQL 1.0
sampling standard applied on Line 7 (GEN-WI-QA-101) and comparable standards on other lines.

## MAINTENANCE PROGRAM UPDATE

Following the Q2 incidents, Engineering updated the preventive maintenance schedule to add infeed
proximity sensor alignment checks to Line 7's quarterly checklist (GEN-ENG-PM-2026) and is
reviewing crimped hydraulic fittings plant-wide.

## WORKFORCE NOTE

Manufacturing attendance metrics for Q2 are reported separately in the Attendance KPI Summary
(GEN-EXEC-KPI-101); this report does not duplicate that detail.

## OUTLOOK

Barring further unplanned stoppages, Engineering and Manufacturing project OEE recovery to the
80% target range by Q3 2026, supported by the expanded preventive maintenance checklist.

## RELATED DOCUMENTS

GEN-INC-MFG-2026-014, GEN-INC-ENG-2026-005, GEN-ENG-PM-2026, GEN-EXEC-KPI-101 (Attendance KPI
Summary), GEN-EXEC-SUM-202 (Quarterly Operations Summary — Q2 2026).
"""

EXEC_SUMMARY_QUARTERLY_OPERATIONS = """
# Quarterly Operations Summary — Q2 2026

**Document ID:** GEN-EXEC-SUM-202 | **Filename:** exec_summary_quarterly_operations.md | **Department:** Executive
**Document Type:** Report | **Version:** 1.0 | **Effective Date:** 2026-06-30
**Owner:** Chief Operating Officer | **Access Classification:** Internal

## OVERVIEW

This summary consolidates production, quality, and workforce trends for Q2 2026 for executive
review.

## PRODUCTION VOLUME

Total production volume across all lines was up **5.4% quarter-over-quarter**, driven by steady
demand and the Line 7 process refinements introduced in GEN-SOP-MFG-101's current revision.

## OPERATIONAL HIGHLIGHTS

- Enterprise OEE: 77.6% (see GEN-EXEC-RPT-201 Plant Performance Report for full detail).
- Two notable incidents this quarter (Line 7 stoppage, Line 5 hydraulic leak) with no injuries;
  both closed with corrective actions in the preventive maintenance schedule.
- Recruitment activity remained steady; see GEN-HR-SOP-201 for the current recruitment process.

## COST AND EFFICIENCY

Maintenance labor efficiency held steady this quarter. A capital request for expanded
predictive-sensor coverage is under review by the finance committee, referenced in the Strategic
Manufacturing Plan (GEN-EXEC-PLAN-301).

## WORKFORCE

Headcount and attrition trends are reported through HR channels; attendance specifically is
tracked in the Attendance KPI Summary (GEN-EXEC-KPI-101), which is the authoritative source for
enterprise attendance figures — this summary does not restate that detail.

## RISK NOTES

No material new enterprise risks were identified this quarter beyond the ongoing preventive
maintenance program expansion tracked in GEN-ENG-PM-2026 and the ordinary course of the HR
grievance process (case volumes remain within normal range; case-level detail is restricted to HR
Compliance).

## OUTLOOK

Management expects continued volume growth into Q3, contingent on the preventive maintenance
program closing the OEE gap identified in the Plant Performance Report.

## RELATED DOCUMENTS

GEN-EXEC-RPT-201 (Plant Performance Report), GEN-EXEC-KPI-101 (Attendance KPI Summary),
GEN-EXEC-PLAN-301 (Strategic Manufacturing Plan 2026–2029).
"""

EXEC_KPI_ATTENDANCE = """
# Attendance KPI Summary — Enterprise

**Document ID:** GEN-EXEC-KPI-101 | **Filename:** exec_kpi_attendance.md | **Department:** Executive
**Document Type:** Report | **Version:** 1.0 | **Effective Date:** 2026-06-30
**Owner:** VP of Human Resources | **Access Classification:** Internal

## PURPOSE

This KPI summary reports enterprise-wide attendance performance to executive leadership, rolled up
from department-level attendance tracking (e.g. Manufacturing's GEN-SOP-MFG-108) under the
enterprise Employee Attendance Policy (GEN-HR-POL-101).

## ENTERPRISE UNEXCUSED ABSENCE RATE

The enterprise-wide unexcused absence rate for Q2 2026 was **2.2%**, down from **2.6%** in the
prior quarter — an improvement executive leadership attributes in part to the coaching-conversation
escalation step formalized in GEN-HR-POL-101 and GEN-SOP-MFG-108.

## DEPARTMENT TREND (AGGREGATE ONLY)

Manufacturing, as the largest hourly workforce, continues to represent the majority of tracked
attendance events; Engineering and HR/corporate attendance rates remain consistently below the
enterprise average. **This summary reports aggregate rates only — no individual case detail,
employee name, or department-specific case count is included at this level**, consistent with
GEN-HR-POL-101's reporting-and-KPI-rollup section.

## TREND DRIVERS

1. The 3rd-tardy coaching-conversation step (GEN-SOP-MFG-108) appears to be reducing repeat
   tardiness before it escalates to an unexcused-absence pattern.
2. No material change in leave-related exclusions (approved leave under GEN-HR-POL-104 is
   excluded from this KPI by definition).

## FORWARD LOOK

HR expects the unexcused absence rate to remain in the 2.0%–2.4% range absent a significant
workforce or policy change; this KPI is reviewed quarterly alongside the Plant Performance Report
and Quarterly Operations Summary.

## DISTRIBUTION AND CONFIDENTIALITY

This KPI summary is distributed to executive leadership only, at the aggregate level shown above.
Individual attendance case files remain with HR and, where department-specific, with the
employee's manager — this document is not a substitute for either and contains no individually
identifiable attendance data.

## RELATED DOCUMENTS

GEN-HR-POL-101 (Employee Attendance Policy), GEN-SOP-MFG-108 (Shift Attendance and Tardiness
Reporting — Manufacturing), GEN-EXEC-RPT-201 (Plant Performance Report).
"""

EXEC_STRATEGIC_MANUFACTURING_PLAN = """
# Strategic Manufacturing Plan 2026–2029

**Document ID:** GEN-EXEC-PLAN-301 | **Filename:** exec_strategic_manufacturing_plan.md | **Department:** Executive
**Document Type:** Strategic Plan | **Version:** 1.0 | **Effective Date:** 2026-07-01
**Owner:** Chief Operating Officer | **Access Classification:** Restricted

## PURPOSE

This plan sets the three-year strategic direction for manufacturing capital investment, capacity,
and operational excellence, for board and executive review.

## STRATEGIC OBJECTIVES

1. Raise enterprise OEE from the current 77.6% (Q2 2026, see GEN-EXEC-RPT-201) to a target of
   **83% by 2029**.
2. Expand the infeed-sensor predictive-monitoring pilot from Line 7 to all filling and packaging
   lines.
3. Reduce unplanned-downtime incidents (see GEN-INC-MFG-2026-014 and GEN-INC-ENG-2026-005 as the
   Q2 2026 baseline examples) by 40% by 2028 through the expanded preventive maintenance program
   (GEN-ENG-PM-2026).

## CAPITAL PLAN

A **$3.6 million** capital request is planned for FY2028 to fund predictive-sensor rollout across
Lines 2–9, contingent on board finance committee approval following the Line 7 pilot results
referenced in prior engineering reporting.

## WORKFORCE STRATEGY

Attendance and retention trends (see GEN-EXEC-KPI-101) will continue to be monitored as an input
to workforce planning, particularly for skilled maintenance technician roles whose availability
gates the pace of the preventive-maintenance expansion.

## RISK CONSIDERATIONS

Execution risk centers on equipment lead times and vendor concentration for the predictive
sensor platform (single-sourced as of this plan's effective date), and on maintaining current
safety and quality performance while capital projects are underway.

## GOVERNANCE

Progress against this plan is reviewed quarterly alongside the Plant Performance Report and
Quarterly Operations Summary, with a formal annual refresh each July.

## CONFIDENTIALITY

This plan contains forward-looking capital and strategic information and is restricted to
executive leadership and the board; it is not distributed to department-level management outside
executive briefings.

## RELATED DOCUMENTS

GEN-EXEC-RPT-201 (Plant Performance Report), GEN-EXEC-SUM-202 (Quarterly Operations Summary),
GEN-EXEC-KPI-101 (Attendance KPI Summary), GEN-ENG-PM-2026 (Preventive Maintenance Schedule).
"""

DOCUMENTS: list[DocSpec] = [
    # Manufacturing (5)
    DocSpec("mfg_sop_production_line7.md", "manufacturing", "internal", MFG_SOP_PRODUCTION_LINE7),
    DocSpec("mfg_sop_machine_shutdown.md", "manufacturing", "internal", MFG_SOP_MACHINE_SHUTDOWN),
    DocSpec("mfg_sop_quality_inspection.md", "manufacturing", "internal", MFG_SOP_QUALITY_INSPECTION),
    DocSpec(
        "mfg_incident_report_line7_stoppage.md",
        "manufacturing",
        "confidential",
        MFG_INCIDENT_REPORT_LINE7_STOPPAGE,
    ),
    DocSpec(
        "mfg_procedure_shift_attendance.md", "manufacturing", "internal", MFG_PROCEDURE_SHIFT_ATTENDANCE
    ),
    # HR (5)
    DocSpec("hr_policy_attendance.md", "hr", "internal", HR_POLICY_ATTENDANCE),
    DocSpec("hr_benefits_guide.md", "hr", "internal", HR_BENEFITS_GUIDE),
    DocSpec("hr_sop_recruitment.md", "hr", "confidential", HR_SOP_RECRUITMENT),
    DocSpec("hr_policy_leave_management.md", "hr", "internal", HR_POLICY_LEAVE_MANAGEMENT),
    DocSpec("hr_incident_report_grievance.md", "hr", "restricted", HR_INCIDENT_REPORT_GRIEVANCE),
    # Engineering (5)
    DocSpec(
        "eng_manual_equipment_maintenance.md",
        "engineering",
        "internal",
        ENG_MANUAL_EQUIPMENT_MAINTENANCE,
    ),
    DocSpec(
        "eng_procedure_engineering_change.md",
        "engineering",
        "internal",
        ENG_PROCEDURE_ENGINEERING_CHANGE,
    ),
    DocSpec("eng_spec_fx2200.md", "engineering", "internal", ENG_SPEC_FX2200),
    DocSpec(
        "eng_schedule_preventive_maintenance.md",
        "engineering",
        "internal",
        ENG_SCHEDULE_PREVENTIVE_MAINTENANCE,
    ),
    DocSpec(
        "eng_incident_report_hydraulic_leak.md",
        "engineering",
        "confidential",
        ENG_INCIDENT_REPORT_HYDRAULIC_LEAK,
    ),
    # Executive (4)
    DocSpec("exec_report_plant_performance.md", "executive", "internal", EXEC_REPORT_PLANT_PERFORMANCE),
    DocSpec(
        "exec_summary_quarterly_operations.md",
        "executive",
        "internal",
        EXEC_SUMMARY_QUARTERLY_OPERATIONS,
    ),
    DocSpec("exec_kpi_attendance.md", "executive", "internal", EXEC_KPI_ATTENDANCE),
    DocSpec(
        "exec_strategic_manufacturing_plan.md",
        "executive",
        "restricted",
        EXEC_STRATEGIC_MANUFACTURING_PLAN,
    ),
]


def render(spec: DocSpec) -> str:
    """YAML-style frontmatter (department + security_classification) followed by the doc body."""
    frontmatter = f"---\ndepartment: {spec.department}\nsecurity_classification: {spec.classification}\n---\n\n"
    return frontmatter + spec.body.strip() + "\n"


def generate(output_dir: Path = OUTPUT_DIR) -> list[Path]:
    """Writes every DOCUMENTS entry under output_dir/<department>/<filename>.

    Deterministic content + fixed paths make this idempotent: re-running just
    overwrites each file with the same bytes, never creating duplicates.
    """
    written = []
    for spec in DOCUMENTS:
        dept_dir = output_dir / spec.department
        dept_dir.mkdir(parents=True, exist_ok=True)
        path = dept_dir / spec.filename
        path.write_text(render(spec), encoding="utf-8", newline="\n")
        written.append(path)
    return written


def validate(output_dir: Path = OUTPUT_DIR) -> list[str]:
    """Returns a list of validation error strings; empty list means the generated
    dataset satisfies every requirement in the task spec (exact counts, exact
    filenames, required metadata present, no duplicates)."""
    errors: list[str] = []

    if not output_dir.exists():
        return [f"Output directory does not exist: {output_dir}"]

    all_paths = sorted(output_dir.glob("*/*.md"))
    seen_filenames: dict[str, Path] = {}
    for path in all_paths:
        if path.name in seen_filenames:
            errors.append(
                f"Duplicate filename: {path.name} appears at both "
                f"{seen_filenames[path.name]} and {path}"
            )
        else:
            seen_filenames[path.name] = path

    if len(all_paths) != 19:
        errors.append(f"Expected exactly 19 generated documents, found {len(all_paths)}")

    counts: dict[str, int] = {dept: 0 for dept in EXPECTED_COUNTS}
    for path in all_paths:
        dept = path.parent.name
        counts[dept] = counts.get(dept, 0) + 1
    for dept, expected in EXPECTED_COUNTS.items():
        actual = counts.get(dept, 0)
        if actual != expected:
            errors.append(f"Department '{dept}': expected {expected} documents, found {actual}")

    for spec in DOCUMENTS:
        path = output_dir / spec.department / spec.filename
        if not path.exists():
            errors.append(f"Missing expected file: {path.relative_to(output_dir)}")
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            errors.append(f"{spec.filename}: missing YAML frontmatter delimiter")
            continue
        end = text.find("\n---\n", 4)
        frontmatter = text[4:end] if end != -1 else ""
        if "department:" not in frontmatter:
            errors.append(f"{spec.filename}: missing 'department' metadata")
        if "security_classification:" not in frontmatter:
            errors.append(f"{spec.filename}: missing 'security_classification' metadata")
        if spec.classification not in VALID_CLASSIFICATIONS:
            errors.append(f"{spec.filename}: unknown classification '{spec.classification}'")

    return errors


def print_tree(output_dir: Path = OUTPUT_DIR) -> None:
    # Plain ASCII (not box-drawing unicode) so this prints cleanly on a
    # default Windows console codepage (cp1252) too.
    print(output_dir.name + "/")
    for dept in sorted(EXPECTED_COUNTS):
        dept_dir = output_dir / dept
        print(f"+-- {dept}/")
        files = sorted(dept_dir.glob("*.md")) if dept_dir.exists() else []
        for i, f in enumerate(files):
            branch = "`--" if i == len(files) - 1 else "|--"
            print(f"|   {branch} {f.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Validate an already-generated dataset without writing."
    )
    args = parser.parse_args()

    if not args.check:
        written = generate()
        print(f"Generated {len(written)} files under {OUTPUT_DIR}")

    errors = validate()
    if errors:
        print("\nVALIDATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\nVALIDATION PASSED:")
    print("  - exactly 19 documents generated")
    for dept, expected in EXPECTED_COUNTS.items():
        print(f"  - {dept} == {expected}")
    print("  - every expected filename exists")
    print("  - every file contains department metadata")
    print("  - every file contains security_classification metadata")
    print("  - no duplicate filenames")

    print("\nGenerated directory tree:")
    print_tree()

    return 0


if __name__ == "__main__":
    sys.exit(main())
