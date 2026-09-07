/* ============================================================================
   USPS Self Report — McLeod CROSS-CHECK query  (READ-ONLY)   *** NOT the source ***

   IMPORTANT: The USPS Self Report is built from the J.B. Hunt data extract (the
   "<Program> Raw Data With Reason Codes" tab: Contract 0029H, SV Trip ID, Load ID,
   O/D PAIR, JBH scheduled/planned vs actual times, and Y/N/"Order is VOID" flags).
   report_usps_self_report.py consumes THAT extract and reproduces the filed report
   exactly (verified 0 differences on the real May 2026 report). McLeod is NOT the
   authoritative source for the on-time numbers JBH scores, so this query does not
   generate the report.

   What this query IS for: an independent CROSS-CHECK. Map each JBH Load ID to its
   McLeod order and pull Delta's OWN scheduled/actual stop times, so you can flag loads
   where McLeod and the JBH extract disagree (a data-quality / dispute aid). Confirm the
   join field first — the 9-digit JBH Load ID (e.g. 110044044) is typically stored on the
   McLeod order as a reference/BOL number; verify where before relying on it.

   Target: DGLIQ warehouse, DGL_TMS (schema tms). Run on-network. Parameterize the month.
   ============================================================================ */

DECLARE @month_start date = '2026-08-01';
DECLARE @month_end   date = EOMONTH(@month_start);

;WITH pu AS (          -- first pickup stop per order
    SELECT s.McLeodOrderId, s.CityName, s.StateCode,
           COALESCE(s.OrigSchedLate, s.SchedArriveLate) AS sched_arrive,
           s.ActualArrival, s.ActualDeparture,
           ROW_NUMBER() OVER (PARTITION BY s.McLeodOrderId ORDER BY s.OrderSequence) AS rn
    FROM   [DGL_TMS].[tms].[Stop] s
    WHERE  s.StopType = 'PU' AND s.IsDeletedInSource = 0
),
so AS (               -- last delivery stop per order
    SELECT s.McLeodOrderId, s.CityName, s.StateCode,
           COALESCE(s.OrigSchedLate, s.SchedArriveLate) AS sched_arrive,
           s.ActualArrival,
           ROW_NUMBER() OVER (PARTITION BY s.McLeodOrderId ORDER BY s.OrderSequence DESC) AS rn
    FROM   [DGL_TMS].[tms].[Stop] s
    WHERE  s.StopType = 'SO' AND s.IsDeletedInSource = 0
)
SELECT
    o.McLeodOrderId                              AS mcleod_order,
    o.RefNumber                                  AS mcleod_ref,     -- expected JBH Load ID; CONFIRM
    UPPER(pu.CityName + ', ' + pu.StateCode + ' | '
          + so.CityName + ', ' + so.StateCode)   AS lane_mcleod,    -- compare to JBH O/D PAIR
    CONVERT(varchar(19), pu.sched_arrive,   120) AS pu_sched_mcleod,
    CONVERT(varchar(19), pu.ActualArrival,  120) AS pu_actual_arrival_mcleod,
    CONVERT(varchar(19), pu.ActualDeparture,120) AS pu_actual_depart_mcleod,
    CONVERT(varchar(19), so.sched_arrive,   120) AS del_sched_mcleod,
    CONVERT(varchar(19), so.ActualArrival,  120) AS del_actual_arrival_mcleod
FROM        [DGL_TMS].[tms].[Order] o
LEFT JOIN   pu ON pu.McLeodOrderId = o.McLeodOrderId AND pu.rn = 1
LEFT JOIN   so ON so.McLeodOrderId = o.McLeodOrderId AND so.rn = 1
WHERE       o.BillDate BETWEEN @month_start AND @month_end
  AND       o.IsDeletedInSource = 0
  AND       o.RefNumber IN ( /* paste the month's JBH Load IDs from the raw extract */ )
ORDER BY    lane_mcleod, o.RefNumber;
