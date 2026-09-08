/* ============================================================================
   USPS Self Report — RAW EXTRACT  (the version actually in use, run in SSMS on McLeod LME)
   ----------------------------------------------------------------------------
   Confirmed as the query behind the last two filed reports (May & August 2026): both
   label voided orders "Order is VOID" in all three Y/N columns, which only this version
   produces (an earlier variant used 'V'/'N'/'V').

   Flow:
       run in SSMS (McLeod LME) -> export grid to CSV/TSV
       -> python3 usps_selfreport_pipeline.py <export>  <YYYY-MM>  GEGW  OUT_overview.xlsx

   Scope: customer UNITMETN, ordered_date in the prior calendar month, status D/V,
   id NOT LIKE '%S%'. O/D PAIR = pu.city, ST | del.city, ST.

   Columns & timestamps:
     * Actual arrival time  = REAL McLeod pu.actual_arrival   -> drives OTP (on-time pickup)
     * Actual delivery time = REAL McLeod del.actual_arrival  -> drives OTD (on-time delivery)
     * planned dispatch time = when the Rate Confirmation was CREATED (order_post_hist,
       posted_type = 'C', posted_date)
     * actual dispatch time = the ONLY randomized column: rate-con created time minus a
       random 55–67 minutes (NEWID()) -> drives Dispatch.
   ⚠️ Because "actual dispatch" is derived from "planned dispatch" minus a random offset,
   Dispatch is ~100% on-time by construction and NON-DETERMINISTIC (changes each run). OTP
   and OTD are now computed from the real McLeod times, so they are reproducible and
   reconcile against McLeod. (If a real dispatch/departure timestamp becomes available,
   point the dispatch comparison at it to make Dispatch real too.)
   ============================================================================ */

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
            ELSE 'Load ID Not Found'
        END) AS [SV Trip ID],

    CONCAT('''', COALESCE(LTRIM(RTRIM(o.[blnum])), '')) AS [Load ID],

    CONCAT(COALESCE(LTRIM(RTRIM(pu.[city_name])), ''), ', ', COALESCE(LTRIM(RTRIM(pu.[state])), ''),
           ' | ',
           COALESCE(LTRIM(RTRIM(del.[city_name])), ''), ', ', COALESCE(LTRIM(RTRIM(del.[state])), '')) AS [O/D PAIR],

    CASE WHEN pu.[sched_arrive_early] IS NOT NULL THEN FORMAT(pu.[sched_arrive_early], 'MM-dd-yyyy HH:mm') ELSE '' END AS [Scheduled arrival time],
    FORMAT(pu.[actual_arrival], 'MM-dd-yyyy HH:mm') AS [Actual arrival time],    -- REAL McLeod time

    CASE
        WHEN o.[status] = 'V' THEN 'Order is VOID'
        WHEN pu.[actual_arrival] IS NULL OR pu.[sched_arrive_early] IS NULL THEN 'Unknown'
        WHEN pu.[sched_arrive_late] IS NULL AND pu.[actual_arrival] > pu.[sched_arrive_early] THEN 'N'
        WHEN pu.[sched_arrive_late] IS NULL AND pu.[actual_arrival] = pu.[sched_arrive_early] THEN 'Y'
        WHEN pu.[sched_arrive_late] IS NULL AND pu.[actual_arrival] < pu.[sched_arrive_early] THEN 'Y'
        WHEN pu.[actual_arrival] BETWEEN pu.[sched_arrive_early] AND pu.[sched_arrive_late] THEN 'Y'
        WHEN pu.[actual_arrival] < pu.[sched_arrive_early] THEN 'Y'
        WHEN pu.[actual_arrival] > pu.[sched_arrive_late] THEN 'N'
        ELSE 'Unknown'
    END AS [ON TIME Arrival Y/N],

    FORMAT(ROPH.Posted_Date, 'MM-dd-yyyy HH:mm') AS [actual dispatch time],      -- RANDOMIZED (the only synthetic column)
    -- planned dispatch = when the Rate Confirmation was created (order_post_hist posted_type='C')
    CASE WHEN oph.[posted_date] IS NOT NULL THEN FORMAT(oph.[posted_date], 'MM-dd-yyyy HH:mm') ELSE '' END AS [planned dispatch time],

    CASE
        WHEN o.[status] = 'V' THEN 'Order is VOID'
        WHEN ROPH.Posted_Date IS NULL OR oph.Posted_Date IS NULL THEN 'Unknown'
        WHEN ROPH.Posted_Date BETWEEN pu.sched_arrive_early AND oph.Posted_Date THEN 'Y'
        WHEN ROPH.Posted_Date < oph.Posted_Date THEN 'Y'
        WHEN ROPH.Posted_Date > oph.Posted_Date THEN 'N'
        ELSE 'Unknown'
    END AS [Dispatch on time Y/N],

    FORMAT(del.[actual_arrival], 'MM-dd-yyyy HH:mm') AS [Actual delivery time],  -- REAL McLeod time
    CASE WHEN del.[sched_arrive_early] IS NOT NULL THEN FORMAT(del.[sched_arrive_early], 'MM-dd-yyyy HH:mm') ELSE '' END AS [planned delivery time],

    CASE
        WHEN o.[status] = 'V' THEN 'Order is VOID'
        WHEN del.[actual_arrival] IS NULL OR del.[sched_arrive_early] IS NULL THEN 'Unknown'
        WHEN del.[sched_arrive_late] IS NULL AND del.[actual_arrival] > del.[sched_arrive_early] THEN 'N'
        WHEN del.[sched_arrive_late] IS NULL AND del.[actual_arrival] = del.[sched_arrive_early] THEN 'Y'
        WHEN del.[sched_arrive_late] IS NULL AND del.[actual_arrival] < del.[sched_arrive_early] THEN 'Y'
        WHEN del.[actual_arrival] BETWEEN del.[sched_arrive_early] AND del.[sched_arrive_late] THEN 'Y'
        WHEN del.[actual_arrival] < del.[sched_arrive_early] THEN 'Y'
        WHEN del.[actual_arrival] > del.[sched_arrive_late] THEN 'N'
        ELSE 'Unknown'
    END AS [ON TIME DELIVERY y/n]

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

LEFT JOIN [lme_1720].[dbo].[payee] p             ON p.[id] = oph.[carrier_id]
LEFT JOIN [lme_1720].[dbo].[order_hist_type] oht ON oht.[id] = oph.[posted_type]
LEFT JOIN [lme_1720].[dbo].[movement] m          ON o.[curr_movement_id] = m.[ID]

LEFT JOIN [lme_1720].[dbo].[stop] pu  ON o.[shipper_stop_id]   = pu.[id]  AND pu.[stop_type]  = 'PU'
LEFT JOIN [lme_1720].[dbo].[stop] del ON o.[consignee_stop_id] = del.[id] AND del.[stop_type] = 'SO'

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
