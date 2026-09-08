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

   ⚠️ DATA-INTEGRITY CAVEAT — ALL THREE "actual" timestamps are RANDOMIZED and
   NON-DETERMINISTIC. Each is the real McLeod value minus a random 55–67 minutes via
   NEWID():
       RandomArrival  = pu.actual_arrival  − rand(55–67m)   -> drives OTP
       RandomDelivery = del.actual_arrival − rand(55–67m)   -> drives OTD
       ROPH.Posted_Date = rate-con posted_date − rand(55–67m) -> drives Dispatch
   Consequences: (a) every metric is computed against a time shifted ~1h EARLIER than
   McLeod recorded, so OTP/OTD/Dispatch read systematically BETTER than actual;
   (b) results CHANGE on every run and do not reconcile against McLeod on audit;
   (c) Dispatch is ~100% by construction (actual is always before planned). For a number
   self-certified to a partner (J.B. Hunt), consider computing on-time from the REAL
   McLeod actual_arrival / actual_departure instead of a randomized value.

   Query preserved as run by Ops.
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
    FORMAT(R.RandomArrival, 'MM-dd-yyyy HH:mm') AS [Actual arrival time],       -- RANDOMIZED (see caveat)

    CASE
        WHEN o.[status] = 'V' THEN 'Order is VOID'
        WHEN R.RandomArrival IS NULL OR pu.[sched_arrive_early] IS NULL THEN 'Unknown'
        WHEN pu.[sched_arrive_late] IS NULL AND R.RandomArrival > pu.[sched_arrive_early] THEN 'N'
        WHEN pu.[sched_arrive_late] IS NULL AND R.RandomArrival = pu.[sched_arrive_early] THEN 'Y'
        WHEN pu.[sched_arrive_late] IS NULL AND R.RandomArrival < pu.[sched_arrive_early] THEN 'Y'
        WHEN R.RandomArrival BETWEEN pu.[sched_arrive_early] AND pu.[sched_arrive_late] THEN 'Y'
        WHEN R.RandomArrival < pu.[sched_arrive_early] THEN 'Y'
        WHEN R.RandomArrival > pu.[sched_arrive_late] THEN 'N'
        ELSE 'Unknown'
    END AS [ON TIME Arrival Y/N],

    FORMAT(ROPH.Posted_Date, 'MM-dd-yyyy HH:mm') AS [actual dispatch time],      -- RANDOMIZED (see caveat)
    CASE WHEN oph.[posted_date] IS NOT NULL THEN FORMAT(oph.[posted_date], 'MM-dd-yyyy HH:mm') ELSE '' END AS [planned dispatch time],

    CASE
        WHEN o.[status] = 'V' THEN 'Order is VOID'
        WHEN ROPH.Posted_Date IS NULL OR oph.Posted_Date IS NULL THEN 'Unknown'
        WHEN ROPH.Posted_Date BETWEEN pu.sched_arrive_early AND oph.Posted_Date THEN 'Y'
        WHEN ROPH.Posted_Date < oph.Posted_Date THEN 'Y'
        WHEN ROPH.Posted_Date > oph.Posted_Date THEN 'N'
        ELSE 'Unknown'
    END AS [Dispatch on time Y/N],

    FORMAT(RDEL.RandomDelivery, 'MM-dd-yyyy HH:mm') AS [Actual delivery time],   -- RANDOMIZED (see caveat)
    CASE WHEN del.[sched_arrive_early] IS NOT NULL THEN FORMAT(del.[sched_arrive_early], 'MM-dd-yyyy HH:mm') ELSE '' END AS [planned delivery time],

    CASE
        WHEN o.[status] = 'V' THEN 'Order is VOID'
        WHEN RDEL.RandomDelivery IS NULL OR del.[sched_arrive_early] IS NULL THEN 'Unknown'
        WHEN del.[sched_arrive_late] IS NULL AND RDEL.RandomDelivery > del.[sched_arrive_early] THEN 'N'
        WHEN del.[sched_arrive_late] IS NULL AND RDEL.RandomDelivery = del.[sched_arrive_early] THEN 'Y'
        WHEN del.[sched_arrive_late] IS NULL AND RDEL.RandomDelivery < del.[sched_arrive_early] THEN 'Y'
        WHEN RDEL.RandomDelivery BETWEEN del.[sched_arrive_early] AND del.[sched_arrive_late] THEN 'Y'
        WHEN RDEL.RandomDelivery < del.[sched_arrive_early] THEN 'Y'
        WHEN RDEL.RandomDelivery > del.[sched_arrive_late] THEN 'N'
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

LEFT JOIN [lme_1720].[dbo].[stop] pu ON o.[shipper_stop_id] = pu.[id] AND pu.[stop_type] = 'PU'
CROSS APPLY (
    SELECT CASE WHEN pu.[actual_arrival] IS NOT NULL
                THEN DATEADD(MINUTE, -(ABS(CHECKSUM(NEWID())) % 13 + 55), pu.[actual_arrival])
                ELSE NULL END AS RandomArrival
) AS R

LEFT JOIN [lme_1720].[dbo].[stop] del ON o.[consignee_stop_id] = del.[id] AND del.[stop_type] = 'SO'
CROSS APPLY (
    SELECT CASE WHEN del.[actual_arrival] IS NOT NULL
                THEN DATEADD(MINUTE, -(ABS(CHECKSUM(NEWID())) % 13 + 55), del.[actual_arrival])
                ELSE NULL END AS RandomDelivery
) AS RDEL

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
