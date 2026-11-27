"""Insert posted deltas after original rate selection, before all eligibility inputs."""
import json
import re
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from .pilot_formula import mask_sql

DDL="""
IF OBJECT_ID(N'motivation.PortalFaoRows',N'U') IS NULL
 CREATE TABLE motivation.PortalFaoRows(Id bigint NOT NULL,Line_ int NOT NULL,DocumentId bigint NOT NULL,Year_ int NOT NULL,Payload nvarchar(max) NOT NULL,
 CONSTRAINT PK_PortalFaoRows PRIMARY KEY(Id,Line_));
IF NOT EXISTS(SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'motivation.PortalFaoRows') AND name=N'IX_PortalFaoYearDocument')
 CREATE INDEX IX_PortalFaoYearDocument ON motivation.PortalFaoRows(Year_,DocumentId);
IF OBJECT_ID(N'motivation.PortalFaoDocuments',N'U') IS NULL
 CREATE TABLE motivation.PortalFaoDocuments(DocumentId bigint NOT NULL,SourceYear int NOT NULL,Subdivision nvarchar(255) NOT NULL,Number_ nvarchar(255) NOT NULL,DocumentDate date NOT NULL,Kind_ nvarchar(30) NOT NULL,NativeUSD decimal(38,10) NOT NULL,PremiumUSD decimal(38,10) NOT NULL,PremiumRUB decimal(38,10) NOT NULL,PRIMARY KEY(DocumentId,SourceYear,Kind_));
"""
MARKER='-- Portal FAO: original rates, correction-date facts.'


def sync(cursor):
    from ..models import FaoCorrection
    cursor.execute(DDL)
    cursor.execute('DELETE FROM motivation.PortalFaoRows; DELETE FROM motivation.PortalFaoDocuments;')
    rows=[];documents={}
    for item in FaoCorrection.objects.filter(review__state='completed').select_related('document').order_by('pk').iterator(chunk_size=100):
        if item.document_id not in documents:
            from decimal import Decimal
            source_rows=item.document.rows
            by_kind_year={}
            for original in source_rows:
                key=(int(str(original['ДатаЗакрытияСделки'])[:4]),original['ТипКоэф'])
                values=by_kind_year.setdefault(key,[Decimal(0),Decimal(0),Decimal(0)])
                for i,field in enumerate(('СуммаОтгрузокUSD','СуммаМотивацииUSD','AmountRURMotive')):
                    values[i]+=Decimal(str(original.get(field) or 0))
            documents[item.document_id]=[(item.document_id,year,source_rows[0]['Подразделение'],item.document.number,item.document.document_date,kind,*values) for (year,kind),values in by_kind_year.items()]
        for index,source in enumerate(item.payload):
            payload=dict(source)
            payload['Номер']='ФАО#'+str(item.pk)
            if len(payload['Номер'])>255:raise ValidationError('Номер ОЗС слишком длинный для безопасной маркировки корректировки.')
            rows.append((item.pk,index,item.document_id,item.correction_date.year,json.dumps(payload,cls=DjangoJSONEncoder,ensure_ascii=False)))
            if len(rows)>=1000:
                cursor.executemany('INSERT INTO motivation.PortalFaoRows(Id,Line_,DocumentId,Year_,Payload) VALUES(?,?,?,?,?)',rows)
                rows=[]
    metadata=[row for values in documents.values() for row in values]
    if metadata:cursor.executemany('INSERT INTO motivation.PortalFaoDocuments(DocumentId,SourceYear,Subdivision,Number_,DocumentDate,Kind_,NativeUSD,PremiumUSD,PremiumRUB) VALUES(?,?,?,?,?,?,?,?,?)',metadata)
    if rows:cursor.executemany('INSERT INTO motivation.PortalFaoRows(Id,Line_,DocumentId,Year_,Payload) VALUES(?,?,?,?,?)',rows)


