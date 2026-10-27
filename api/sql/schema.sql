-- Knowledge Hub CS2 - monitoring table in sqldb-kh-knowledgehub.
-- Run once as the Microsoft Entra admin (see sql/README.md). Safe to re-run.
IF OBJECT_ID(N'dbo.measurements', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.measurements (
        id           BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_measurements PRIMARY KEY,
        hostname     NVARCHAR(253) NOT NULL,
        ip_address   VARCHAR(45)   NOT NULL,
        metric       VARCHAR(50)   NOT NULL,
        value        FLOAT         NOT NULL,
        unit         VARCHAR(16)   NULL,
        measured_at  DATETIME2(0)  NOT NULL,   -- UTC, from the client
        received_at  DATETIME2(0)  NOT NULL
            CONSTRAINT df_measurements_received_at DEFAULT SYSUTCDATETIME()
    );
    -- One row per host, metric and moment: a resent batch cannot create duplicates.
    CREATE UNIQUE INDEX ux_measurements_host_metric_time
        ON dbo.measurements (hostname, metric, measured_at)
        WITH (IGNORE_DUP_KEY = ON);
END;
GO
