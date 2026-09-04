# Customer Accessorial Rate Sheet — Markup Analysis & Draft Standard

**Question posed:** we pass accessorials through to customers today; should they be marked
up, and what should a standard Customer Accessorial Rate Sheet look like?

**Short answer:** Yes — mark them up, and formalize it. You are already applying an
informal markup inside the quoting engine (~1.3–1.5×), you have at least one accessorial
you are provably *losing money on*, and your own carrier Rate Confirmation loads all the
administrative and collection risk onto Delta while pure pass-through captures none of the
reward. A standard sheet turns an inconsistent, leaky practice into policy.

---

## What the evidence shows

### 1. You already apply a de-facto markup — it's just informal and inconsistent

The DGL Command Center's quote emails carry an **"Accessorial exposure (company guidance)"**
block with bill/pay pairs. Observed values (identical across sampled quotes, i.e. current
company defaults):

| Accessorial | Bill (customer) | Pay (carrier) | Implied markup | Implied margin |
|-------------|----------------:|--------------:|:--------------:|:--------------:|
| Detention   | ~$148 | ~$99  | **1.49×** | 33% |
| Stopoff     | ~$95  | ~$64  | 1.48×     | 33% |
| TONU        | ~$226 | ~$163 | 1.39×     | 28% |
| Layover     | ~$318 | ~$240 | 1.33×     | 25% |
| Lumper      | ~$238 | ~$246 | **0.97×** | **−3% (loss)** |

Two findings jump out:
- There is **already an intended markup** (~1.3–1.5× on most types) — it just lives in a
  quoting tool as guidance, not in a published, enforced rate sheet.
- **Lumper is billed below cost.** The tool itself flags it: *"underbilled company-wide —
  make sure it is passed through."* Every lumper is currently a small loss plus unpaid
  handling time. This is a fix-now item independent of any markup decision.

### 2. Your carrier Rate Confirmation makes pure pass-through a bad deal for Delta

The signed carrier Rate Confirmation (section 6 and Freight Bill terms) puts the entire
burden on Delta while, under pass-through, giving it none of the upside:

- Delta must collect **signed facility proof** and a **revised signed rate con in real
  time**, chase documentation, and adjudicate eligibility — real labor per event.
- Carrier accessorial pay is **contingent on Delta collecting from the customer first**
  ("shall NOT be PAID DETENTION, LAYOVER, DEADHEAD, RE-CONSIGNMENT, or TONU unless & until
  DELTA GROUP LOGISTICS receives full payment from its customer"). Delta carries the
  **collection and dispute risk**.

If the customer bill merely equals carrier pay, Delta does all this work and takes on all
this risk for **$0 margin**. A markup is the compensation for the administrative load and
the collection risk Delta is contractually absorbing.

### 3. The authoritative "actuals" still require McLeod

The numbers above are the quoting engine's *assumed* rates, not a report of what was
actually invoiced. The system of record for **realized** customer accessorial billing is
**McLeod's accounting/AR module** — customer invoices carrying accessorial revenue codes
(e.g. detention, layover, TONU, lumper, stopoff). That is not connected in this session.

**To finalize this sheet with real numbers, connect McLeod AR (or export a McLeod
accessorial-revenue report by revenue code for the trailing 6–12 months).** Then we compare
billed-vs-paid per type, per customer, to see who is already absorbing markup and who is
getting pass-through, and set the standard from data rather than the engine's defaults.

---

## Recommendation

1. **Adopt a standard markup, published as a rate sheet** rather than living as tool
   guidance. Recommended default: **carrier cost + 40%**, with **per-type minimums** so
   small accessorials still cover their handling cost.
2. **Fix lumper immediately:** bill at **cost + a fixed handling fee** (recommend $35–$50)
   at minimum, never below cost.
3. **Split accessorials into two pricing behaviors:**
   - **Time/service-based** (detention, layover, stopoff, TONU) → percentage markup, since
     these carry dispute and adjudication cost.
   - **Third-party pass-through fees** (lumper, tolls, scale) → cost + fixed handling fee,
     since the dollar amount is a receipt, not a negotiation.
4. **Set customer-facing free time equal to or tighter than carrier free time (2h)** so you
   are never paying carrier detention you can't bill the customer.
5. **Make the sheet contractual:** reference it in customer rate agreements so accessorials
   are pre-agreed, not negotiated per event. This is what makes automated billing possible.

---

## Draft Standard Customer Accessorial Rate Sheet (v0 — validate against McLeod AR)

Carrier cost basis is from the signed Rate Confirmation. "Current engine bill" is the
Command Center default. "Recommended customer rate" applies the standard above. **All
recommended figures are provisional pending McLeod actuals.**

| Accessorial | Carrier cost (Rate Con) | Current engine bill | Recommended customer rate | Basis |
|-------------|------------------------|--------------------:|---------------------------|-------|
| **Detention** | $35/h solo · $50/h team, 2h free, caps at layover | $148 | **$49/h solo · $70/h team**, 1.5h free, min $75 | cost +40%, tighter free time |
| **Layover** | $150 solo · $250 team | $318 | **$225 solo · $375 team** | cost +50% (high dispute) |
| **TONU** | $150 solo · $250 team | $226 | **$225 solo · $350 team** | cost +40–50% |
| **Driver assist** | pre-approved only | — | **$150 min, pre-approved, quoted per event** | negotiated, not auto |
| **Stopoff** | (not in Rate Con; carrier-negotiated) | $95 | **cost +40%, $95 min** | matches current engine |
| **Lumper** | receipt (third-party) | $238 *(underbilled)* | **cost + $50 handling, never below cost** | **fix the loss** |
| **Tolls / scale** | receipt (third-party) | — | **cost + $25 handling** | pass-through + handling |

### Notes for whoever owns pricing
- The **team-service** distinction runs through the whole carrier sheet; carry it into the
  customer sheet so team loads bill at the team tier.
- Detention **caps at the layover rate on the carrier side** — decide whether the customer
  side caps the same way or bills detention past the layover threshold as a layover.
- "Recommended" percentages are a starting standard, not a finding from your books.
  **Connect McLeod AR and I will replace this column with rates derived from what you have
  actually billed and collected**, by customer, so the standard reflects reality and
  survives a customer pushback conversation.

---

## How this ties into the automation

The reference engine (`reference-implementation/accessorial_rules.py`) already computes the
**carrier-pay** side of every one of these from load facts, with the eligibility gates and
the manager-only override. Adding the **customer-bill** side is a small extension: the same
per-type rules, a markup table (this sheet) instead of the carrier rate table, and the same
"held until documented" gates. Once McLeod AR is connected, the engine can propose the
customer invoice line *and* the carrier payable from one set of facts — which is the whole
point of the pilot.
