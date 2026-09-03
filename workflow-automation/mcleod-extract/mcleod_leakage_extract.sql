/* ============================================================================
   McLeod LME — Accessorial Leakage Extract  (READ-ONLY)
   Target: server DB02, database LME_1720  (McLeod LoadMaster Enterprise, SQL Server)

   Purpose: produce ONE ROW PER LOAD delivered in the trailing 365 days, with the
   columns the leakage model consumes (reference-implementation/leakage_model.py ->
   required_mcleod_columns()). Pull real stop in/out times, appointment windows,
   accessorial billing/pay, and the signed Rate Confirmation return timestamp.

   THIS SCRIPT ONLY SELECTS. It writes nothing. Run it against a READ replica or
   with a read-only login.

   !! SCHEMA CAVEAT — READ THIS FIRST !!
   McLeod LME table/column names vary by version and by site customization. The
   names below are the standard LME schema, but you MUST confirm them against THIS
   instance before trusting output. Run the discovery block at the bottom first, or
   check McLeod's data dictionary. Every place that needs confirming is marked
   -- CONFIRM.
   ============================================================================ */

/* ---- Parameters --------------------------------------------------------- */
DECLARE @from_date        datetime = DATEADD(day, -365, CAST(GETDATE() AS date));
DECLARE @company_id       varchar(10) = 'TMS';   -- CONFIRM: McLeod company id
/* Image type numbers — from the Accounting folder doc / McLeod image setup.
   temporary POD = 4 is confirmed by the PM. CONFIRM the rest against your list. */
DECLARE @img_signed_ratecon int = NULL;   -- CONFIRM: image type # for a SIGNED Rate Confirmation
DECLARE @img_temp_pod       int = 4;      -- confirmed: "temporary POD" = 4
DECLARE @img_bol_pod        int = NULL;   -- CONFIRM: image type # for BOL / final POD

/* ---- Main extract ------------------------------------------------------- */
;WITH
/* The billable delivery load. In LME the order id is the PRO the reps quote. */
ord AS (
    SELECT  o.id                      AS pro_number,
            o.company_id,
            o.customer_id,
            o.curr_movement_id,
            o.freight_charge          AS linehaul_rate,   -- CONFIRM: linehaul vs total_charge
            o.delivered_date,                              -- CONFIRM col name (may be on movement/stop)
            o.operational_status
    FROM    dbo.orders o                                   -- CONFIRM table
    WHERE   o.company_id = @company_id
      AND   o.delivered_date >= @from_date
      AND   o.status = 'A'                                 -- CONFIRM: active/delivered filter
),
/* Carrier on the current movement. */
mov AS (
    SELECT  m.id AS movement_id, m.order_id,
            m.override_carrier_id AS carrier_id,           -- CONFIRM
            m.override_pay_amt    AS carrier_total_pay,    -- CONFIRM
            m.brokerage_status
    FROM    dbo.movement m                                 -- CONFIRM
),
/* Appointment window + actual in/out at each stop; we summarize the worst dwell. */
stops AS (
    SELECT  s.order_id,
            SUM(CASE WHEN s.actual_departure IS NOT NULL AND s.actual_arrival IS NOT NULL
                     THEN DATEDIFF(minute, s.actual_arrival, s.actual_departure) ELSE 0 END)
                                                   AS total_dwell_minutes,
            MAX(CASE WHEN s.actual_arrival > s.sched_arrive_late THEN 1 ELSE 0 END)
                                                   AS any_late_arrival,   -- appointment miss
            MIN(s.actual_arrival)                  AS first_check_in,
            MAX(s.actual_departure)                AS last_check_out,
            COUNT(*)                               AS stop_count
    FROM    dbo.stop s                                     -- CONFIRM: sched_arrive_early/late, actual_arrival/departure
    GROUP BY s.order_id
),
/* Customer-side accessorial billing (othercharge). Filter to accessorial codes. */
cust_acc AS (
    SELECT  oc.order_id,
            SUM(oc.amount) AS actual_customer_accessorial_billed
    FROM    dbo.othercharge oc                             -- CONFIRM
    WHERE   oc.charge_id IN ('DET','LAY','TONU','STOP','LUMP')  -- CONFIRM your accessorial charge codes
    GROUP BY oc.order_id
),
/* Carrier-side accessorial pay and deductions (carrier other charges). */
carr_acc AS (
    SELECT  occ.order_id,
            SUM(CASE WHEN occ.amount > 0 THEN occ.amount ELSE 0 END) AS actual_carrier_accessorial_paid,
            SUM(CASE WHEN occ.amount < 0 THEN -occ.amount ELSE 0 END) AS actual_deductions_taken
    FROM    dbo.otherchargecarrier occ                     -- CONFIRM table name (carrier other charges)
    WHERE   occ.charge_id IN ('DET','LAY','TONU','TRACK','POD','RATECON','CHKCALL','DIRRUN','EXCL')  -- CONFIRM
    GROUP BY occ.order_id
),
/* Lumper cost actually paid (to net the customer handling markup). */
lumper AS (
    SELECT occ.order_id, SUM(occ.amount) AS lumper_cost
    FROM   dbo.otherchargecarrier occ                      -- CONFIRM
    WHERE  occ.charge_id = 'LUMP'                          -- CONFIRM
    GROUP BY occ.order_id
),
/* When was the SIGNED Rate Confirmation image uploaded? (image header table) */
ratecon_img AS (
    SELECT  i.order_id,                                    -- CONFIRM: image links by order_id or pro_nbr
            MIN(i.create_date) AS signed_ratecon_uploaded_at
    FROM    dbo.imghdr i                                   -- CONFIRM: LME image header table (imghdr / img_images)
    WHERE   i.img_type = @img_signed_ratecon               -- CONFIRM: image type column + value
    GROUP BY i.order_id
),
/* When was a POD (temp or final) uploaded? Used for POD-late derivation. */
pod_img AS (
    SELECT  i.order_id, MIN(i.create_date) AS pod_uploaded_at
    FROM    dbo.imghdr i                                   -- CONFIRM
    WHERE   i.img_type IN (@img_temp_pod, @img_bol_pod)
    GROUP BY i.order_id
)
SELECT
    ord.pro_number,
    CAST(ord.delivered_date AS date)                              AS delivered_date,
    cu.name                                                       AS customer,          -- CONFIRM customer table/col
    ca.name                                                       AS carrier,           -- CONFIRM carrier table/col
    CAST(NULL AS varchar(1))                                      AS team_service,      -- UNKNOWN in McLeod -> leave null (CONFIRM if a flag exists)
    ord.linehaul_rate,
    st.first_check_in                                             AS stop_check_in,
    st.last_check_out                                             AS stop_check_out,
    CAST(NULL AS varchar(1))                                      AS carrier_at_fault,  -- UNKNOWN -> null (model treats conservatively)
    CAST(NULL AS varchar(1))                                      AS signed_facility_proof, -- UNKNOWN unless imaged separately
    CASE WHEN rc.signed_ratecon_uploaded_at IS NOT NULL THEN 'Y' ELSE 'N' END AS revised_signed_ratecon,
    CAST(NULL AS varchar(1))                                      AS customer_paid,     -- from AR paid status (CONFIRM: ar_status)
    CAST(NULL AS varchar(1))                                      AS layover,           -- CONFIRM: derive from othercharge 'LAY'
    CAST(NULL AS varchar(1))                                      AS tonu,              -- CONFIRM: derive from othercharge 'TONU'
    CAST(NULL AS int)                                             AS stopoff_count,     -- derive from stop_count-2 or 'STOP' charges
    ISNULL(lu.lumper_cost, 0)                                     AS lumper_cost,
    CAST(NULL AS varchar(1))                                      AS driver_assist_preapproved,
    CAST(NULL AS varchar(1))                                      AS macropoint_tracking_provided, -- from MacroPoint/P44 integration table if present
    CASE WHEN st.any_late_arrival = 1 THEN 'N' ELSE 'Y' END       AS arrived_on_time,
    CAST(NULL AS varchar(1))                                      AS direct_run_violation,
    CAST(NULL AS int)                                             AS missed_check_calls_count,
    CASE WHEN pod.pod_uploaded_at IS NULL THEN 'Y'
         WHEN DATEDIFF(hour, st.last_check_out, pod.pod_uploaded_at) > 1 THEN 'Y'
         ELSE 'N' END                                            AS pod_late,          -- >1h after unload per Rate Con
    CAST(NULL AS int)                                             AS pod_days_late,
    CASE WHEN rc.signed_ratecon_uploaded_at IS NOT NULL THEN 'Y' ELSE 'N' END AS signed_ratecon_returned,
    CAST(NULL AS varchar(1))                                      AS exclusive_use_violation,
    ISNULL(cca.actual_customer_accessorial_billed, 0)            AS actual_customer_accessorial_billed,
    ISNULL(cra.actual_carrier_accessorial_paid, 0)              AS actual_carrier_accessorial_paid,
    ISNULL(cra.actual_deductions_taken, 0)                      AS actual_deductions_taken
