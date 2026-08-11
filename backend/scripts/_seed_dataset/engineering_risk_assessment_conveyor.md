# Engineering Risk Assessment: Line 4 Conveyor Belt System Upgrade

Document ID: ENG-RA-022 | Revision: 2 | Date: 2026-02-10 | Owner: Engineering — Project Manager

## PROJECT SUMMARY

This risk assessment supports the planned upgrade of the Line 4 main conveyor from a fixed-speed
belt drive to a variable-frequency-drive (VFD) controlled system, targeting a 15% throughput
increase and reduced belt wear. Project code: PRJ-2026-014.

## SCOPE OF WORK

Replacement of the existing 3-phase induction motor with a VFD-compatible motor, installation of a
new drive cabinet, retrofit of the belt tensioning system, and integration with the existing PLC
via Modbus TCP. Estimated downtime: one 48-hour weekend outage.

## IDENTIFIED RISKS

1. **Integration risk (High):** The existing PLC firmware (version 4.2) has not been validated
   against the new VFD's Modbus register map. Mitigation: schedule a bench test of the VFD against
   a PLC simulator at least 2 weeks before the outage window.
2. **Schedule risk (Medium):** VFD cabinet lead time from the vendor is currently quoted at 6
   weeks, close to the critical path for the Q2 go-live target. Mitigation: place the order this
   week and request expedited shipping as a contingency.
3. **Safety risk (Medium):** The belt tensioning retrofit requires work inside the guarded zone
   with the conveyor de-energized. Mitigation: full lockout/tagout per EHS-SAFE-005, with a
   dedicated safety observer during all in-guard work.
4. **Throughput regression risk (Low):** Incorrect VFD ramp-rate tuning could reduce throughput
   below current baseline during commissioning. Mitigation: stage commissioning with a 72-hour
   monitored ramp-up period before declaring the line back to full production.
5. **Vendor risk (Low):** This is the first VFD installation from this vendor at this site.
   Mitigation: require an on-site vendor commissioning engineer for the first 8 hours of startup.

## RISK MATRIX SUMMARY

Of the five risks identified, one is rated High, two Medium, and two Low. No risk in this
assessment is rated Critical; project may proceed to detailed design pending closure of the
Integration risk mitigation (PLC/VFD bench test).

## RECOMMENDED NEXT STEPS

1. Confirm VFD cabinet purchase order this week to protect the schedule.
2. Complete the PLC/VFD bench test and document results in the project risk log before the outage
   window is finalized.
3. Submit the lockout/tagout work plan to EHS for review at least 10 business days before the
   outage.

## APPROVALS

This risk assessment requires sign-off from the Engineering Manager and the Plant Safety Officer
before the outage window is scheduled with Production.

## RELATED DOCUMENTS

PRJ-2026-014 Project Charter, EHS-SAFE-005 (Lockout/Tagout Procedure), SOP-MFG-014 (Injection
Molding Startup — referenced for adjacent-line coordination during the outage).
