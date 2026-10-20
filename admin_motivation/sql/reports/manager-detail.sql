SELECT *
FROM DataWH.motivation.ReportMotivationByDealAuthor
WHERE [Подразделение] IN (@Subdivision)
  AND [АвторСделки] IN (@DealAuthor)
  AND [ТипКоэф] = @TypeCoeff_
  AND [ГруппаПлановПродаж] IN (@gp_sales)
  AND [ГруппаТоваров] IN (@GPGoods)
  AND [Марка] IN (@Brend)
  AND [Год] IN (@Years)