FROM        ord
LEFT JOIN   mov        ON mov.order_id = ord.pro_number
LEFT JOIN   dbo.customer cu ON cu.id = ord.customer_id          -- CONFIRM
LEFT JOIN   dbo.carrier  ca ON ca.id = mov.carrier_id           -- CONFIRM
LEFT JOIN   stops      st  ON st.order_id  = ord.pro_number
LEFT JOIN   cust_acc   cca ON cca.order_id = ord.pro_number
LEFT JOIN   carr_acc   cra ON cra.order_id = ord.pro_number
LEFT JOIN   lumper     lu  ON lu.order_id  = ord.pro_number
LEFT JOIN   ratecon_img rc ON rc.order_id  = ord.pro_number
LEFT JOIN   pod_img    pod ON pod.order_id = ord.pro_number
ORDER BY    ord.delivered_date;

/* ============================================================================
   DISCOVERY BLOCK — run this FIRST to confirm the real table/column names on
   THIS instance, then fix every -- CONFIRM above.
   ============================================================================ */
-- Tables that look order/stop/charge/image related:
-- SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
--  WHERE TABLE_NAME LIKE '%order%' OR TABLE_NAME LIKE '%stop%'
--     OR TABLE_NAME LIKE '%othercharge%' OR TABLE_NAME LIKE '%img%'
--     OR TABLE_NAME LIKE '%movement%' ORDER BY TABLE_NAME;
--
-- Columns on the stop table (appointment + actual in/out):
-- SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
--  WHERE TABLE_NAME = 'stop' ORDER BY ORDINAL_POSITION;
--
-- Distinct accessorial charge codes actually in use (fix the IN(...) lists):
-- SELECT charge_id, descr, COUNT(*) n, SUM(amount) total
--   FROM dbo.othercharge GROUP BY charge_id, descr ORDER BY n DESC;
--
-- Image types in use (confirm signed rate con # and BOL/POD #; temp POD = 4):
-- SELECT img_type, COUNT(*) n FROM dbo.imghdr GROUP BY img_type ORDER BY img_type;
