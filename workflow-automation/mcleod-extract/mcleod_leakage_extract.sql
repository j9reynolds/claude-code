/* ============================================================================
   McLeod LME — Accessorial Leakage Extract  (READ-ONLY)
   Target: server DB02, database LME_1720  (McLeod LoadMaster Enterprise, SQL Server)

   Run READ-ONLY (SELECT only). Delivered, billed loads in the trailing 365 days.

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
    mv.carrier_id,
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
FROM        dbo.orders   o
LEFT JOIN   dbo.movement mv   ON mv.id   = o.curr_movement_id
LEFT JOIN   dbo.customer cust ON cust.id = o.customer_id
LEFT JOIN   dbo.payee    pay  ON pay.id  = mv.carrier_id
OUTER APPLY (
    SELECT  MIN(s.actual_arrival)   AS first_check_in,
            MAX(s.actual_departure) AS last_check_out,
            MAX(DATEDIFF(minute, s.actual_arrival, s.actual_departure)) AS max_dwell_minutes,
            MAX(CASE WHEN s.sched_arrive_late IS NOT NULL
                      AND s.actual_arrival > s.sched_arrive_late THEN 1 ELSE 0 END) AS any_late_arrival,
            COUNT(*) AS stop_count
    FROM    dbo.stop s
    WHERE   s.movement_id = o.curr_movement_id
      AND   s.actual_arrival IS NOT NULL
      AND   s.actual_departure IS NOT NULL
) st
WHERE   o.status = 'D'                                       -- delivered
  AND   o.bill_date >= DATEADD(day, -365, CAST(GETDATE() AS date))
ORDER BY o.bill_date;


/* ---------------------------------------------------------------------------
   QUERY B — ITEMIZED CUSTOMER CHARGES  ->  othercharges.csv
   Every other-charge line on those loads, with its REAL code. This is the split
   the connector can't give (it only returns the lump total). No code list to guess.
   --------------------------------------------------------------------------- */
SELECT
    c.order_id,
    c.charge_id,                 -- the accessorial/fuel/etc. code
    c.descr,                     -- if this errors, the column is `description`
    c.amount,
    c.rate,
    c.units
FROM        dbo.other_charge c
JOIN        dbo.orders o ON o.id = c.order_id
WHERE       o.status = 'D'
  AND       o.bill_date >= DATEADD(day, -365, CAST(GETDATE() AS date));


/* ---------------------------------------------------------------------------
   QUERY C — CARRIER-SIDE CHARGES  ->  carriercharges.csv   (optional but ideal)
   Carrier accessorial pay + deductions. The table name varies by LME build, so
   FIND IT FIRST with the discovery snippet, then fill it into the template.
   --------------------------------------------------------------------------- */
-- DISCOVERY: find the carrier other-charge table --
-- SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
--  WHERE TABLE_NAME LIKE '%other%charge%' OR TABLE_NAME LIKE '%carrier%charge%'
--     OR TABLE_NAME LIKE '%pay%detail%';
--
-- TEMPLATE (swap <carrier_charge_table> + its order/movement key + code/amount cols):
-- SELECT cc.movement_id, cc.charge_id, cc.descr, cc.amount
-- FROM   dbo.<carrier_charge_table> cc
-- JOIN   dbo.movement mv ON mv.id = cc.movement_id
-- JOIN   dbo.orders   o  ON o.id  = mv.order_id
-- WHERE  o.status = 'D'
--   AND  o.bill_date >= DATEADD(day, -365, CAST(GETDATE() AS date));


/* ---------------------------------------------------------------------------
   SANITY (optional) — how often is the rate con actually recorded?
   Confirms the control-gap finding (0197341 had it NULL).
   --------------------------------------------------------------------------- */
-- SELECT CASE WHEN rate_confirmation_sent_date IS NULL THEN 'null' ELSE 'set' END AS rc,
--        COUNT(*) FROM dbo.movement GROUP BY CASE WHEN rate_confirmation_sent_date IS NULL THEN 'null' ELSE 'set' END;
