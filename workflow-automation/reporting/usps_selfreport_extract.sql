/* ============================================================================
   USPS Self Report — RAW EXTRACT (the real one, run in SSMS against McLeod LME)
   ----------------------------------------------------------------------------
   This is the authoritative source for the Self Report's raw per-load rows. Run it
   in SSMS, export the grid to CSV (or paste into the "… Raw Data With Reason Codes"
   tab), then feed it to the pipeline:

       python3 usps_selfreport_pipeline.py  <export>.csv  <YYYY-MM>  GEGW  OUT_overview.xlsx

   The pipeline reads these exact column headers (O/D PAIR, ON TIME Arrival Y/N,
   Dispatch on time Y/N, ON TIME DELIVERY y/n, Load ID) and reproduces the Overview
   (Load Count incl. VOID; metric % = count("Y")/loads; TOTAL = unweighted mean of
   lane %s). Reason codes are added by hand after export (this query emits none).

   Scope (matches the report): customer UNITMETN, ordered_date in the prior calendar
   month, status D or V, order id NOT LIKE '%S%' (excludes subject orders).

   TWO THINGS TO REVIEW (flagged, not changed — logic left exactly as run):

   1. DISPATCH "actual" is SYNTHETIC and NON-DETERMINISTIC. `actual dispatch time`
      (ROPH.Posted_Date) = the rate-con posted_date MINUS a random 55–67 minutes
      (DATEADD(MINUTE, -((ABS(CHECKSUM(NEWID())) % 13) + 55), oph.posted_date)).
      Because the "actual" is always ~1h before the "planned", Dispatch_Flag is
      effectively always on-time (≈100%), and NEWID() makes the value change on every
      run. If JBH ever audits dispatch, "actual = planned − random hour" won't hold up.
      If a real dispatch timestamp exists (e.g. movement actual departure), point the
      dispatch comparison at it; otherwise this metric is not measuring performance.

   2. The trailing `STRING_AGG(...) AS TripIDs` is an aggregate used alongside
      non-aggregated columns with no GROUP BY — SQL Server rejects that (msg 8120).
      The pipeline does not need it; drop that column for a clean per-load export.
   ============================================================================ */

-- LAST MONTH extract, customer UNITMETN (USPS). Verbatim as run by Ops.

DECLARE @PUDate_START DATE;
DECLARE @PUDate_END   DATE;
DECLARE @CustomerID   NVARCHAR(15);

SET @CustomerID = 'UNITMETN';
SET @PUDate_START = DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()) - 1, 0);   -- start of last month
SET @PUDate_END   = DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()), 0);       -- start of this month (exclusive)

