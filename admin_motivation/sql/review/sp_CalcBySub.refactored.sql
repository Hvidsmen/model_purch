USE [DataWH]
GO
/****** Object:  StoredProcedure [motivation].[sp_CalcBySub]    Script Date: 08.10.2026 16:59:27 ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
-- Candidate for staging validation, not installed by the portal.
-- Defaults reproduce the year/version actually consumed by the original report.
CREATE OR ALTER PROCEDURE [motivation].[sp_CalcBySub]
    @Year int = 2024,
    @PlanVersion nvarchar(255) = N'План продаж [Q1_2024] v4'
AS
BEGIN
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @Year NOT BETWEEN 2001 AND 9998 THROW 50001, N'Недопустимый год расчёта.', 1;
IF NULLIF(LTRIM(RTRIM(@PlanVersion)), N'') IS NULL THROW 50002, N'Не задана версия плана.', 1;
IF @@TRANCOUNT <> 0 THROW 50003, N'Запускайте процедуру вне внешней транзакции.', 1;
DECLARE @DateFrom date = DATEFROMPARTS(@Year, 1, 1);
DECLARE @DateTo date = DATEFROMPARTS(@Year + 1, 1, 1);
BEGIN TRY
    BEGIN TRANSACTION;
    DECLARE @LockResult int;
    EXEC @LockResult = sys.sp_getapplock
        @Resource = N'motivation.sp_CalcBySub', @LockMode = 'Exclusive',
        @LockOwner = 'Transaction', @LockTimeout = 0;
    IF @LockResult < 0 THROW 50004, N'Расчёт мотивации уже выполняется.', 1;
 

IF OBJECT_ID('tempdb..#document_sales') IS NOT NULL
	DROP TABLE #document_sales

SELECT 
	Ссылка
	,DocumentKey
	,Номенклатура
	,ВидЦены
	,ДаичиТипСкидкиНаДату
	,даичи_ВидЦеныВходнаяСтоимостьНаименование
	,ROW_ID
	,Даичи_АвторСделкиНаименование
	,СпособДоставки
	,[даичи_УсловияДоставки]
INTO #document_sales
FROM (
	SELECT 
		rtu.Ссылка
		,DataWH.dbo.makeDocumentKeyDate(rtu.Дата,rtu.Номер) DocumentKey
		,rtut.Номенклатура
		,ВидЦены
		,COALESCE(даичи_ВидЦеныВходнаяСтоимостьНаименование, '') даичи_ВидЦеныВходнаяСтоимостьНаименование
		,ДаичиТипСкидкиНаДату
		,ROW_NUMBER() OVER(PARTITION BY DataWH.dbo.makeDocumentKeyDate(rtu.Дата,rtu.Номер), rtut.Номенклатура ORDER BY НомерСтроки) ROW_ID
		,rtu.Даичи_АвторСделкиНаименование
		,zk.СпособДоставки
		,zk.[даичи_УсловияДоставки]
	FROM 
		DataWH.erp.[Документы.РеализацияТоваровУслуг] rtu
		INNER JOIN DataWH.erp.[Документы.РеализацияТоваровУслугТовары] rtut
			ON rtu.Ссылка = rtut.СсылкаНаДокумент
		LEFT JOIN DataWH.erp.[Документы.ЗаказКлиента] zk
			ON rtu.ЗаказКлиента_Многосостав = zk.Ссылка
) t
WHERE 
	ROW_ID = 1
UNION ALL
SELECT	
Ссылка
	,DocumentKey
	,Номенклатура
	,ВидЦены
	,ДаичиТипСкидкиНаДату
	,даичи_ВидЦеныВходнаяСтоимостьНаименование
	,ROW_ID
	,Даичи_АвторСделкиНаименование
	,'' СпособДоставки
	,'' [даичи_УсловияДоставки]
FROM (
SELECT 
		kr.Ссылка
		,DataWH.dbo.makeDocumentKeyDate(kr.Дата,kr.Номер) DocumentKey
		,krt.Номенклатура
		,ВидЦены
		,COALESCE(даичи_ВидЦеныВходнаяСтоимостьНаименование, '') даичи_ВидЦеныВходнаяСтоимостьНаименование
		,ДаичиТипСкидкиНаДату
		,ROW_NUMBER() OVER(PARTITION BY DataWH.dbo.makeDocumentKeyDate(kr.Дата,kr.Номер), krt.Номенклатура ORDER BY krt.НомерСтроки) ROW_ID
		,rtu.Даичи_АвторСделкиНаименование
FROM 
	DataWH.erp.[Документы.КорректировкаРеализации] kr
	INNER JOIN DataWH.erp.[Документы.КорректировкаРеализацииТовары]  krt
		ON kr.Ссылка = krt.Ссылка
	INNER JOIN DataWH.erp.[Документы.РеализацияТоваровУслугТовары] rtut
		ON rtut.СсылкаНаДокумент = kr.ДокументОснование_Многосостав
		AND krt.[КодСтроки] = rtut.КодСтроки
	INNER JOIN DataWH.erp.[Документы.РеализацияТоваровУслуг] rtu
			ON rtu.Ссылка = rtut.СсылкаНаДокумент
	
) t
WHERE 
	ROW_ID = 1


IF EXISTS (SELECT 1 FROM #document_sales GROUP BY DocumentKey, Номенклатура HAVING COUNT_BIG(*) > 1)
    THROW 50005, N'Неоднозначное соответствие документа и товара.', 1;
CREATE INDEX IX_document_sales_match ON #document_sales(DocumentKey, Номенклатура);

UPDATE s
	SET s.[даичи_ВидЦеныВходнаяСтоимостьНаименование] =COALESCE(ds.[даичи_ВидЦеныВходнаяСтоимостьНаименование],'')
		,s.[ДаичиТипСкидкиНаДату] = COALESCE(ds.[ДаичиТипСкидкиНаДату],'')
		,s.[ВидЦены] = COALESCE(ds.[ВидЦены],'')
		,s.Даичи_АвторСделкиНаименование = ds.Даичи_АвторСделкиНаименование
	,s.[даичи_УсловияДоставки] = ds.[даичи_УсловияДоставки]
	,s.СпособДоставки = ds.СпособДоставки
FROM 
	DataWH.dbo.Sales s
	LEFT JOIN #document_sales ds
		ON s.DocumentKey = ds.DocumentKey
		AND s.ModelCodeERP = ds.Номенклатура




UPDATE s
	SET s.DiscountMotivation = 
	CASE 
	WHEN s.ВидЦены != '' and (1-s.Discount/100) =0 THEN s.Discount
			WHEN s.ВидЦены != '' and (s.Price - AmountUSDIt_ITW)/NULLIF(1-s.Discount/100.0,0) =0 THEN s.Discount
			WHEN s.ВидЦены != '' and (s.Price - AmountUSDIt_ITW)  =0 THEN s.Discount
			WHEN s.ВидЦены != '' AND s.Price - AmountUSDIt_ITW = 0  THEN s.Discount
			WHEN s.ВидЦены = '' AND  (phe.Price*DataWH.dbo.getCurrancyRateUSD(DateInvoice, phe.CurrencyCode)*Quantity) = 0 THEN  s.Discount
			WHEN s.ВидЦены != '' THEN (1-(s.Price - AmountUSDIt_ITW+ s.BallsOuterUSD)/NULLIF((s.Price - AmountUSDIt_ITW)/NULLIF(1-s.Discount/100.0,0),0))*100
			WHEN s.ВидЦены = '' AND phe.Price IS NOT NULL THEN (1- (s.Price - AmountUSDIt_ITW+ s.BallsOuterUSD)/ NULLIF(phe.Price*DataWH.dbo.getCurrancyRateUSD(DateInvoice, phe.CurrencyCode)*Quantity,0))*100
			ELSE s.Discount
		END
	
FROM 
	DataWH.dbo.Sales s
	LEFT JOIN DataWH.dbo.vPriceHistoryERP phe
		ON s.ModelCodeERP = phe.ModelCode
		AND s.[даичи_ВидЦеныВходнаяСтоимостьНаименование] = phe.PriceType
		AND s.DateInvoice BETWEEN phe.DateFrom AND phe.DateTo
		AND phe.Price !=0


DECLARE @default_coeff FLOAT = 0.71


IF OBJECT_ID('tempdb..#fact') IS NOT NULL
	DROP TABLE #fact

--calc fact
;WITH fact AS (
SELECT 
	sub.SubdivisionName
	,sub.Chanel
	,s.Subdivision
	,ClosedDealDate
	,p.PlanningGroupSalesErp
	,p.ModelCode
	,s.DocumentNumber
	,s.DateInvoice
	,CASE
		WHEN p.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN p.GroupERP
		ELSE ''
	END GroupERP
	,CASE
		WHEN p.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN BrandName
		ELSE ''
	END  [Марка(Бренд)]
	,'Политики' АвторСделки
	--,COALESCE(s.Даичи_АвторСделкиНаименование,'') АвторСделки
	,s.DiscountMotivation
	,ДаичиТипСкидкиНаДату
	,CASE
		WHEN s.DiscountMotivation >= COALESCE(sm.O4,10000) THEN 'O4'
		WHEN s.DiscountMotivation>= COALESCE(sm.O3,10000) AND s.DiscountMotivation < COALESCE(sm.O4,10000)  THEN 'O3'
		WHEN s.DiscountMotivation>= COALESCE(sm.O2,10000) AND s.DiscountMotivation < COALESCE(sm.O3,10000)  THEN 'O2'
		WHEN s.DiscountMotivation>= COALESCE(sm.O1,10000) AND s.DiscountMotivation < COALESCE(sm.O2,10000)  THEN 'O1'
		ELSE'O0'
	END Segment
	,sm.O0
	,sm.O1
	,sm.O2
	,sm.O3
	,sm.O4

	,CASE
		WHEN s.DiscountMotivation >= COALESCE(sm.O4,10000) THEN COALESCE(sc.K4,0.008*@default_coeff)
		WHEN s.DiscountMotivation>= COALESCE(sm.O3,10000) AND s.DiscountMotivation < COALESCE(sm.O4,10000)  THEN COALESCE(sc.K3,0.008*@default_coeff)
		WHEN s.DiscountMotivation>= COALESCE(sm.O2,10000) AND s.DiscountMotivation < COALESCE(sm.O3,10000)  THEN  COALESCE(sc.K2,0.008*@default_coeff)
		WHEN s.DiscountMotivation>= COALESCE(sm.O1,10000) AND s.DiscountMotivation < COALESCE(sm.O2,10000)  THEN COALESCE( sc.K1,0.008*@default_coeff)
		ELSE COALESCE(sc.K0,0.008*@default_coeff)
	END Coeff_
	,COALESCE(sc.K0,0.008*@default_coeff) K0
	,COALESCE(sc.K1,0.008*@default_coeff) K1
	,COALESCE(sc.K2,0.008*@default_coeff) K2 
	,COALESCE(sc.K3,0.008*@default_coeff) K3  
	,COALESCE(sc.K4,0.008*@default_coeff) K4  

	,CASE
		WHEN s.DiscountMotivation >= COALESCE(sm.O4,10000) THEN COALESCE(sc.K4,0.008*@default_coeff)
		WHEN s.DiscountMotivation>= COALESCE(sm.O3,10000) AND s.DiscountMotivation < COALESCE(sm.O4,10000)  THEN COALESCE(sc.K3,0.008*@default_coeff)
		WHEN s.DiscountMotivation>= COALESCE(sm.O2,10000) AND s.DiscountMotivation < COALESCE(sm.O3,10000)  THEN  COALESCE(sc.K2,0.008*@default_coeff)
		WHEN s.DiscountMotivation>= COALESCE(sm.O1,10000) AND s.DiscountMotivation < COALESCE(sm.O2,10000)  THEN COALESCE( sc.K1,0.008*@default_coeff)
		ELSE COALESCE(sc.K0,0.008*@default_coeff)
	END * (Price - AmountUSDIt_ITW + BallsOuterUSD) AmountUSDMotive
	,ВидЦены
	,[даичи_ВидЦеныВходнаяСтоимостьНаименование]
	,(Price - AmountUSDIt_ITW + BallsOuterUSD) AmountUSD
	,'Политики' ТипКоэф
	,c.CustomerKeyName [Партнер]
	,COALESCE(sc.VariationCalculate,
	
	CASE
		WHEN p.PlanningGroupSalesErp IN ('1. RAC','2. VRF', '3. PROF') THEN 'Группа планов продаж'
		ELSE 'Подразделение'
	END 
	) VariationCalculate
	,s.AmountExwAEUSD
	,CASE
		WHEN s.DiscountMotivation >= COALESCE(sm.O4,10000) THEN COALESCE(sc.K4,0.008*@default_coeff)
		WHEN s.DiscountMotivation>= COALESCE(sm.O3,10000) AND s.DiscountMotivation < COALESCE(sm.O4,10000)  THEN COALESCE(sc.K3,0.008*@default_coeff)
		WHEN s.DiscountMotivation>= COALESCE(sm.O2,10000) AND s.DiscountMotivation < COALESCE(sm.O3,10000)  THEN  COALESCE(sc.K2,0.008*@default_coeff)
		WHEN s.DiscountMotivation>= COALESCE(sm.O1,10000) AND s.DiscountMotivation < COALESCE(sm.O2,10000)  THEN COALESCE( sc.K1,0.008*@default_coeff)
		ELSE COALESCE(sc.K0,0.008*@default_coeff)
	END *(Price - AmountUSDIt_ITW + BallsOuterUSD)*s.PriceRUR /IIF(s.Price=0,1,s.Price)  AmountRURMotive
FROM 
	DataWH.dbo.Sales s
	INNER JOIN DataWH.dbo.Customer c
		ON s.CustomerKey = c.CustomerKey
	INNER JOIN DataWH.dbo.Subdivisions sub
		ON s.Subdivision = sub.SubdivisionKey
	INNER JOIN DataWH.dbo.Product p
		ON s.ProductCode = p.ProductCode
		AND [Тип номенклатуры] ='Товар'
	LEFT JOIN DataWH.motivation.vSegmentMotivationFromTO sm
		ON s.ClosedDealDate BETWEEN sm.DateFrom and sm.DateTo
		AND s.ДаичиТипСкидкиНаДату LIKE CONCAT('%',sm.TypeDiscount,'%')
	--		AND sm.TypeDiscount = 'NOne'
	LEFT JOIN  DataWH.motivation.vSubdivisionMotiveCoeffTo sc
		ON s.ClosedDealDate BETWEEN sc.DateFrom and sc.DateTo
		AND p.PlanningGroupSalesErp = sc.PGSales
		AND (CASE
				WHEN p.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN p.GroupERP
				ELSE '*'
			END)  =sc.Group_
		AND (CASE
				WHEN p.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN BrandName
				ELSE ''
			END) = sc.Brand	
		AND TRIM(sc.TypeCoeff) = 'Политики'
		AND sub.SubdivisionName = sc.Subdivision
WHERE 
	s.ClosedDealDate >= @DateFrom AND s.ClosedDealDate < @DateTo
UNION ALL
SELECT 
	sub.SubdivisionName
	,sub.Chanel
	,s.Subdivision
	,ClosedDealDate
	,p.PlanningGroupSalesErp
	,p.ModelCode
	,s.DocumentNumber
	,s.DateInvoice
	,CASE
		WHEN p.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN p.GroupERP
		ELSE ''
	END GroupERP
	,CASE
		WHEN p.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN BrandName
		ELSE ''
	END  [Марка(Бренд)]
	,'Продажи' АвторСделки
	,s.DiscountMotivation
	,ДаичиТипСкидкиНаДату
	,CASE
		WHEN s.DiscountMotivation >= COALESCE(sm.O4,10000) THEN 'O4'
		WHEN s.DiscountMotivation>= COALESCE(sm.O3,10000) AND s.DiscountMotivation < COALESCE(sm.O4,10000)  THEN 'O3'
		WHEN s.DiscountMotivation>= COALESCE(sm.O2,10000) AND s.DiscountMotivation < COALESCE(sm.O3,10000)  THEN 'O2'
		WHEN s.DiscountMotivation>= COALESCE(sm.O1,10000) AND s.DiscountMotivation < COALESCE(sm.O2,10000)  THEN 'O1'
		ELSE 'O0'
	END Segment
	,sm.O0
	,sm.O1
	,sm.O2
	,sm.O3
	,sm.O4

	,CASE
		WHEN s.DiscountMotivation >= COALESCE(sm.O4,10000) THEN COALESCE(sc.K4,0.008*(1-@default_coeff))
		WHEN s.DiscountMotivation>= COALESCE(sm.O3,10000) AND s.DiscountMotivation < COALESCE(sm.O4,10000)  THEN COALESCE(sc.K3,0.008*(1-@default_coeff))
		WHEN s.DiscountMotivation>= COALESCE(sm.O2,10000) AND s.DiscountMotivation < COALESCE(sm.O3,10000)  THEN  COALESCE(sc.K2,0.008*(1-@default_coeff))
		WHEN s.DiscountMotivation>= COALESCE(sm.O1,10000) AND s.DiscountMotivation < COALESCE(sm.O2,10000)  THEN COALESCE( sc.K1,0.008*(1-@default_coeff))
		ELSE COALESCE(sc.K0,0.008*(1-@default_coeff))
	END Coeff_
	,COALESCE(sc.K0,0.008*(1-@default_coeff)) K0
	,COALESCE(sc.K1,0.008*(1-@default_coeff)) K1
	,COALESCE(sc.K2,0.008*(1-@default_coeff)) K2 
	,COALESCE(sc.K3,0.008*(1-@default_coeff)) K3  
	,COALESCE(sc.K4,0.008*(1-@default_coeff)) K4  

	,CASE
		WHEN s.DiscountMotivation >= COALESCE(sm.O4,10000) THEN COALESCE(sc.K4,0.008*(1-@default_coeff))
		WHEN s.DiscountMotivation>= COALESCE(sm.O3,10000) AND s.DiscountMotivation < COALESCE(sm.O4,10000)  THEN COALESCE(sc.K3,0.008*(1-@default_coeff))
		WHEN s.DiscountMotivation>= COALESCE(sm.O2,10000) AND s.DiscountMotivation < COALESCE(sm.O3,10000)  THEN  COALESCE(sc.K2,0.008*(1-@default_coeff))
		WHEN s.DiscountMotivation>= COALESCE(sm.O1,10000) AND s.DiscountMotivation < COALESCE(sm.O2,10000)  THEN COALESCE( sc.K1,0.008*(1-@default_coeff))
		ELSE COALESCE(sc.K0,0.008*(1-@default_coeff))
	END * (Price - AmountUSDIt_ITW + BallsOuterUSD) AmountUSDMotive
	,ВидЦены
	,[даичи_ВидЦеныВходнаяСтоимостьНаименование]
	,(Price - AmountUSDIt_ITW + BallsOuterUSD) AmountUSD
	,'Продажи' ТипКоэф
	,c.CustomerKeyName [Партнер]
,COALESCE(sc.VariationCalculate,
	
	CASE
		WHEN p.PlanningGroupSalesErp IN ('1. RAC','2. VRF', '3. PROF') THEN 'Группа планов продаж'
		ELSE 'Подразделение'
	END 
	) VariationCalculate
	-- ,'Марка(Бренд)'
	,s.AmountExwAEUSD
	,CASE
		WHEN s.DiscountMotivation >= COALESCE(sm.O4,10000) THEN COALESCE(sc.K4,0.008*(1-@default_coeff))
		WHEN s.DiscountMotivation>= COALESCE(sm.O3,10000) AND s.DiscountMotivation < COALESCE(sm.O4,10000)  THEN COALESCE(sc.K3,0.008*(1-@default_coeff))
		WHEN s.DiscountMotivation>= COALESCE(sm.O2,10000) AND s.DiscountMotivation < COALESCE(sm.O3,10000)  THEN  COALESCE(sc.K2,0.008*(1-@default_coeff))
		WHEN s.DiscountMotivation>= COALESCE(sm.O1,10000) AND s.DiscountMotivation < COALESCE(sm.O2,10000)  THEN COALESCE( sc.K1,0.008*(1-@default_coeff))
		ELSE COALESCE(sc.K0,0.008*(1-@default_coeff))
	END * (Price - AmountUSDIt_ITW + BallsOuterUSD)*s.PriceRUR /IIF(s.Price=0,1,s.Price)  AmountRURMotive
FROM 
	DataWH.dbo.Sales s
	INNER JOIN DataWH.dbo.Customer c
		ON s.CustomerKey = c.CustomerKey
	INNER JOIN DataWH.dbo.Subdivisions sub
		ON s.Subdivision = sub.SubdivisionKey
	INNER JOIN DataWH.dbo.Product p
		ON s.ProductCode = p.ProductCode
		AND [Тип номенклатуры] ='Товар'
	LEFT JOIN DataWH.motivation.vSegmentMotivationFromTO sm
		ON s.ClosedDealDate BETWEEN sm.DateFrom and sm.DateTo
		AND s.ДаичиТипСкидкиНаДату LIKE CONCAT('%',sm.TypeDiscount,'%')
	--	AND sm.TypeDiscount = 'NOne'
LEFT JOIN  DataWH.motivation.vSubdivisionMotiveCoeffTo sc
		ON s.ClosedDealDate BETWEEN sc.DateFrom and sc.DateTo
		AND p.PlanningGroupSalesErp = sc.PGSales
		AND (CASE
				WHEN p.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN p.GroupERP
				ELSE '*'
			END)  =sc.Group_
		AND (CASE
				WHEN p.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN BrandName
				ELSE ''
			END) = sc.Brand	
		AND TRIM(sc.TypeCoeff) = 'Продажи'
		AND sub.SubdivisionName = sc.Subdivision
WHERE 
	s.ClosedDealDate >= @DateFrom AND s.ClosedDealDate < @DateTo
)

SELECT 
	SubdivisionName			Подразделение
	,Subdivision			ПодразделениеДокумент
	,Chanel					Канал
	,Year(ClosedDealDate)	Год
	,DATEPART(QUARTER,ClosedDealDate)	Квартал
	,MONTH(ClosedDealDate)				Месяц
	,ClosedDealDate						ДатаЗакрытияСделки
	,DateInvoice						ДатаОтгрузки
	,DocumentNumber						Номер
	,ModelCode							Модель
	,PlanningGroupSalesErp				ГруппаПлановПродаж
	,GroupERP							ГруппаТоваров
	,[Марка(Бренд)]						Марка
	,DiscountMotivation					ПроцентСкидки
	,ДаичиТипСкидкиНаДату				ТипСкидки
	,Segment							СегментСкидки
	,АвторСделки
	,O0		
	,O1
	,O2
	,O3
	,O4
	,Coeff_								КоэфициентМотивации
	,K0
	,K1
	,K2
	,K3
	,K4
	,SUM(AmountUSDMotive)				СуммаМотивацииUSD
	,SUM(AmountUSD)						СуммаОтгрузокUSD
	,0						СуммаПланUSD
	,ВидЦены
	,[даичи_ВидЦеныВходнаяСтоимостьНаименование]
	,ТипКоэф
	,[Партнер]
	,VariationCalculate
	,SUM(AmountExwAEUSD) AmountExwAEUSD
	,sum(AmountRURMotive)AmountRURMotive
INTO #fact
FROM 
	fact
GROUP BY 
	SubdivisionName
	,Chanel
	,Year(ClosedDealDate) 
	,MONTH(ClosedDealDate)
	,DATEPART(QUARTER,ClosedDealDate) 
	,ClosedDealDate
	,DateInvoice
	,DocumentNumber
	,ModelCode
	,PlanningGroupSalesErp
	,GroupERP
	,[Марка(Бренд)]
	,DiscountMotivation
	,ДаичиТипСкидкиНаДату
	,Segment
	,O0
	,O1
	,O2
	,O3
	,O4
	,Coeff_
	,K0
	,K1
	,K2
	,K3
	,K4
	,Subdivision
	,АвторСделки
	,ВидЦены
	,[даичи_ВидЦеныВходнаяСтоимостьНаименование]
	,ТипКоэф
	,[Партнер]
	,VariationCalculate

IF OBJECT_ID('DataWH.motivation.Fact') IS NOT NULL
	DROP TABLE DataWH.motivation.Fact

SELECT 
	*
	,СуммаМотивацииUSD*0.8								СуммаКвМотивацииUSD
	,СуммаМотивацииUSD*0.2								СуммаМотивацииUSDГод
	
INTO DataWH.motivation.Fact
FROM 
	#fact



--add plan
IF OBJECT_ID('tempdb..#percent_plan_menger') IS NOT NULL
	DROP TABLE #percent_plan_menger


SELECT 
	*
	,AmountUSD/NULLIF(SUM(AmountUSD) OVER(PARTITION BY 
		ПодразделениеДокумент
		,ГруппаПлановПродаж
		,ГруппаТоваров
		,Марка
		),0) Percent_
INTO #percent_plan_menger
FROM 
	(
	SELECT 
		ПодразделениеДокумент
		,'Политики' АвторСделки
		,ГруппаПлановПродаж
		,ГруппаТоваров
		,Марка
		,SUM(СуммаОтгрузокUSD) AmountUSD
	FROM 
		#fact
	WHERE 
		TRIM(ТипКоэф) = 'Политики'
	GROUP BY ПодразделениеДокумент
		,АвторСделки
		,ГруппаПлановПродаж
		,ГруппаТоваров
		,Марка
	HAVING 
		SUM(СуммаОтгрузокUSD)>0
	) t



IF OBJECT_ID('tempdb..#plan') IS NOT NULL
	DROP TABLE #plan

SELECT 
	 s.SubdivisionName[Подразделение]
      ,t.Subdivision  [ПодразделениеДокумент]
      ,s.Chanel [Канал]
      ,YEAR(t.Date_) [Год]
      ,DATEPART(QUARTER, t.Date_) [Квартал]
      ,MONTH(t.Date_) [Месяц]
      ,t.Date_ [ДатаЗакрытияСделки]
      ,t.Date_ [ДатаОтгрузки]
      ,'' [Номер]
      ,'' [Модель]
      ,t.PlanningGroupSalesERP [ГруппаПлановПродаж]
      ,t.GroupERP [ГруппаТоваров]
      ,t.[Марка(Бренд)] [Марка]
      ,NULL [ПроцентСкидки]
      ,NULL [ТипСкидки]
      ,NULL [СегментСкидки]
      ,COALESCE([АвторСделки],'Политики') [АвторСделки]
      ,NULL [O0]
      ,NULL [O2]
      ,NULL [O3]
      ,NULL [O4]
      ,NULL [КоэфициентМотивации]
      ,COALESCE(sc.[K0],0.008*(@default_coeff)) [K0]
      ,COALESCE(sc.[K1],0.008*(@default_coeff)) [K1]
      ,COALESCE(sc.[K2],0.008*(@default_coeff)) [K2]
      ,COALESCE(sc.[K3],0.008*(@default_coeff)) [K3]
      ,COALESCE(sc.[K4],0.008*(@default_coeff)) [K4]
      ,0 [СуммаМотивацииUSD]
      ,0 [СуммаОтгрузокUSD]
      ,t.AmountUSD*COALESCE(pm.Percent_,1) [СуммаПланUSD]
	  ,NULL ВидЦены
	  ,NULL [даичи_ВидЦеныВходнаяСтоимостьНаименование]
	  ,TRIM(sc.TypeCoeff) TypeCoeff_
	  ,COALESCE(sc.VariationCalculate,
	
	CASE
		WHEN t.PlanningGroupSalesErp IN ('1. RAC','2. VRF', '3. PROF') THEN 'Группа планов продаж'
		ELSE 'Подразделение'
	END 
	) VariationCalculate
INTO #plan
FROM 
	(
SELECT 
	Subdivision
	,PlanningGroupSalesERP
	,Date_

	,CASE
		WHEN g.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN g.GroupERP
		ELSE ''
	END GroupERP
	,CASE
		WHEN g.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN BrandName
		ELSE ''
	END  [Марка(Бренд)]
	,SUM(AmountUSD) AmountUSD
FROM 
	DataWH.planning.PlanSales ps
	INNER JOIN DataWH.planning.Goods g
		on ps.GoodsKey = g.PlanningKey
WHERE 
	ps.Version_ = @PlanVersion
    AND ps.Date_ >= @DateFrom AND ps.Date_ < @DateTo
GROUP BY 
	Subdivision
	,PlanningGroupSalesERP
	,Date_
	,CASE
		WHEN g.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN g.GroupERP
		ELSE ''
	END 
	,CASE
		WHEN g.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN BrandName
		ELSE ''
	END 
	
)  t
INNER JOIN DataWH.dbo.Subdivisions s
	ON t.Subdivision = s.SubdivisionKey
LEFT JOIN #percent_plan_menger pm
	ON s.SubdivisionKey = pm.ПодразделениеДокумент
	AND t.PlanningGroupSalesERP = pm.ГруппаПлановПродаж
	AND t.GroupERP = pm.ГруппаТоваров
	AND t.[Марка(Бренд)] = pm.Марка

LEFT JOIN  DataWH.motivation.vSubdivisionMotiveCoeffTo sc
		ON t.Date_ BETWEEN sc.DateFrom and sc.DateTo
		AND t.PlanningGroupSalesErp = sc.PGSales
		AND (CASE
				WHEN t.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN t.GroupERP
				ELSE '*'
			END)  =sc.Group_
		AND (CASE
				WHEN t.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN [Марка(Бренд)]
				ELSE ''
			END) = sc.Brand	
		AND TRIM(sc.TypeCoeff) = 'Политики'
		AND s.SubdivisionName = sc.Subdivision
UNION ALL

SELECT 
	 s.SubdivisionName[Подразделение]
      ,t.Subdivision  [ПодразделениеДокумент]
      ,s.Chanel [Канал]
      ,YEAR(t.Date_) [Год]
      ,DATEPART(QUARTER, t.Date_) [Квартал]
      ,MONTH(t.Date_) [Месяц]
      ,t.Date_ [ДатаЗакрытияСделки]
      ,t.Date_ [ДатаОтгрузки]
      ,'' [Номер]
      ,'' [Модель]
      ,t.PlanningGroupSalesERP [ГруппаПлановПродаж]
      ,t.GroupERP [ГруппаТоваров]
      ,t.[Марка(Бренд)] [Марка]
      ,NULL [ПроцентСкидки]
      ,NULL [ТипСкидки]
      ,NULL [СегментСкидки]
      ,'Продажи' [АвторСделки]
      ,NULL [O0]
      ,NULL [O2]
      ,NULL [O3]
      ,NULL [O4]
      ,NULL [КоэфициентМотивации]
      ,COALESCE(sc.[K0],0.008*(1-@default_coeff)) [K0]
      ,COALESCE(sc.[K1],0.008*(1-@default_coeff)) [K1]
      ,COALESCE(sc.[K2],0.008*(1-@default_coeff)) [K2]
      ,COALESCE(sc.[K3],0.008*(1-@default_coeff)) [K3]
      ,COALESCE(sc.[K4],0.008*(1-@default_coeff)) [K4]
      ,0 [СуммаМотивацииUSD]
      ,0 [СуммаОтгрузокUSD]
      ,t.AmountUSD [СуммаПланUSD]
	  ,NULL ВидЦены
	  ,NULL [даичи_ВидЦеныВходнаяСтоимостьНаименование]
	  ,TRIM(sc.TypeCoeff) TypeCoeff_
	  ,COALESCE(sc.VariationCalculate,
	
	CASE
		WHEN t.PlanningGroupSalesErp IN ('1. RAC','2. VRF', '3. PROF') THEN 'Группа планов продаж'
		ELSE 'Подразделение'
	END 
	) VariationCalculate
	 -- ,'Марка(Бренд)' VariationCalculate
FROM 
	(
SELECT 
	Subdivision
	,PlanningGroupSalesERP
	,Date_

	,CASE
		WHEN g.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN g.GroupERP
		ELSE ''
	END GroupERP
	,CASE
		WHEN g.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN BrandName
		ELSE ''
	END  [Марка(Бренд)]
	,SUM(AmountUSD) AmountUSD
FROM 
	DataWH.planning.PlanSales ps
	INNER JOIN DataWH.planning.Goods g
		on ps.GoodsKey = g.PlanningKey
WHERE 
	ps.Version_ = @PlanVersion
    AND ps.Date_ >= @DateFrom AND ps.Date_ < @DateTo
GROUP BY 
	Subdivision
	,PlanningGroupSalesERP
	,Date_
	,CASE
		WHEN g.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN g.GroupERP
		ELSE ''
	END 
	,CASE
		WHEN g.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN BrandName
		ELSE ''
	END 
	
)  t
INNER JOIN DataWH.dbo.Subdivisions s
	ON t.Subdivision = s.SubdivisionKey

LEFT JOIN  DataWH.motivation.vSubdivisionMotiveCoeffTo sc
		ON t.Date_ BETWEEN sc.DateFrom and sc.DateTo
		AND t.PlanningGroupSalesErp = sc.PGSales
		AND (CASE
				WHEN t.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN t.GroupERP
				ELSE '*'
			END)  =sc.Group_
		AND (CASE
				WHEN t.GroupERP IN ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') THEN t.[Марка(Бренд)]
				ELSE ''
			END) = sc.Brand	
		AND TRIM(sc.TypeCoeff) = 'Продажи'
		AND s.SubdivisionName = sc.Subdivision

		
IF OBJECT_ID('DataWH.motivation.PlanFact') IS NOT NULL
	DROP TABLE DataWH.motivation.PlanFact

SELECT		
	[Подразделение]
      ,[ПодразделениеДокумент]
      ,[Канал]
      ,[Год]
      ,[Квартал]
      ,[Месяц]
      ,[ДатаЗакрытияСделки]
      ,[ДатаОтгрузки]
      ,[Номер]
      ,[Модель]
      ,[ГруппаПлановПродаж]
      ,[ГруппаТоваров]
      ,[Марка]
      ,[ПроцентСкидки]
      ,[ТипСкидки]
      ,[СегментСкидки]
      ,[АвторСделки]
      ,[O0]
      ,[O2]
      ,[O3]
      ,[O4]
      ,[КоэфициентМотивации]
      ,[K0]
      ,[K1]
      ,[K2]
      ,[K3]
      ,[K4]
      ,[СуммаМотивацииUSD]
      ,[СуммаОтгрузокUSD]
      ,[СуммаПланUSD]
	  ,ВидЦены
		,[даичи_ВидЦеныВходнаяСтоимостьНаименование]
		,ТипКоэф
		,VariationCalculate
		,AmountRURMotive
INTO DataWH.motivation.PlanFact
FROM #fact 
UNION ALL

SELECT 
	[Подразделение]
      ,[ПодразделениеДокумент]
      ,[Канал]
      ,[Год]
      ,[Квартал]
      ,[Месяц]
      ,[ДатаЗакрытияСделки]
      ,[ДатаОтгрузки]
      ,[Номер]
      ,[Модель]
      ,[ГруппаПлановПродаж]
      ,[ГруппаТоваров]
      ,[Марка]
      ,[ПроцентСкидки]
      ,CAST([ТипСкидки] AS nvarchar(255))
      ,CAST([СегментСкидки] AS nvarchar(255))
      ,[АвторСделки]
      ,[O0]
      ,[O2]
      ,[O3]
      ,[O4]
      ,[КоэфициентМотивации]
      ,[K0]
      ,[K1]
      ,[K2]
      ,[K3]
      ,[K4]
      ,[СуммаМотивацииUSD]
      ,[СуммаОтгрузокUSD]
      ,[СуммаПланUSD]
	  ,CAST(ВидЦены AS nvarchar(255))
	  ,CAST([даичи_ВидЦеныВходнаяСтоимостьНаименование] AS nvarchar(255))
	  ,TRIM(TypeCoeff_) Тип
	  ,VariationCalculate
	  ,0
FROM 
	#plan
   


IF OBJECT_ID('tempdb..#pf_base') IS NOT NULL
	DROP TABLE #pf_base

SELECT 
	Подразделение
	,ПодразделениеДокумент
	,Канал
	,Год
	,Квартал
	,ГруппаПлановПродаж
	,ГруппаТоваров
	,Марка
	,АвторСделки
	,K0
	,K1
	,K2
	,k3
	,k4
	,VariationCalculate
	,CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN CONCAT(Подразделение,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров,Марка)
		WHEN VariationCalculate = 'Группа товаров' THEN CONCAT(Подразделение,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров)
		WHEN VariationCalculate = 'Группа планов продаж' THEN CONCAT(Подразделение,АвторСделки,ГруппаПлановПродаж)
		WHEN VariationCalculate = 'Подразделение' THEN CONCAT(Подразделение,АвторСделки)
		ELSE CONCAT(Подразделение,АвторСделки)
	END  KeyPlan
	,TRIM(COALESCE(ТипКоэф,IIF(АвторСделки='Продажи','Продажи','Политики'))) ТипКоэф
	,SUM(IIF(СегментСкидки='O0',СуммаОтгрузокUSD,0))	O0
	,SUM(IIF(СегментСкидки='O1',СуммаОтгрузокUSD,0))	O1
	,SUM(IIF(СегментСкидки='O2',СуммаОтгрузокUSD,0))	O2
	,SUM(IIF(СегментСкидки='O3',СуммаОтгрузокUSD,0))	O3
	,SUM(IIF(СегментСкидки='O4',СуммаОтгрузокUSD,0))	O4
	,SUM(IIF(СуммаМотивацииUSD*0.8<0,0,СуммаМотивацииUSD*0.8))								СуммаМотивацииUSD
	,SUM(IIF(СуммаМотивацииUSD*0.2<0,0,СуммаМотивацииUSD*0.2))								СуммаМотивацииUSDГод
	,SUM(СуммаОтгрузокUSD)								СуммаОтгрузокUSD
	,SUM(СуммаПланUSD)									СуммаПланUSD
	

	,SUM(IIF(AmountRURMotive*0.8<0,0,AmountRURMotive*0.8))								СуммаМотивацииRUR
	,SUM(IIF(AmountRURMotive*0.2<0,0,AmountRURMotive*0.2))								СуммаМотивацииRURГод
INTO #pf_base
FROM 
	DataWH.motivation.PlanFact

GROUP BY 
	Подразделение
	,ПодразделениеДокумент
	,Канал
	,Год
	,Квартал
	,ГруппаПлановПродаж
	,ГруппаТоваров
	,Марка
	,АвторСделки
	,K0
	,K1
	,K2
	,k3
	,k4
	,VariationCalculate
	,TRIM(COALESCE(ТипКоэф,IIF(АвторСделки='Продажи','Продажи','Политики')))
	,CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN CONCAT(Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка)
		WHEN VariationCalculate = 'Группа товаров' THEN CONCAT(Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров)
		WHEN VariationCalculate = 'Группа планов продаж' THEN CONCAT(Подразделение,АвторСделки,Год,ГруппаПлановПродаж)
		WHEN VariationCalculate = 'Подразделение' THEN CONCAT(Подразделение,АвторСделки)
		ELSE CONCAT(Подразделение,АвторСделки,Год)
	END

DELETE FROM #pf_base
WHERE 
 Канал ='ДПП'

 DELETE FROM #pf_base
WHERE 
 Подразделение IN ('ДПП','REtail','E-Com','СНГ','Д-Маврикий','Буфер','Брендинг')





--q1
IF OBJECT_ID('DataWH.motivation.ReportMotivationByDealAuthor') IS NOT NULL
	DROP TABLE  DataWH.motivation.ReportMotivationByDealAuthor
SELECT 
	Подразделение
	,ПодразделениеДокумент
	,Канал
	,Год
	,Квартал
	,ГруппаПлановПродаж
	,ГруппаТоваров
	,Марка
	,АвторСделки
	,K0
	,K1
	,K2
	,k3
	,k4
	,O0
	,O1
	,O2
	,O3
	,O4
	,СуммаМотивацииUSD
	,СуммаМотивацииUSD		СуммаМотивацииUSDУчетПроцента
	
	,SUM(СуммаОтгрузокUSD) OVER(PARTITION BY KeyPlan,Год,ТипКоэф)      СуммаОтгрузокUSD
	,SUM(СуммаПланUSD) OVER(PARTITION BY KeyPlan,Год,ТипКоэф)		СуммаПланUSD
	,'1 Квартал'			КварталМотивации
	,СуммаПланUSD			СуммаПланUSDКвартал
	,СуммаОтгрузокUSD		СуммаОтгрузокUSDКвартал
	,ТипКоэф
	,0 Остаток
	,VariationCalculate
	,СуммаПланUSD			СуммаПланUSDКварталFC
	,СуммаОтгрузокUSD		СуммаОтгрузокUSDКварталFC

	,СуммаПланUSD			СуммаПланUSDКварталValue
	,СуммаОтгрузокUSD		СуммаОтгрузокUSDКварталValue
	,СуммаМотивацииRUR
	,СуммаМотивацииRUR СуммаМотивацииRURУчетПроцента
	,CAST(0.0 AS FLOAT) ОстатокRUR
INTO DataWH.motivation.ReportMotivationByDealAuthor
FROM 
	#pf_base
WHERE 
	Квартал = 1
UNION 
SELECT 
	Подразделение
	,ПодразделениеДокумент
	,Канал
	,Год
	,Квартал
	,ГруппаПлановПродаж
	,ГруппаТоваров
	,Марка
	,АвторСделки
	,K0
	,K1
	,K2
	,k3
	,k4
	,IIF(Квартал=2,O0,0) O0
	,IIF(Квартал=2,O1,0) O1
	,IIF(Квартал=2,O2,0) O2
	,IIF(Квартал=2,O3,0) O3
	,IIF(Квартал=2,O4,0) O4
	,IIF(Квартал=2,СуммаМотивацииUSD,0) СуммаМотивацииUSD
	,IIF(
		CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END <0.8
		OR 
		SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,Год,ТипКоэф)/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,Год,ТипКоэф)
		<0.8
		or SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)<0.8
		,0
		,IIF(Квартал=2,СуммаМотивацииUSD,0)
	) СуммаМотивацииUSDУчетПроцента
	,CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
			 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				
		END  СуммаОтгрузокUSD
	,CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END   СуммаПланUSD
	,'2 Квартал(1-2 кв)' КварталМотивации

	,IIF(Квартал = 2,СуммаПланUSD,0)			СуммаПланUSDКвартал
	,IIF(Квартал = 2,СуммаОтгрузокUSD,0)		СуммаОтгрузокUSDКвартал
	,ТипКоэф
	,0 Остаток
	,VariationCalculate
	,IIF(Квартал = 2,СуммаПланUSD,0)			СуммаПланUSDКвартал
	,IIF(Квартал = 2,СуммаОтгрузокUSD,0)		СуммаОтгрузокUSDКвартал
	,СуммаПланUSD			СуммаПланUSDКварталValue
	,СуммаОтгрузокUSD		СуммаОтгрузокUSDКварталValue
	,IIF(Квартал=2,СуммаМотивацииRUR,0) СуммаМотивацииRUR
	,IIF(
		CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END <0.8
		OR 
		SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,Год,ТипКоэф)/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,Год,ТипКоэф)
		<0.8
		or SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)<0.8
		,0
		,IIF(Квартал=2,СуммаМотивацииRUR,0)
	) СуммаМотивацииRURУчетПроцента
	,0
FROM 
	#pf_base
WHERE 
	Квартал IN (1,2)

UNION ALL
SELECT 
	Подразделение
	,ПодразделениеДокумент
	,Канал
	,Год
	,Квартал
	,ГруппаПлановПродаж
	,ГруппаТоваров
	,Марка
	,АвторСделки
	,K0
	,K1
	,K2
	,k3
	,k4
	,O0
	,O1
	,O2
	,O3
	,O4
	,СуммаМотивацииUSD
	,IIF(
		CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END <0.8
		OR 
		SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,Год,ТипКоэф)/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,Год,ТипКоэф)
		<0.8
		or SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)<0.8
		,0
		,IIF(Квартал=3,СуммаМотивацииUSD,0)
	) СуммаМотивацииUSDУчетПроцента
	,CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
			 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				
		END  СуммаОтгрузокUSD
	,CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END   СуммаПланUSD
	,'3 Квартал' КварталМотивации

	,СуммаПланUSD			СуммаПланUSDКвартал
	,СуммаОтгрузокUSD		СуммаОтгрузокUSDКвартал
	,ТипКоэф
	,0 Остаток
	,VariationCalculate
	,СуммаПланUSD			СуммаПланUSDКвартал
	,СуммаОтгрузокUSD		СуммаОтгрузокUSDКвартал
	,СуммаПланUSD			СуммаПланUSDКварталValue
	,СуммаОтгрузокUSD		СуммаОтгрузокUSDКварталValue
	,IIF(Квартал=3,СуммаМотивацииRUR,0) СуммаМотивацииRUR
	,IIF(
		CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END <0.8
		OR 
		SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,Год,ТипКоэф)/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,Год,ТипКоэф)
		<0.8
		or SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)<0.8
		,0
		,IIF(Квартал=3,СуммаМотивацииRUR,0)
	) СуммаМотивацииRURУчетПроцента
	,0
FROM 
	#pf_base
WHERE 
	Квартал IN (3)
UNION ALL
SELECT 
	Подразделение
	,ПодразделениеДокумент
	,Канал
	,Год
	,Квартал
	,ГруппаПлановПродаж
	,ГруппаТоваров
	,Марка
	,АвторСделки
	,K0
	,K1
	,K2
	,k3
	,k4
	,IIF(Квартал=4,O0,0) O0
	,IIF(Квартал=4,O1,0) O1
	,IIF(Квартал=4,O2,0) O2
	,IIF(Квартал=4,O3,0) O3
	,IIF(Квартал=4,O4,0) O4
	,IIF(Квартал=4,СуммаМотивацииUSD,0)  СуммаМотивацииUSD
	,IIF(
		CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END <0.8
		OR 
		SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,Год,ТипКоэф)/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,Год,ТипКоэф)
		<0.8
		or SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)<0.8
		,0
		,IIF(Квартал=4,СуммаМотивацииUSD,0)
	) СуммаМотивацииUSDУчетПроцента
,CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
			 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				
		END  СуммаОтгрузокUSD
	,CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END   СуммаПланUSD
	,'4 Квартал(1-4 кв)' КварталМотивации

	,IIF(Квартал = 4,СуммаПланUSD,0)			СуммаПланUSDКвартал
	,IIF(Квартал = 4,СуммаОтгрузокUSD,0)		СуммаОтгрузокUSDКвартал
	,ТипКоэф
	,0 Остаток
	,VariationCalculate
	,IIF(Квартал = 4,СуммаПланUSD,0)			СуммаПланUSDКвартал
	,IIF(Квартал = 4,СуммаОтгрузокUSD,0)		СуммаОтгрузокUSDКвартал
	,СуммаПланUSD			СуммаПланUSDКварталValue
	,СуммаОтгрузокUSD		СуммаОтгрузокUSDКварталValue
	,IIF(Квартал=3,СуммаМотивацииRUR,0) СуммаМотивацииRUR
	,IIF(
		CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END <0.8
		OR 
		SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,Год,ТипКоэф)/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,Год,ТипКоэф)
		<0.8
		or SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)<0.8
		,0
		,IIF(Квартал=4,СуммаМотивацииRUR,0)
	) СуммаМотивацииRURУчетПроцента
	,0
FROM 
	#pf_base
WHERE 
	Квартал IN (1,2,3,4)
UNION ALL
SELECT 
	Подразделение
	,ПодразделениеДокумент
	,Канал
	,Год
	,Квартал
	,ГруппаПлановПродаж
	,ГруппаТоваров
	,Марка
	,АвторСделки
	,K0
	,K1
	,K2
	,k3
	,k4
	,O0
	,O1
	,O2
	,O3
	,O4
	,СуммаМотивацииUSDГод
	,IIF(
		CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END <0.8
		OR 
		SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,Год,ТипКоэф)/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,Год,ТипКоэф)
		<0.8
		or SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)<0.8
		,0
		,СуммаМотивацииUSDГод
	) СуммаМотивацииUSDУчетПроцента
	,CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
			 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				
		END  СуммаОтгрузокUSD
	,CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END   СуммаПланUSD
	,'Год' КварталМотивации

	,IIF(Квартал = 4,СуммаПланUSD,СуммаПланUSD)			СуммаПланUSDКвартал
	,IIF(Квартал = 4,СуммаОтгрузокUSD,СуммаОтгрузокUSD)		СуммаОтгрузокUSDКвартал
	,ТипКоэф
	,0 Остаток
	,VariationCalculate
	,0
	,0
	,СуммаПланUSD			СуммаПланUSDКварталValue
	,СуммаОтгрузокUSD		СуммаОтгрузокUSDКварталValue
	,СуммаМотивацииRURГод СуммаМотивацииRUR
	,IIF(
		CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END <0.8
		OR 
		SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,Год,ТипКоэф)/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,Год,ТипКоэф)
		<0.8
		or SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		/SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)<0.8
		,0
		,СуммаМотивацииRURГод
	) СуммаМотивацииRURУчетПроцента
	,0
FROM 
	#pf_base
WHERE 
	Квартал IN (1,2,3,4)

INSERT INTO  DataWH.motivation.ReportMotivationByDealAuthor
SELECT 
	Подразделение
	,ПодразделениеДокумент
	,Канал
	,Год
	,Квартал
	,ГруппаПлановПродаж
	,ГруппаТоваров
	,Марка
	,АвторСделки
	,K0
	,K1
	,K2
	,k3
	,k4
	,O0
	,O1
	,O2
	,O3
	,O4
	, 0 СуммаМотивацииUSDГод
	, 0 СуммаМотивацииUSDУчетПроцента
	,0 СуммаОтгрузокUSD
	,0  СуммаПланUSD
	,'Год' КварталМотивации

	,0		СуммаПланUSDКвартал
	,0		СуммаОтгрузокUSDКвартал
	,ТипКоэф
	,IIF(
		CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
				/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END <0.8
		OR 
		SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,Год,ТипКоэф)/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,Год,ТипКоэф)
		<0.8
		or SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)<0.8
		,0
		,IIF(СуммаМотивацииUSD - СуммаМотивацииUSDУчетПроцента<0,0,СуммаМотивацииUSD - СуммаМотивацииUSDУчетПроцента)
	)   Остаток
	,VariationCalculate
	,0
	,0
	,0
	,0
	,0
	,0
	,IIF(
		CASE
		WHEN VariationCalculate = 'Марка(Бренд)' THEN 
			SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф)
				/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,Марка,ТипКоэф) 
		WHEN VariationCalculate = 'Группа товаров' THEN
			SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
				/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ГруппаТоваров,ТипКоэф)
		WHEN VariationCalculate = 'Группа планов продаж' THEN 
			SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
				/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ГруппаПлановПродаж,ТипКоэф)
		WHEN VariationCalculate = 'Подразделение' THEN 
			SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		ELSE 
			SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
				/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		END <0.8
		OR 
		SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,Год,ТипКоэф)/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,Год,ТипКоэф)
		<0.8
		or SUM(СуммаОтгрузокUSDКварталFC) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
		/SUM(IIF(СуммаПланUSDКварталFC=0,1,СуммаПланUSDКварталFC)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)<0.8
		,0
		,IIF(CAST(СуммаМотивацииRUR AS FLOAT) - CAST(СуммаМотивацииRURУчетПроцента AS FLOAT)<0,0,CAST(СуммаМотивацииRUR AS FLOAT) - CAST(СуммаМотивацииRURУчетПроцента AS FLOAT))
	)   ОстатокRUR

FROM 
	DataWH.motivation.ReportMotivationByDealAuthor
WHERE КварталМотивации <> N'Год';

COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
END;
GO
