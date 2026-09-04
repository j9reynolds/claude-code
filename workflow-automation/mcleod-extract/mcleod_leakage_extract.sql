/* ============================================================================
   McLeod LME — Accessorial Leakage Extract  (READ-ONLY)
   Target: server DB02, database LME_1720  (McLeod LoadMaster Enterprise, SQL Server)

   Purpose: ONE ROW PER DELIVERED LOAD in the trailing 365 days, with the columns the
   leakage model consumes (reference-implementation/leakage_model.py). Real stop in/out
   times, appointment windows, itemized accessorial billing, carrier pay, and the rate-con
   state.

   READ-ONLY. Only SELECTs. Run against a read replica or a read-only login.

   SCHEMA NOTE: the core table/column names below (orders, movement, stop, payee/drs_payee)
   were CONFIRMED against this live instance via the dgl-mcp connector. The remaining
   -- CONFIRM markers are the itemized-charge pieces the connector does not expose
   (other_charge codes, carrier-side charges) — settle them with the discovery block at the
   bottom, which is a two-minute job.
   ============================================================================ */

DECLARE @from_date datetime = DATEADD(day, -365, CAST(GETDATE() AS date));

;WITH
/* Delivered, billed loads in the window. (status 'D' = delivered; confirmed.) */
ord AS (
    SELECT  o.id                AS pro_number,
            o.customer_id,
            o.curr_movement_id,
            o.freight_charge,          -- confirmed
            o.otherchargetotal,        -- confirmed: LUMP of all other charges (see oc CTE for the split)
            o.total_charge,            -- confirmed
            o.bill_date,               -- confirmed
            o.ordered_date,            -- confirmed
            o.equipment_type_id        -- confirmed (proxy only; team-service not stored)
    FROM    dbo.orders o
    WHERE   o.status = 'D'                     -- confirmed: delivered
      AND   o.bill_date >= @from_date          -- billed in the last 365 days
),
/* Carrier + pay + rate-con state on the current movement. (all confirmed via connector) */
mov AS (
    SELECT  m.id AS movement_id, m.order_id,
            m.carrier_id,
            m.override_pay_amt,                 -- confirmed: total carrier pay
            m.rate_confirmation_status,         -- confirmed (often NULL — data-quality caveat)
            m.rate_confirmation_sent_date       -- confirmed: when rate con was sent to carrier
    FROM    dbo.movement m
),
/* Worst dwell + appointment miss across the load's stops. (stop cols confirmed) */
stops AS (
    SELECT  s.movement_id,
            MAX(DATEDIFF(minute, s.actual_arrival, s.actual_departure)) AS max_dwell_minutes,
            MAX(CASE WHEN s.sched_arrive_late IS NOT NULL
                      AND s.actual_arrival > s.sched_arrive_late THEN 1 ELSE 0 END) AS any_late_arrival,
            MIN(s.actual_arrival)   AS first_check_in,
            MAX(s.actual_departure) AS last_check_out,
            SUM(CASE WHEN s.stop_type NOT IN ('PU','SO') THEN 1 ELSE 0 END) AS extra_stops  -- CONFIRM stop_type codes
    FROM    dbo.stop s
    WHERE   s.actual_arrival IS NOT NULL AND s.actual_departure IS NOT NULL
    GROUP BY s.movement_id
),
/* ITEMIZED customer accessorial billing — the piece the connector could not give us.
   McLeod line-item other charges live in dbo.other_charge. CONFIRM the table name and the
   accessorial charge codes with the discovery block, then adjust the IN(...) list. */
oc AS (
    SELECT  c.order_id,
            SUM(CASE WHEN c.charge_id IN ('DET','LAY','TONU','STOP','LUMP','DRVAST')  -- CONFIRM codes
                     THEN c.amount ELSE 0 END)                        AS accessorial_billed,
            SUM(CASE WHEN c.charge_id = 'LUMP' THEN c.amount ELSE 0 END) AS lumper_billed  -- CONFIRM
    FROM    dbo.other_charge c                                        -- CONFIRM table name
    GROUP BY c.order_id
),
/* Carrier-side accessorial pay + deductions. In many LME builds these are other_charge
   rows tied to the movement, or a carrier-charge table. CONFIRM against your instance. */
