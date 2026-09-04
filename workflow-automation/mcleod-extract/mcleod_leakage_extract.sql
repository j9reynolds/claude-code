/* ============================================================================
   McLeod LME — Accessorial Leakage Extract  (READ-ONLY)
   Target: server DB02, database lme_1720  (McLeod LoadMaster Enterprise, SQL Server)

   Run READ-ONLY (SELECT only). Delivered, billed loads in the trailing 365 days.
   Tables are fully qualified as [lme_1720].[dbo].[...] so it runs from any DB context
   in SSMS without USE lme_1720. (Or run `USE [lme_1720];` once and drop the prefixes.)

   Design: TWO result sets, both runnable as-is. Nobody has to guess an accessorial
   code list — QUERY B returns every itemized charge with its real code, and the
   classification/leakage math is done on the analysis side from that.

   Core table/column names (orders, movement, stop, customer, payee) were CONFIRMED
   against this live instance via the dgl-mcp connector. The only thing not yet
   confirmed is the CARRIER-side charge table (Query C) — the discovery snippet finds it.

   Export each query to CSV:  loads.csv, othercharges.csv, (optional) carriercharges.csv
   ============================================================================ */


/* ---------------------------------------------------------------------------
   QUERY A — LOADS  ->  loads.csv
   One row per delivered load: header, carrier + pay, rate-con state, and
   stop-derived dwell / on-time (real appointment vs actual times).
   --------------------------------------------------------------------------- */
SELECT
    o.id                                         AS pro_number,
    CONVERT(varchar(10), o.bill_date, 120)       AS delivered_date,
    o.customer_id,
    cust.name                                    AS customer,
    o.curr_movement_id,
    mv.override_payee_id,
    pay.name                                     AS carrier,
    o.freight_charge                             AS linehaul_rate,
    o.otherchargetotal,                          -- lump; the split is in Query B
    o.total_charge,
    mv.override_pay_amt                          AS carrier_total_pay,
    mv.rate_confirmation_status,
    mv.rate_confirmation_sent_date,
    o.equipment_type_id,                         -- team-service is not stored; proxy only
    st.first_check_in                            AS stop_check_in,
    st.last_check_out                            AS stop_check_out,
    st.max_dwell_minutes,                        -- worst single-stop dwell -> detention basis
    st.any_late_arrival,                         -- 1 = missed an appointment window
    st.stop_count
FROM        [lme_1720].[dbo].[orders]   o
LEFT JOIN   [lme_1720].[dbo].[movement] mv   ON mv.id   = o.curr_movement_id
LEFT JOIN   [lme_1720].[dbo].[customer] cust ON cust.id = o.customer_id
LEFT JOIN   [lme_1720].[dbo].[payee]    pay  ON pay.id  = mv.override_payee_id
OUTER APPLY (
    SELECT  MIN(s.actual_arrival)   AS first_check_in,
            MAX(s.actual_departure) AS last_check_out,
            MAX(DATEDIFF(minute, s.actual_arrival, s.actual_departure)) AS max_dwell_minutes,
            MAX(CASE WHEN s.sched_arrive_late IS NOT NULL
                      AND s.actual_arrival > s.sched_arrive_late THEN 1 ELSE 0 END) AS any_late_arrival,
            COUNT(*) AS stop_count
    FROM    [lme_1720].[dbo].[stop] s
    WHERE   s.movement_id = o.curr_movement_id
      AND   s.actual_arrival IS NOT NULL
      AND   s.actual_departure IS NOT NULL
) st
WHERE   o.status = 'D'                                       -- delivered
  AND   o.bill_date >= DATEADD(day, -365, CAST(GETDATE() AS date))
ORDER BY o.bill_date;


/* ---------------------------------------------------------------------------
   QUERY B — ITEMIZED CHARGES (customer bill + carrier pay/deduct)  ->  othercharges.csv
   other_charge holds BOTH sides, distinguished by bill_type (customer_id populated
   on the bill side, driver_id on the carrier/pay side). This single query therefore
   covers customer billing AND carrier accessorials/deductions — Query C is not needed
   (broke_drs_ex_pay is empty at DGL). Raw rows returned; classified on the analysis side.
   --------------------------------------------------------------------------- */
SELECT
    c.order_id,
    c.charge_id,                 -- accessorial/fuel/etc. code (dictionary in Query D)
    c.descr,
    c.bill_type,                 -- direction: customer bill vs carrier pay/deduct
    c.customer_id,               -- populated on the customer-billed side
    c.driver_id,                 -- populated on the carrier/driver-pay side
    c.amount,
    c.rate,
    c.units,
    c.stop_id
FROM        [lme_1720].[dbo].[other_charge] c
JOIN        [lme_1720].[dbo].[orders] o ON o.id = c.order_id
WHERE       o.status = 'D'
  AND       o.bill_date >= DATEADD(day, -365, CAST(GETDATE() AS date));


/* ---------------------------------------------------------------------------
   QUERY C — NOT NEEDED. broke_drs_ex_pay is empty at DGL; carrier accessorials
   and deductions live in other_charge (Query B), split by bill_type. If asset
   (DFS company-driver) extra pay is ever needed, dbo.driver_extra_pay has the
   same shape (order_id, movement_id, payee_id, deduct_code_id, amount) — ask.
   --------------------------------------------------------------------------- */


/* ---------------------------------------------------------------------------
   QUERY D — CHARGE-CODE DICTIONARY  ->  chargecodes.csv   (tiny; run once)
   Lets the analysis classify Query B/C codes precisely (accessorial vs fuel etc.)
   instead of guessing. is_fuel_surcharge flags fuel so it isn't counted as an
   accessorial.
   --------------------------------------------------------------------------- */
SELECT id AS charge_id, descr, is_fuel_surcharge, glid
FROM   [lme_1720].[dbo].[charge_code];


/* ---------------------------------------------------------------------------
   SANITY (optional) — how often is the rate con actually recorded?
   Confirms the control-gap finding (0197341 had it NULL).
   --------------------------------------------------------------------------- */
-- SELECT CASE WHEN rate_confirmation_sent_date IS NULL THEN 'null' ELSE 'set' END AS rc,
--        COUNT(*) FROM [lme_1720].[dbo].[movement]
--  GROUP BY CASE WHEN rate_confirmation_sent_date IS NULL THEN 'null' ELSE 'set' END;