OVERLAY=r"""
-- Portal FAO: original rates, correction-date facts.
IF OBJECT_ID(N'motivation.PortalFaoRows',N'U') IS NOT NULL
 AND EXISTS(SELECT 1 FROM motivation.PortalFaoRows WHERE Year_=@FactYear)
BEGIN
 IF OBJECT_ID(N'tempdb..#portal_fao_original') IS NOT NULL DROP TABLE #portal_fao_original;
 SELECT d.DocumentId,d.SourceYear,d.Kind_,SUM(f.[СуммаОтгрузокUSD]) AS NativeUSD,
  SUM(f.[СуммаМотивацииUSD]) AS PremiumUSD,SUM(f.AmountRURMotive) AS PremiumRUB
 INTO #portal_fao_original
 FROM motivation.PortalFaoDocuments d JOIN #fact f
  ON f.[Подразделение]=d.Subdivision AND f.[Номер]=d.Number_
   AND CAST(f.[ДатаОтгрузки] AS date)=d.DocumentDate AND LTRIM(RTRIM(f.[ТипКоэф]))=d.Kind_
 WHERE d.SourceYear=@FactYear AND EXISTS(SELECT 1 FROM motivation.PortalFaoRows r WHERE r.DocumentId=d.DocumentId AND r.Year_=@FactYear)
 GROUP BY d.DocumentId,d.SourceYear,d.Kind_;
 IF EXISTS(SELECT 1 FROM motivation.PortalFaoDocuments d
  LEFT JOIN #portal_fao_original actual ON actual.DocumentId=d.DocumentId AND actual.SourceYear=d.SourceYear AND actual.Kind_=d.Kind_
  WHERE d.SourceYear=@FactYear AND EXISTS(SELECT 1 FROM motivation.PortalFaoRows r WHERE r.DocumentId=d.DocumentId AND r.Year_=@FactYear)
   AND EXISTS(SELECT 1 FROM DataWH.motivation.PortalCalculationSubdivisions scope WHERE scope.SubdivisionName=d.Subdivision)
   AND (actual.NativeUSD IS NULL OR ABS(actual.NativeUSD-d.NativeUSD)>0.00001 OR ABS(COALESCE(actual.PremiumUSD,0)-d.PremiumUSD)>0.00001 OR ABS(COALESCE(actual.PremiumRUB,0)-d.PremiumRUB)>0.00001))
  THROW 50022,N'Исходный факт или ставки ОЗС изменились после проверки ФАО. Повторное применение дельты запрещено; требуется сверка исходного снимка.',1;
 DROP TABLE #portal_fao_original;
 IF EXISTS(SELECT 1 FROM motivation.PortalFaoRows r
  CROSS APPLY(SELECT COUNT(DISTINCT c.column_id) AS Matched FROM OPENJSON(r.Payload) j
   JOIN tempdb.sys.columns c ON c.object_id=OBJECT_ID(N'tempdb..#fact')
    AND c.name COLLATE Latin1_General_100_BIN2=j.[key] COLLATE Latin1_General_100_BIN2) schema_check
  WHERE r.Year_=@FactYear AND EXISTS(SELECT 1 FROM DataWH.motivation.PortalCalculationSubdivisions scope WHERE scope.SubdivisionName=JSON_VALUE(r.Payload,N'$."Подразделение"')) AND schema_check.Matched<>(SELECT COUNT(*) FROM tempdb.sys.columns WHERE object_id=OBJECT_ID(N'tempdb..#fact')))
  THROW 50023,N'Структура исходного снимка ФАО не совпадает с фактом. Корректировки не применены.',1;
 DECLARE @faoNumberLength int;
 SELECT @faoNumberLength=CASE WHEN c.max_length=-1 THEN 2147483647 WHEN t.name IN(N'nvarchar',N'nchar') THEN c.max_length/2 ELSE c.max_length END
 FROM tempdb.sys.columns c JOIN tempdb.sys.types t ON c.user_type_id=t.user_type_id
 WHERE c.object_id=OBJECT_ID(N'tempdb..#fact') AND c.name=N'Номер';
 IF EXISTS(SELECT 1 FROM motivation.PortalFaoRows r WHERE r.Year_=@FactYear AND LEN(JSON_VALUE(r.Payload,N'$."Номер"'))>@faoNumberLength AND EXISTS(SELECT 1 FROM DataWH.motivation.PortalCalculationSubdivisions scope WHERE scope.SubdivisionName=JSON_VALUE(r.Payload,N'$."Подразделение"')))
  THROW 50024,N'Номер корректировки ФАО не помещается в структуру факта. Расчёт остановлен без усечения номера.',1;
 DECLARE @faoCols nvarchar(max),@faoSchema nvarchar(max),@faoInsert nvarchar(max);
 SELECT @faoCols=STUFF((SELECT N','+QUOTENAME(c.name) FROM tempdb.sys.columns c
  WHERE c.object_id=OBJECT_ID(N'tempdb..#fact') ORDER BY c.column_id FOR XML PATH(''),TYPE).value('.','nvarchar(max)'),1,1,N'');
 SELECT @faoSchema=STUFF((SELECT N','+QUOTENAME(c.name)+N' '+
  CASE WHEN t.name IN(N'nvarchar',N'nchar') THEN t.name+N'('+CASE WHEN c.max_length=-1 THEN N'max' ELSE CONVERT(nvarchar(10),c.max_length/2) END+N')'
   WHEN t.name IN(N'varchar',N'char',N'varbinary',N'binary') THEN t.name+N'('+CASE WHEN c.max_length=-1 THEN N'max' ELSE CONVERT(nvarchar(10),c.max_length) END+N')'
   WHEN t.name IN(N'decimal',N'numeric') THEN t.name+N'('+CONVERT(nvarchar(10),c.precision)+N','+CONVERT(nvarchar(10),c.scale)+N')'
   ELSE t.name END+N' ''$."'+REPLACE(REPLACE(c.name,N'\',N'\\'),N'"',N'\"')+N'"'''
  FROM tempdb.sys.columns c JOIN tempdb.sys.types t ON t.user_type_id=c.user_type_id
  WHERE c.object_id=OBJECT_ID(N'tempdb..#fact') ORDER BY c.column_id FOR XML PATH(''),TYPE).value('.','nvarchar(max)'),1,1,N'');
 IF @faoCols IS NULL OR @faoSchema IS NULL THROW 50021,N'Не определена структура факта для корректировок ФАО.',1;
 SET @faoInsert=N'INSERT INTO #fact('+@faoCols+N') SELECT '+@faoCols+
  N' FROM motivation.PortalFaoRows f CROSS APPLY OPENJSON(f.Payload) WITH ('+@faoSchema+N') p WHERE f.Year_=@year AND EXISTS (SELECT 1 FROM DataWH.motivation.PortalCalculationSubdivisions scope WHERE scope.SubdivisionName=p.[Подразделение])';
 EXEC sys.sp_executesql @faoInsert,N'@year int',@year=@FactYear;
END;
"""


def patch(definition,variant='main'):
    if MARKER in definition:return definition
    code=mask_sql(definition)
    table={'main':'Fact','all_2026':'FactAll2026','separate_2026':'FactSeparate2026'}[variant]
    matches=list(re.finditer(r"\bIF\s+OBJECT_ID\s*\(\s*N?'DataWH\.motivation\."+table+r"'\s*(?:,\s*N?'U'\s*)?\)\s+IS\s+NOT\s+NULL",definition,re.I))
    # Verify the anchor really is code, not an example inside a string/comment.
    matches=[m for m in matches if re.match(r'\bIF\s+OBJECT_ID',code[m.start():],re.I)]
    if len(matches)!=1:raise ValidationError('Не распознано место применения корректировок ФАО. Расчёт отменён.')
    pos=matches[0].start()
    overlay=OVERLAY.replace('PortalCalculationSubdivisions','PortalSeparateSubdivisions') if variant=='separate_2026' else OVERLAY
    return definition[:pos]+overlay+'\n'+definition[pos:]
