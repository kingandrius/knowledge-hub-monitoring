-- Give the API's system-assigned managed identity data access only.
-- Replace app-kh-api-XXXX with the web app name (the identity has the same name).
-- Must be run by a Microsoft Entra login (the Entra admin), not by khsqladmin.
CREATE USER [app-kh-api-XXXX] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [app-kh-api-XXXX];
ALTER ROLE db_datawriter ADD MEMBER [app-kh-api-XXXX];
GO
-- Check:
SELECT dp.name, dp.type_desc, r.name AS role_name
FROM sys.database_role_members rm
JOIN sys.database_principals r  ON rm.role_principal_id = r.principal_id
JOIN sys.database_principals dp ON rm.member_principal_id = dp.principal_id
WHERE dp.name = 'app-kh-api-XXXX';
