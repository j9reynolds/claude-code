/* ============================================================================
   Monthly Customer Performance Report — source query  (READ-ONLY)
   Target: DGLIQ warehouse, database DGL_TMS (schema tms) — the normalized McLeod replica.
   Confirmed via dgliq_describe_schema: tms.[Order], tms.Movement, tms.Stop, tms.Customer,
   tms.Carrier. Run on-network (scheduled monthly). Parameterize @customer + month.
   ============================================================================ */

DECLARE @mcleod_customer_id varchar(8) = 'UNITMETN';        -- e.g. USPS - PNT Office
DECLARE @month_start date = '2026-08-01';
DECLARE @month_end   date = EOMONTH(@month_start);

;WITH ord AS (
    SELECT  o.McLeodOrderId, o.McLeodCurrentMovementId,
            o.FreightCharge, o.OtherChargeTotal, o.TotalCharge, o.BillDate
    FROM    [DGL_TMS].[tms].[Order] o
    WHERE   o.McLeodCustomerId = @mcleod_customer_id
      AND   o.Status = 'D'                          -- delivered
      AND   o.BillDate BETWEEN @month_start AND @month_end
      AND   o.IsDeletedInSource = 0
),
mov AS (
    SELECT  m.McLeodOrderId, m.McLeodPayeeId, m.PayAmount, m.RateConfirmationSentDate
    FROM    [DGL_TMS].[tms].[Movement] m
),
/* on-time = delivery stop arrived on/before its scheduled-late appointment */
ontime AS (
    SELECT  s.McLeodOrderId,
            MAX(CASE WHEN s.StopType = 'SO' AND s.SchedArriveLate IS NOT NULL
                      AND s.ActualArrival > s.SchedArriveLate THEN 1 ELSE 0 END) AS delivered_late
    FROM    [DGL_TMS].[tms].[Stop] s
    GROUP BY s.McLeodOrderId
)
SELECT
    cu.Name                                             AS customer,
    @month_start                                        AS month,
    COUNT(*)                                            AS loads,
    SUM(ord.TotalCharge)                                AS revenue,
    SUM(ord.FreightCharge)                              AS freight,
    SUM(ord.OtherChargeTotal)                           AS other_charges,
    SUM(mov.PayAmount)                                  AS carrier_pay,
    SUM(ord.TotalCharge) - SUM(mov.PayAmount)           AS gross_margin,
    CAST(100.0 * (SUM(ord.TotalCharge) - SUM(mov.PayAmount))
         / NULLIF(SUM(ord.TotalCharge), 0) AS decimal(5,1)) AS margin_pct,
    CAST(SUM(ord.TotalCharge) / NULLIF(COUNT(*), 0) AS decimal(12,2)) AS avg_per_load,
    CAST(100.0 * SUM(CASE WHEN ISNULL(ot.delivered_late, 0) = 0 THEN 1 ELSE 0 END)
         / COUNT(*) AS decimal(5,1))                    AS on_time_pct,
    COUNT(DISTINCT mov.McLeodPayeeId)                   AS carrier_count
FROM        ord
LEFT JOIN   mov ON mov.McLeodOrderId = ord.McLeodOrderId
LEFT JOIN   ontime ot ON ot.McLeodOrderId = ord.McLeodOrderId
LEFT JOIN   [DGL_TMS].[tms].[Customer] cu ON cu.McLeodCustomerId = @mcleod_customer_id
GROUP BY    cu.Name;

/* ---- Top lanes (origin -> destination) for the same window ----------------- */
-- SELECT TOP 10
--   pu.CityName + ', ' + pu.StateCode + '  ->  ' + de.CityName + ', ' + de.StateCode AS lane,
--   COUNT(*) AS loads
-- FROM ord
-- JOIN [DGL_TMS].[tms].[Stop] pu ON pu.McLeodOrderId = ord.McLeodOrderId AND pu.StopType = 'PU'
-- JOIN [DGL_TMS].[tms].[Stop] de ON de.McLeodOrderId = ord.McLeodOrderId AND de.StopType = 'SO'
-- GROUP BY pu.CityName, pu.StateCode, de.CityName, de.StateCode
-- ORDER BY COUNT(*) DESC;

/* ---- Accessorials billed to the customer: use the other_charge extract (Query B of
        mcleod-extract) filtered to these orders — DGLIQ tms carries only the lump
        OtherChargeTotal, not itemized lines. ------------------------------------ */
