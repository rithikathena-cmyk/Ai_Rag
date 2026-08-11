# Technical Report: CNC Machine Predictive Maintenance Program — Pilot Results

Document ID: ENG-TR-031 | Revision: 1 | Date: 2026-02-20 | Owner: Engineering — Project Manager

## EXECUTIVE SUMMARY

This report summarizes the results of a 6-month pilot deploying vibration and spindle-temperature
sensors on 8 CNC machining centers to enable predictive maintenance, replacing the prior
time-based preventive maintenance schedule for the pilot group.

## BACKGROUND

The previous maintenance approach serviced spindles and ball screws on a fixed 2,000-hour
interval regardless of actual wear. Analysis of 18 months of maintenance records showed 40% of
services found no meaningful wear, while two unplanned spindle failures occurred between
scheduled services, each causing more than 24 hours of unplanned downtime.

## PILOT DESIGN

Eight CNC machining centers on Line 7 were fitted with tri-axial vibration sensors on the spindle
housing and a thermocouple on the spindle bearing. Data was streamed to the existing SCADA
historian at 1-minute intervals and analyzed with a vendor-supplied anomaly-detection model.

## RESULTS

1. **Failure prediction:** The system flagged 3 developing bearing faults during the pilot period,
   each confirmed on teardown; all three were caught at least 5 days before an estimated failure
   point, allowing maintenance to be scheduled during planned downtime.
2. **False positive rate:** 2 alerts were investigated and found to be sensor mounting issues
   rather than genuine machine faults — both were resolved by re-torquing the sensor bracket.
3. **Maintenance labor:** Unnecessary preventive services on the pilot group dropped by
   approximately 35% compared to the fixed-interval baseline for the same period.
4. **Downtime:** Zero unplanned spindle-related downtime events occurred on the pilot group during
   the 6-month window, compared to two events on the non-pilot fleet of comparable age.

## COST ANALYSIS

Sensor and platform cost for the 8-machine pilot was approximately $46,000, with an estimated
maintenance-labor savings of $18,000 over the 6-month pilot and avoided-downtime value estimated
separately by Finance in the Q1 enterprise performance summary.

## RECOMMENDATION

Engineering recommends expanding the predictive maintenance program to all 22 CNC machining
centers across Lines 6, 7, and 8 in a phased rollout over the next two quarters, subject to
capital approval. A full technical rollout plan and updated cost model will be submitted as a
separate capital request.

## LIMITATIONS

The pilot's sample size (8 machines, 6 months) is too small to produce a statistically robust
failure-rate reduction estimate; results should be treated as directional evidence supporting a
larger rollout, not a final ROI figure.

## RELATED DOCUMENTS

Line 7 Maintenance Records (2024–2026), Q1 Enterprise Performance Summary (Finance), Capital
Request Template ENG-CAP-002.
