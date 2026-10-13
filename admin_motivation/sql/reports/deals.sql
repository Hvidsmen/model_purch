SELECT 
	*
FROM 
	DataWH.motivation.Fact
WHERE 
	АвторСделки IN (@ManagerName)
                AND Подразделение = @Division
AND Канал IN (@Channel)
AND ТипКоэф = @TypeCoeff
AND ДатаЗакрытияСделки BETWEEN @StartDate AND @EndDate
AND ГруппаПлановПродаж IN (@GPSales)
AND ГруппаТоваров IN (@GPGoods)
AND Марка IN (@Brend)
AND Партнер IN (@Partner)