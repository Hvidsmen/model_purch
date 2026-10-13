SELECT 
*
,CASE WHEN VariationCalculate = 'Группа планов продаж' THEN СуммаОтгрузокUSD
  ELSE NULL	
END СуммаОтгрузокUSDI
,CASE WHEN VariationCalculate = 'Группа планов продаж' THEN СуммаПланUSD
  ELSE NULL	
END СуммаПланUSDI
FROM 
DataWH.motivation.ReportMotivationByDealAuthor
WHERE 
Подразделение IN (@Subdivision)
AND ГруппаПлановПродаж  IN (@gp_sales)
AND COALESCE(ГруппаТоваров,'') IN (@GPGoods)
AND Марка iN (@Brend)
AND ТипКоэф IN (@type_coeff)