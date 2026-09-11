/* ============================================================================
   McLeod LME - Accessorial Shadow Report, MONTHLY extract  (READ-ONLY)
   Target: server DB02, database lme_1720  (McLeod LoadMaster Enterprise, SQL Server 2016)

   The monthly sibling of mcleod_leakage_extract.sql. Same five result sets and the
   SAME columns (so the existing analyzer/classifier consume it unchanged), but scoped
   to the PRIOR CALENDAR MONTH of delivered loads, ALL customers -- for a recurring
   "money left on the table" shadow report that a human reviews. READ-ONLY: SELECT only,
   no writes; this reports, it never bills or pays.

   Every table is fully qualified [lme_1720].[dbo].[...] so it runs from any DB context.
   All 27 referenced columns were verified live against this instance (INFORMATION_SCHEMA,
   2026-09-09). Char columns are space-padded (SQL Server 2016) -- LTRIM(RTRIM(x)) before
   comparing/displaying; no STRING_AGG/TRIM.

   Host run (Invoke-Sqlcmd): export each query to its CSV -- loads.csv, othercharges.csv,
   carrierpay.csv, chargecodes.csv, stops.csv -- then feed analyze_leakage.py.
   ============================================================================ */

/* Prior calendar month window [@mfrom, @mto).  A run on 2026-09-09 covers Aug 2026. */
DECLARE @mfrom date = DATEFROMPARTS(YEAR(DATEADD(month, -1, GETDATE())),
                                    MONTH(DATEADD(month, -1, GETDATE())), 1);
DECLARE @mto   date = DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1);


/* ---------------------------------------------------------------------------
   QUERY A - LOADS  ->  loads.csv     (one row per delivered load)
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
  AND   o.bill_date >= @mfrom
  AND   o.bill_date <  @mto
ORDER BY o.bill_date;


/* ---------------------------------------------------------------------------
   QUERY B - CUSTOMER accessorial charges  ->  othercharges.csv
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
  AND       o.bill_date >= @mfrom
  AND       o.bill_date <  @mto;


/* ---------------------------------------------------------------------------
   QUERY C - CARRIER accessorial pay  ->  carrierpay.csv
   deduct_code_id non-null => a deduction; null => extra pay. (broke_drs_ex_pay is empty.)
   --------------------------------------------------------------------------- */
SELECT
    x.order_id,
    x.movement_id,
    x.payee_id,
    x.deduct_code_id,
    x.descr,
    x.short_desc,
    x.amount,
    x.rate,
    x.units,
    x.transaction_date
FROM        [lme_1720].[dbo].[driver_extra_pay] x
JOIN        [lme_1720].[dbo].[orders] o ON o.id = x.order_id
WHERE       o.status = 'D'
  AND       o.bill_date >= @mfrom
  AND       o.bill_date <  @mto;


/* ---------------------------------------------------------------------------
   QUERY D - CHARGE-CODE DICTIONARY  ->  chargecodes.csv   (tiny; whole dictionary)
   is_fuel_surcharge flags fuel so it is never counted as an accessorial.
   --------------------------------------------------------------------------- */
SELECT id AS charge_id, descr, is_fuel_surcharge, glid
FROM   [lme_1720].[dbo].[charge_code];


/* ---------------------------------------------------------------------------
   QUERY E - PER-STOP TIMES  ->  stops.csv   (appointment-based detention)
   Join stop -> orders DIRECTLY on s.order_id (PM-corrected; not via movement).
   McLeod actual times run ~25-60 min off the POD, so this is the POPULATION estimate;
   the live per-load figure reads the POD per event (pod_reader.py).
   --------------------------------------------------------------------------- */
SELECT
    s.order_id,
    s.stop_type,
    s.order_sequence,
    -- CONVERT to ISO (style 120: 'YYYY-MM-DD HH:MI:SS') so the CSV is locale-independent;
    -- Export-Csv would otherwise write the host's local datetime format and break parsing.
    CONVERT(varchar(19), s.sched_arrive_early, 120) AS appointment_early,
    CONVERT(varchar(19), s.sched_arrive_late,  120) AS appointment_late,
    CONVERT(varchar(19), s.actual_arrival,     120) AS actual_arrival,
    CONVERT(varchar(19), s.actual_departure,   120) AS actual_departure,
    s.appt_required
FROM        [lme_1720].[dbo].[stop] s
JOIN        [lme_1720].[dbo].[orders] o ON o.id = s.order_id
WHERE       o.status = 'D'
  AND       o.bill_date >= @mfrom
  AND       o.bill_date <  @mto
ORDER BY    s.order_id, s.order_sequence;
