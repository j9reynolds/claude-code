-- DGL Command Center — news aggregation storage.
-- Run against the Command Center application database on DGLIQ.
-- The aggregator's MERGE is insert-only, so re-running the job never
-- rewrites history; PublishedUtc drives display order and pruning.

IF OBJECT_ID('dbo.NewsItem', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.NewsItem
    (
        ItemId        char(32)       NOT NULL CONSTRAINT PK_NewsItem PRIMARY KEY,
        Source        nvarchar(100)  NOT NULL,
        Title         nvarchar(500)  NOT NULL,
        Link          nvarchar(2000) NOT NULL,
        Summary       nvarchar(500)  NULL,
        PublishedUtc  datetime2(0)   NULL,
        Tags          nvarchar(400)  NULL,   -- comma-separated tag names
        IngestedUtc   datetime2(0)   NOT NULL CONSTRAINT DF_NewsItem_Ingested DEFAULT SYSUTCDATETIME()
    );

    CREATE INDEX IX_NewsItem_PublishedUtc ON dbo.NewsItem (PublishedUtc DESC);
END
GO

-- Retention: keep 90 days. Add to the existing nightly maintenance job.
-- DELETE dbo.NewsItem WHERE PublishedUtc < DATEADD(day, -90, SYSUTCDATETIME());

-- Grants: the portal reads, the ETL gMSA writes.
-- GRANT SELECT ON dbo.NewsItem TO [portal-app-role];
-- GRANT SELECT, INSERT ON dbo.NewsItem TO [DELTA\svc-dglcc-etl$];
