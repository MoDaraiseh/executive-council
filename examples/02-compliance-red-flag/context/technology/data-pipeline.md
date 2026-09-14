# Data pipeline note — EU contact list provenance

- The 280,000-contact EU list was imported into the CRM in 2019, before our consent-tracking fields were added to the schema (consent-tracking went live in 2021).
- There is no reliable per-contact record of current marketing-consent status for this list; the CRM cannot distinguish contacts who have since opted out or never validly opted in under post-2021 consent standards.
- Sending to this list as-is means we cannot technically suppress non-consented contacts at send time.

**CTO read:** this is a real data-compliance gap, not just a process formality — flagging as a regulatory/technical compliance concern per our review checklist.
