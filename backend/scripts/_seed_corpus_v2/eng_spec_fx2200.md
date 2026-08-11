# Engineering Specification: Line 7 Filling Machine — Model FX-2200

**Document ID:** ENG-SPEC-210 | **Filename:** eng_spec_fx2200.md | **Department:** Engineering
**Document Type:** Specification | **Version:** 1.4 | **Effective Date:** 2026-01-12
**Owner:** Engineering Manager | **Access Classification:** Internal

> Vendor contact details below are synthetic test data generated for RAG/PII-redaction testing
> only and do not correspond to a real person or company representative.

## MACHINE OVERVIEW

The FX-2200 is a rotary volumetric filling machine used on Production Line 7 for 2-liter liquid
product containers.

## RATED PERFORMANCE

- **Rated throughput:** 45 units per minute (design maximum); operating target per SOP-MFG-101 is
  42 units per minute to maintain the specified fill tolerance.
- **Fill volume range:** 0.5L to 2.0L, factory-configured for 2.0L ± 0.02L on Line 7.
- **Fill accuracy:** ±0.5% of nominal volume under normal operating conditions.

## ELECTRICAL AND UTILITY REQUIREMENTS

- **Power:** 480V, 60Hz, 3-phase, 45 kVA connected load.
- **Compressed air:** 90 psi minimum, dry and filtered to ISO 8573-1 Class 2.
- **Footprint:** 3.2m x 2.4m, minimum 1.5m clearance on all sides for maintenance access.

## CONSTRUCTION AND MATERIALS

Product-contact surfaces are 316L stainless steel. The main drive gearbox and outfeed conveyor
bearings are the components covered by ENG-MAINT-301's lubrication and replacement schedule.

## CONTROL SYSTEM

The FX-2200 integrates with the plant PLC via Ethernet/IP for line-speed setpoint and jam-detect
signaling. Sensor calibration requirements are defined in ENG-MAINT-301.

## VENDOR AND WARRANTY

Manufactured by Meridian Fill Systems (test vendor name). Standard warranty is 24 months on
mechanical components, 12 months on electronics, from date of installation.

**Vendor Sales Engineer (test data):** Thomas Bergstrom, thomas.bergstrom.test@meridianfill-test.example,
+1 (414) 555-0129. Provided for spare-parts ordering and warranty claims only.

## RELATED DOCUMENTS

ENG-MAINT-301 (Equipment Maintenance Manual), SOP-MFG-101 (Line 7 Operation), WI-QA-101 (Quality
Inspection SOP), ENG-PROC-401 (Engineering Change Procedure — required for any spec deviation).