SELECT
    'Delta Group Logistics' AS [SUPPLIER NAME],

    CASE WHEN CHARINDEX('0029H', x.cleaned_refno) > 0 THEN '''0029H' ELSE '''0029H' END AS [Contract ID],

    CONCAT('''',
        CASE
            WHEN LEN(x.cleaned_refno) - LEN(REPLACE(x.cleaned_refno, '/', '')) = 0 THEN LTRIM(RTRIM(x.cleaned_refno))
            WHEN LEN(x.cleaned_refno) - LEN(REPLACE(x.cleaned_refno, '/', '')) = 1 THEN LTRIM(RTRIM(SUBSTRING(x.cleaned_refno, CHARINDEX('/', x.cleaned_refno) + 1, LEN(x.cleaned_refno))))
            WHEN LEN(x.cleaned_refno) - LEN(REPLACE(x.cleaned_refno, '/', '')) >= 2 THEN LTRIM(RTRIM(SUBSTRING(x.cleaned_refno, CHARINDEX('/', x.cleaned_refno, CHARINDEX('/', x.cleaned_refno) + 1) + 1, LEN(x.cleaned_refno))))
            ELSE 'Err'
        END) AS [SV Trip ID],

    CONCAT('''', COALESCE(LTRIM(RTRIM(o.[blnum])), '')) AS [Load ID],

    CONCAT(COALESCE(LTRIM(RTRIM(pu.[city_name])), ''), ', ', COALESCE(LTRIM(RTRIM(pu.[state])), ''),
           ' | ',
           COALESCE(LTRIM(RTRIM(del.[city_name])), ''), ', ', COALESCE(LTRIM(RTRIM(del.[state])), '')) AS [O/D PAIR],

    CASE WHEN pu.[sched_arrive_early] IS NOT NULL THEN pu.[sched_arrive_early] ELSE '' END AS [Scheduled arrival time],
    pu.[actual_arrival] AS [Actual arrival time],

    CASE
        WHEN o.[status] = 'V' THEN 'V'
        WHEN pu.[actual_arrival] IS NULL OR pu.[sched_arrive_early] IS NULL THEN 'Err'
        WHEN pu.[sched_arrive_late] IS NULL AND pu.[actual_arrival] > pu.[sched_arrive_early] THEN 'N'
        WHEN pu.[sched_arrive_late] IS NULL AND pu.[actual_arrival] = pu.[sched_arrive_early] THEN 'Y'
        WHEN pu.[sched_arrive_late] IS NULL AND pu.[actual_arrival] < pu.[sched_arrive_early] THEN 'Y'
        WHEN pu.[actual_arrival] BETWEEN pu.[sched_arrive_early] AND pu.[sched_arrive_late] THEN 'Y'
        WHEN pu.[actual_arrival] < pu.[sched_arrive_early] THEN 'Y'
        WHEN pu.[actual_arrival] > pu.[sched_arrive_late] THEN 'N'
        ELSE 'Err'
    END AS [ON TIME Arrival Y/N],

    ROPH.Posted_Date AS [actual dispatch time],                                   -- SYNTHETIC (see header note 1)
    CASE WHEN oph.[posted_date] IS NOT NULL THEN oph.[posted_date] ELSE '' END AS [planned dispatch time],

    CASE
        WHEN o.[status] = 'V' THEN 'N'
        WHEN ROPH.Posted_Date IS NULL OR oph.Posted_Date IS NULL THEN 'Err'
        WHEN ROPH.Posted_Date BETWEEN pu.sched_arrive_early AND oph.Posted_Date THEN 'Y'
        WHEN ROPH.Posted_Date < oph.Posted_Date THEN 'Y'
        WHEN ROPH.Posted_Date > oph.Posted_Date THEN 'N'
        ELSE 'Err'
    END AS [Dispatch on time Y/N],

    del.[actual_arrival] AS [Actual delivery time],
    CASE WHEN del.[sched_arrive_early] IS NOT NULL THEN del.[sched_arrive_early] ELSE '' END AS [planned delivery time],

    CASE
        WHEN o.[status] = 'V' THEN 'V'
        WHEN del.[actual_arrival] IS NULL OR del.[sched_arrive_early] IS NULL THEN 'Err'
        WHEN del.[sched_arrive_late] IS NULL AND del.[actual_arrival] > del.[sched_arrive_early] THEN 'N'
        WHEN del.[sched_arrive_late] IS NULL AND del.[actual_arrival] = del.[sched_arrive_early] THEN 'Y'
        WHEN del.[sched_arrive_late] IS NULL AND del.[actual_arrival] < del.[sched_arrive_early] THEN 'Y'
        WHEN del.[actual_arrival] BETWEEN del.[sched_arrive_early] AND del.[sched_arrive_late] THEN 'Y'
        WHEN del.[actual_arrival] < del.[sched_arrive_early] THEN 'Y'
        WHEN del.[actual_arrival] > del.[sched_arrive_late] THEN 'N'
        ELSE 'Err'
    END AS [ON TIME DELIVERY y/n],

    CASE
        WHEN o.[status] = 'V' THEN NULL
        WHEN (CASE WHEN pu.[actual_arrival] IS NULL OR pu.[sched_arrive_early] IS NULL THEN 'Err'
                   WHEN pu.[actual_arrival] > ISNULL(pu.[sched_arrive_late], pu.[sched_arrive_early]) THEN 'N'
                   ELSE 'Y' END) = 'Y' THEN 1
        WHEN (CASE WHEN pu.[actual_arrival] IS NULL OR pu.[sched_arrive_early] IS NULL THEN 'Err'
                   WHEN pu.[actual_arrival] > ISNULL(pu.[sched_arrive_late], pu.[sched_arrive_early]) THEN 'N'
                   ELSE 'Y' END) = 'N' THEN 0
        ELSE NULL
    END AS [OTP_Flag],

    CASE
        WHEN o.[status] = 'V' THEN NULL
        WHEN ROPH.Posted_Date IS NULL OR oph.Posted_Date IS NULL THEN NULL
        WHEN ROPH.Posted_Date <= oph.Posted_Date THEN 1
        WHEN ROPH.Posted_Date > oph.Posted_Date THEN 0
        ELSE NULL
    END AS [Dispatch_Flag],

    CASE
        WHEN o.[status] = 'V' THEN NULL
        WHEN del.[actual_arrival] IS NULL OR del.[sched_arrive_early] IS NULL THEN NULL
        WHEN del.[actual_arrival] > ISNULL(del.[sched_arrive_late], del.[sched_arrive_early]) THEN 0
        ELSE 1
    END AS [OTD_Flag],

    CASE WHEN o.[status] = 'V' THEN 1 ELSE 0 END AS IsVoid

    /* NOTE: the original trailing `STRING_AGG(...) AS TripIDs` is removed here — it is an
       aggregate with no GROUP BY (SQL Server msg 8120) and the pipeline does not use it. */

FROM [lme_1720].[dbo].[orders] o

OUTER APPLY (
    SELECT TOP 1 oph.*
    FROM [lme_1720].[dbo].[order_post_hist] AS oph
    WHERE oph.order_id = o.id AND oph.posted_type = 'C'
    ORDER BY oph.posted_date DESC
) AS oph
CROSS APPLY (
    SELECT CASE WHEN oph.posted_date IS NOT NULL
                THEN DATEADD(MINUTE, -((ABS(CHECKSUM(NEWID())) % 13) + 55), oph.posted_date)
                ELSE NULL END AS Posted_Date
) AS ROPH

LEFT JOIN [lme_1720].[dbo].[payee] p            ON p.[id] = oph.[carrier_id]
LEFT JOIN [lme_1720].[dbo].[order_hist_type] oht ON oht.[id] = oph.[posted_type]
LEFT JOIN [lme_1720].[dbo].[movement] m          ON o.[curr_movement_id] = m.[ID]
LEFT JOIN [lme_1720].[dbo].[stop] pu             ON o.[shipper_stop_id]   = pu.[id] AND pu.[stop_type] = 'PU'
LEFT JOIN [lme_1720].[dbo].[stop] del            ON o.[consignee_stop_id] = del.[id] AND del.[stop_type] = 'SO'
CROSS APPLY (
    SELECT REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
        o.[consignee_refno], ' / ', '/'), '/ ', '/'), ' /', '/'), '  / ', '/'),
        '/  ', '/'), '  /', '/'), ' /  ', '/'), '  / ', '/'), CHAR(160) + '/', '/') AS cleaned_refno
) AS x

WHERE o.ordered_date >= @PUDate_START
  AND o.ordered_date <  @PUDate_END
  AND o.customer_id  = @CustomerID
  AND o.id NOT LIKE '%S%'
  AND o.[status] IN ('D', 'V')
ORDER BY o.[id], [blnum] DESC;

SET @CustomerID = NULL;
SET @PUDate_START = NULL;
SET @PUDate_END = NULL;
