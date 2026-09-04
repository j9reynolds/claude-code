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
   QUERY B — CUSTOMER accessorial charges  ->  othercharges.csv
   other_charge = the CUSTOMER billing side (carrier pay is separate, Query C).
   Every line with its real code; classified on the analysis side via Query D.
   --------------------------------------------------------------------------- */
SELECT
    c.order_id,
    c.charge_id,                 -- accessorial/fuel/etc. code (dictionary in Query D)
    c.descr,
    c.bill_type,                 -- keep to filter out any non-billed lines
    c.amount,
    c.rate,
    c.units,
    c.stop_id
FROM        [lme_1720].[dbo].[other_charge] c
JOIN        [lme_1720].[dbo].[orders] o ON o.id = c.order_id
WHERE       o.status = 'D'
  AND       o.bill_date >= DATEADD(day, -365, CAST(GETDATE() AS date));


/* ---------------------------------------------------------------------------
   QUERY C — CARRIER accessorial pay  ->  carrierpay.csv
   Confirmed table: driver_extra_pay (this is where carrier accessorial pay lives;
   broke_drs_ex_pay is empty at DGL). No charge_id column here — classify by descr
   and deduct_code_id (non-null => a deduction; null => extra pay).
   --------------------------------------------------------------------------- */
SELECT
    x.order_id,
    x.movement_id,
    x.payee_id,
    x.deduct_code_id,          -- non-null => a deduction; null => extra pay
    x.descr,
    x.short_desc,
    x.amount,
    x.rate,
    x.units,
    x.transaction_date
FROM        [lme_1720].[dbo].[driver_extra_pay] x
JOIN        [lme_1720].[dbo].[orders] o ON o.id = x.order_id
WHERE       o.status = 'D'
  AND       o.bill_date >= DATEADD(day, -365, CAST(GETDATE() AS date));


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