cc AS (
    SELECT  cc.movement_id,
            SUM(CASE WHEN cc.amount > 0 THEN cc.amount ELSE 0 END)  AS carrier_accessorial_paid,   -- CONFIRM
            SUM(CASE WHEN cc.amount < 0 THEN -cc.amount ELSE 0 END) AS deductions_taken            -- CONFIRM
    FROM    dbo.othercharge_carrier cc                               -- CONFIRM table name (carrier other charges)
    GROUP BY cc.movement_id
)
SELECT
    ord.pro_number,
    CONVERT(varchar(10), ord.bill_date, 120)                     AS delivered_date,
    c.name                                                        AS customer,        -- CONFIRM customer name col
    p.name                                                        AS carrier,         -- payee/drs_payee (confirmed join shape)
    CAST(NULL AS varchar(1))                                      AS team_service,    -- not stored in McLeod -> unknown
    ord.freight_charge                                            AS linehaul_rate,
    st.first_check_in                                             AS stop_check_in,
    st.last_check_out                                             AS stop_check_out,
    CAST(NULL AS varchar(1))                                      AS carrier_at_fault,        -- judgment; unknown
    CAST(NULL AS varchar(1))                                      AS signed_facility_proof,   -- unknown unless imaged separately
    CASE WHEN mov.rate_confirmation_sent_date IS NOT NULL THEN 'Y' ELSE 'N' END AS revised_signed_ratecon,  -- best available
    CASE WHEN ord.bill_date IS NOT NULL THEN 'Y' ELSE 'N' END     AS customer_paid,   -- proxy: billed. CONFIRM ar paid status if available
    CAST(NULL AS varchar(1))                                      AS layover,         -- derive: oc has 'LAY' > 0
    CAST(NULL AS varchar(1))                                      AS tonu,            -- derive: oc has 'TONU' > 0
    ISNULL(st.extra_stops, 0)                                     AS stopoff_count,
    0                                                             AS lumper_cost,     -- from carrier side if separable; else oc.lumper_billed
    CAST(NULL AS varchar(1))                                      AS driver_assist_preapproved,
    CAST(NULL AS varchar(1))                                      AS macropoint_tracking_provided,  -- from tracking integration table if present
    CASE WHEN st.any_late_arrival = 1 THEN 'N' ELSE 'Y' END       AS arrived_on_time,
    CAST(NULL AS varchar(1))                                      AS direct_run_violation,
    CAST(NULL AS int)                                             AS missed_check_calls_count,
    CAST(NULL AS varchar(1))                                      AS pod_late,        -- needs image upload time (imghdr) vs unload
    CAST(NULL AS int)                                             AS pod_days_late,
    CASE WHEN mov.rate_confirmation_sent_date IS NOT NULL THEN 'Y' ELSE 'N' END AS signed_ratecon_returned,
    CAST(NULL AS varchar(1))                                      AS exclusive_use_violation,
    ISNULL(oc.accessorial_billed, 0)                             AS actual_customer_accessorial_billed,
    ISNULL(cc.carrier_accessorial_paid, 0)                      AS actual_carrier_accessorial_paid,
    ISNULL(cc.deductions_taken, 0)                              AS actual_deductions_taken
FROM        ord
LEFT JOIN   mov  ON mov.movement_id = ord.curr_movement_id
LEFT JOIN   dbo.customer c ON c.id = ord.customer_id             -- CONFIRM customer table/col
LEFT JOIN   dbo.payee    p ON p.id = mov.carrier_id             -- payee INNER JOIN drs_payee per connector
LEFT JOIN   stops st ON st.movement_id = ord.curr_movement_id
LEFT JOIN   oc      ON oc.order_id     = ord.pro_number
LEFT JOIN   cc      ON cc.movement_id  = ord.curr_movement_id
ORDER BY    ord.bill_date;

/* ============================================================================
   DISCOVERY BLOCK — run FIRST to settle the remaining -- CONFIRM items.
   ============================================================================ */
-- Itemized customer charge codes actually in use (fix the oc IN(...) list):
-- SELECT charge_id, descr, COUNT(*) n, SUM(amount) total
--   FROM dbo.other_charge GROUP BY charge_id, descr ORDER BY total DESC;
--
-- Find the carrier other-charge table (carrier accessorials / deductions):
-- SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
--  WHERE TABLE_NAME LIKE '%other%charge%' OR TABLE_NAME LIKE '%carrier%charge%';
--
-- Confirm stop_type codes (PU/SO seen; is there DEL, etc.?):
-- SELECT stop_type, COUNT(*) FROM dbo.stop GROUP BY stop_type;
--
-- Rate-con population rate (how often is rate_confirmation_sent_date actually set?):
-- SELECT CASE WHEN rate_confirmation_sent_date IS NULL THEN 'null' ELSE 'set' END, COUNT(*)
--   FROM dbo.movement GROUP BY CASE WHEN rate_confirmation_sent_date IS NULL THEN 'null' ELSE 'set' END;
