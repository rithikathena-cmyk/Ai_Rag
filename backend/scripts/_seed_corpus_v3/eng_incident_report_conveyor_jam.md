# Incident Report: CV-350 Conveyor Jam and Belt Damage

**Document ID:** GEN-INC-ENG-2026-009 | **Filename:** eng_incident_report_conveyor_jam.md | **Department:** Engineering
**Document Type:** Incident Report | **Version:** 1.0 | **Effective Date:** 2026-06-19
**Owner:** Engineering Manager | **Access Classification:** Confidential

> This is a synthetic test record. All names, contact details, and identifiers below are
> fictitious and generated for RAG/RBAC/PII-redaction testing only.

## INCIDENT SUMMARY

On 2026-06-18 at 14:20, the CV-350 conveyor (ENG-SPEC-CV350) jammed at the transfer curve, causing
a case to wedge under the belt and tear roughly 30cm of the belt edge before the line's photo-eye
sensor triggered an automatic stop.

## TIMELINE

- 14:20 — Photo-eye triggers automatic stop; Line 7 operator notifies Maintenance.
- 14:32 — Maintenance Technician Marcus Chen confirms belt damage at the transfer curve, isolates
  the conveyor per EHS-SAFE-005 before beginning clearance.
- 15:05 — Jammed case removed; belt damage assessed as requiring full replacement, not a patch.
- 16:40 — Spare belt (per ENG-SPEC-CV350's minimum-stock requirement) installed and tracking
  verified.
- 17:10 — Line 7 back to full speed; total downtime 4 hours 50 minutes.

## ROOT CAUSE

Product buildup on an idler roller near the transfer curve (a known failure mode per
ENG-SPEC-CV350) had gone uncleared for approximately two shifts, allowing enough buildup to deflect
a case off-track into the jam point.

## CORRECTIVE ACTION

The weekly cleaning step was reclassified to a per-shift check on the Line 7 shift-end checklist,
effective immediately. Maintenance flagged the spare-belt inventory for replenishment given this
event consumed the single spare kept on hand.

## REPORTED BY

- **Name:** Marcus Chen (test data)
- **Employee ID:** ENG-2201
- **Role:** Maintenance Technician, Engineering
- **Contact Phone:** [REDACTED_PHONE]
- **Contact Email:** [REDACTED_EMAIL]

## PRODUCTION IMPACT

Estimated production loss: approximately 3,200 units at standard line speed, reflected in the next
Plant Performance Report's OEE figure for Line 7.

## RELATED DOCUMENTS

ENG-SPEC-CV350 (Conveyor System Specification), EHS-SAFE-005 (Lockout/Tagout Procedure), SOP-MFG-101
(Production Line 7 Operation).
