-- Read-only diagnostics. No recalculation, procedure changes or cache clearing.
USE DataWH;

SELECT o.name AS [Процедура],
 CASE WHEN m.definition IS NULL THEN N'Нет определения/права VIEW DEFINITION'
 WHEN CHARINDEX(N'-- Portal: consume the published prepared sales snapshot.',m.definition)>0
 THEN N'Подготовленный факт подключён' ELSE N'Подключение не подтверждено' END AS [Источник],
 CASE WHEN CHARINDEX(N'Подготовленный факт: строк=',m.definition)>0
 THEN N'Новая диагностика копирования установлена' ELSE N'Прежняя версия процедуры' END AS [Версия подготовки]
FROM sys.objects o LEFT JOIN sys.sql_modules m ON m.object_id=o.object_id
WHERE o.schema_id=SCHEMA_ID(N'motivation')
 AND o.name IN (N'PortalCalcBySub',N'PortalCalcAll2026',N'PortalCalcSeparate2026');

IF OBJECT_ID(N'motivation.PortalMotivationRun',N'U') IS NOT NULL
 SELECT TOP(30) JSON_VALUE(Parameters_,'$.variant') AS [Вариант],
 DATEADD(HOUR,3,StartedAt) AS [Начало МСК],DATEADD(HOUR,3,CompletedAt) AS [Завершение МСК],
 DATEDIFF(SECOND,StartedAt,CompletedAt) AS [Секунд],State_ AS [Состояние]
 FROM motivation.PortalMotivationRun
 WHERE StartedAt>=DATEADD(DAY,-3,SYSUTCDATETIME()) ORDER BY StartedAt DESC;

SELECT j.name AS [Задание],
 CASE WHEN CHARINDEX(N'EXEC motivation.PortalCalcBySub',s.command)>0 THEN 1 ELSE 0 END AS [Полный основной],
 CASE WHEN CHARINDEX(N'EXEC motivation.PortalCalcAll2026',s.command)>0 THEN 1 ELSE 0 END AS [Все кварталы],
 CASE WHEN CHARINDEX(N'EXEC motivation.PortalCalcSeparate2026',s.command)>0 THEN 1 ELSE 0 END AS [Раздельный]
FROM msdb.dbo.sysjobs j JOIN msdb.dbo.sysjobsteps s ON s.job_id=j.job_id
WHERE j.name LIKE N'PortalMotivation[_]%' AND j.name<>N'PortalMotivation_PrepareSales';
