# Recruitment SOP

**Document ID:** HR-SOP-201 | **Filename:** hr_sop_recruitment.md | **Department:** HR
**Document Type:** SOP | **Version:** 1.6 | **Effective Date:** 2026-01-15
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
2. Phone screen (30 minutes) — logged in the applicant tracking system (ATS).
3. Panel interview (2–3 rounds depending on level).
4. Reference check — minimum 2 professional references contacted.
5. Offer approval — Talent Acquisition Manager and department head sign off on final compensation.

## SAMPLE CANDIDATE RECORD (TEST DATA ONLY)

The following is a fully synthetic sample record used to validate that candidate PII fields are
captured and later redacted correctly in any downstream system, including this RAG system:

- **Candidate Name:** Priya Anand (test data — fictitious)
- **Test SSN-format value:** 987-65-4321 (NOT a real Social Security Number — format only, for
  redaction testing)
- **Email:** priya.anand.candidate.test@examplecorp.internal
- **Phone:** (312) 555-0142
- **Position Applied For:** Manufacturing Engineer II, Requisition REQ-2026-0088
- **Assigned Employee ID upon hire (test data):** EMP-ENG-30591

## OFFER AND ONBOARDING HANDOFF

Once an offer is accepted, Talent Acquisition transfers the candidate's PII fields (name, contact
info, SSN, background check results) to the HR onboarding system and purges the ATS copy of the
SSN field within 5 business days per data retention policy.

## BACKGROUND CHECKS

Background checks are initiated only after a verbal offer acceptance and are conducted by a
third-party vendor; results are stored in the onboarding system, not in the ATS or this document
repository.

## RELATED DOCUMENTS

HR-POL-101 (Employee Attendance Policy), HR-POL-102 (Employee Benefits Guide) — provided to new
hires at onboarding.
