/* ============================================================================
   USPS GEGW "Self Report" — source query  (READ-ONLY)
   Feeds report_usps_self_report.py. One row per delivered order in the month, with
   the origin (PU) and destination (SO) city/state, the scheduled reference time, and
   the actual arrival/departure the three metrics compare against.

   Target: DGLIQ warehouse, database DGL_TMS (schema tms) — the normalized McLeod
   replica. Confirmed via dgliq_describe_schema: tms.[Order], tms.Movement, tms.Stop.
   Run on-network (scheduled monthly). Parameterize @customer + month.

   Scheduled reference (*_sched) = COALESCE(OrigSchedLate, SchedArriveLate): the ORIGINAL
   tender commitment when present, else the current appointment late window. USPS/JBH
   scores the original commitment; a reschedule must not erase a miss. The Python side
   only compares actual <= sched, so the business rule lives here.

   CONFIRM before first live run:
     * @customer — the McLeod customer code for the USPS GEGW book. 'UNITMETN' (USPS)
       is the working assumption; the GEGW loads brokered under J.B. Hunt tender 0029H
       may instead sit under a J.B. Hunt customer code. Verify in McLeod.
     * The "lane number" in parens on each row (e.g. "(89)") — the JBH/USPS lane id.
       If McLeod stores it (order RefNumber / a stop RefNumber / a user field), select
       it as lane_number below; otherwise leave it null and the label omits the parens.
     * The OT Dispatch scheduled reference. Here it uses the PU scheduled time (departed
       by the scheduled pickup close). If USPS defines a distinct dispatch cutoff,
       point pu_sched for the dispatch comparison at that field instead.
   ============================================================================ */

DECLARE @mcleod_customer_id varchar(8) = 'UNITMETN';        -- USPS GEGW book — CONFIRM
DECLARE @month_start date = '2026-08-01';
DECLARE @month_end   date = EOMONTH(@month_start);

;WITH ord AS (
    SELECT  o.McLeodOrderId, o.McLeodCurrentMovementId, o.BillDate
    FROM    [DGL_TMS].[tms].[Order] o
    WHERE   o.McLeodCustomerId = @mcleod_customer_id
      AND   o.Status = 'D'                          -- delivered
      AND   o.BillDate BETWEEN @month_start AND @month_end
      AND   o.IsDeletedInSource = 0
),
/* first pickup stop on the order */
pu AS (
    SELECT  s.McLeodOrderId, s.CityName, s.StateCode, s.RefNumber,
            COALESCE(s.OrigSchedLate, s.SchedArriveLate) AS sched,
            s.ActualArrival, s.ActualDeparture,
            ROW_NUMBER() OVER (PARTITION BY s.McLeodOrderId
                               ORDER BY s.OrderSequence) AS rn
    FROM    [DGL_TMS].[tms].[Stop] s
    WHERE   s.StopType = 'PU' AND s.IsDeletedInSource = 0
),
/* last delivery stop on the order */
so AS (
    SELECT  s.McLeodOrderId, s.CityName, s.StateCode,
            COALESCE(s.OrigSchedLate, s.SchedArriveLate) AS sched,
            s.ActualArrival,
            ROW_NUMBER() OVER (PARTITION BY s.McLeodOrderId
                               ORDER BY s.OrderSequence DESC) AS rn
    FROM    [DGL_TMS].[tms].[Stop] s
    WHERE   s.StopType = 'SO' AND s.IsDeletedInSource = 0
)
SELECT
    ord.McLeodOrderId                       AS order_id,
    pu.RefNumber                            AS lane_number,   -- CONFIRM source of the "(89)" id
    pu.CityName                             AS pu_city,
    pu.StateCode                            AS pu_state,
    CONVERT(varchar(19), pu.sched, 120)     AS pu_sched,
    CONVERT(varchar(19), pu.ActualArrival, 120)   AS pu_arrival,
    CONVERT(varchar(19), pu.ActualDeparture, 120) AS pu_departure,
    so.CityName                             AS so_city,
    so.StateCode                            AS so_state,
    CONVERT(varchar(19), so.sched, 120)     AS so_sched,
    CONVERT(varchar(19), so.ActualArrival, 120)   AS so_arrival,
    ''                                      AS comment        -- manual exception note, filled on review
FROM        ord
LEFT JOIN   pu ON pu.McLeodOrderId = ord.McLeodOrderId AND pu.rn = 1
LEFT JOIN   so ON so.McLeodOrderId = ord.McLeodOrderId AND so.rn = 1
ORDER BY    pu.StateCode, pu.CityName, so.StateCode, so.CityName;
