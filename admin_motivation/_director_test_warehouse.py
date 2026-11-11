"""Synthetic DWH fixture executing financial SELECTs; not a production connector.

SQLite executes the actual aggregation/segment SQL with dialect adaptation.
SQL Server catalog, schema XML and application locks are deliberately simulated.
No live credentials, staff names or warehouse procedure definitions are used.
"""
import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from .services.prepared_sales import RULE as PREPARED_RULE
from .services.recalculation_2025 import RULE_VERSION
from .services.director_scenario_inputs import BRANCHES


class Warehouse:
    def __init__(self):
        self.directory=TemporaryDirectory(prefix='director-e2e-')
        self.path=Path(self.directory.name)/'warehouse.sqlite3'
        self.calls=[];self.connections=[];self.lock_result=0
        self.source_run=str(uuid4());self.prepared_run=str(uuid4())
        connection=self.open()
        connection.executescript('''
 CREATE TABLE Subdivisions(SubdivisionKey TEXT PRIMARY KEY,SubdivisionName TEXT COLLATE CI,Chanel TEXT);
 CREATE TABLE Product(ProductCode TEXT PRIMARY KEY,PlanningGroupSalesErp TEXT,GroupERP TEXT,
 BrandName TEXT,ModelCode TEXT,[Тип номенклатуры] TEXT);
 CREATE TABLE Customer(CustomerKey TEXT PRIMARY KEY,CustomerKeyName TEXT);
 CREATE TABLE PortalPreparedSales(Subdivision TEXT,ProductCode TEXT,CustomerKey TEXT,ClosedDealDate TEXT,
 DocumentNumber TEXT,DateInvoice TEXT,DiscountMotivation NUMERIC,ДаичиТипСкидкиНаДату TEXT,
 ВидЦены TEXT,даичи_ВидЦеныВходнаяСтоимостьНаименование TEXT,Price NUMERIC,AmountUSDIt_ITW NUMERIC,BallsOuterUSD NUMERIC);
 CREATE TABLE FactTest2025(Подразделение TEXT COLLATE CI,ПодразделениеДокумент TEXT,Год INT,Квартал INT,
 ГруппаПлановПродаж TEXT,ГруппаТоваров TEXT,Марка TEXT,СегментСкидки TEXT,СуммаОтгрузокUSD NUMERIC,ТипКоэф TEXT);
 CREATE TABLE RunJournal(RunId TEXT,State_ TEXT,Parameters_ TEXT,CompletedAt TEXT,StartedAt TEXT);
 CREATE TABLE PreparedReady(RunId TEXT,CompletedAt TEXT,State_ TEXT,Rule_ TEXT,PreparedFrom TEXT,PreparedTo TEXT,SourceSchema TEXT);
 INSERT INTO Product VALUES('P','1. RAC','Бытовые кондиционеры','Brand','Model','Товар'),
 ('Mobile','1. RAC','Мобильные кондиционеры','Manufacturer','Mobile model','Товар'),
 ('Heat','4. HEAT','Heat','Manufacturer','Heat model','Товар'),
 ('Service','1. RAC','Бытовые кондиционеры','Brand','Service','Услуга');
 INSERT INTO Customer VALUES('C','Synthetic customer');
 ''')
        connection.executemany('INSERT INTO Subdivisions VALUES (?,?,?)',
            [('M',BRANCHES[0],'Dealer'),('T',BRANCHES[1].lower(),'ДПП'),('Other','Other','Dealer')])
        for key,branch,amount in [('M',BRANCHES[0],500),('T',BRANCHES[1].lower(),1500)]:
            for q in range(1,5):
                connection.execute('INSERT INTO FactTest2025 VALUES (?,?,2025,?,?,?,?,?,?,?)',
                    (branch,key,q,'1. RAC','Бытовые кондиционеры','Brand','O0',amount,'Продажи'))
            for month in (1,4,7):self.sale(connection,key,amount,day=f'2026{month:02d}15',doc=f'{key}-{month}')
        # These must never enter 2026 Q1-Q3 premiums.
        self.sale(connection,'M',9000,day='20261001',doc='Q4')
        self.sale(connection,'Other',9000,day='20260115',doc='Other')
        self.sale(connection,'M',9000,day='20260115',doc='Service',product='Service')
        params={'variant':'test_2025','fact_year':2025,'plan_version':'P2025','quarter_share':'80',
            'year_share':'20','payout_threshold':'.8','recalculation_rule':RULE_VERSION}
        connection.execute('INSERT INTO RunJournal VALUES (?,?,?,?,?)',
            (self.source_run,'completed',json.dumps(params),'2026-11-11 00:00:01','2026-11-11 00:00:00'))
        connection.execute('INSERT INTO PreparedReady VALUES (?,?,?,?,?,?,?)',
            (self.prepared_run,'2026-11-11 00:00:00','completed',PREPARED_RULE,'20250101','20270101','fixture-schema'))
        connection.commit();connection.close()

    def open(self):
        connection=sqlite3.connect(self.path)
        connection.create_collation('CI',lambda a,b:(a.casefold()>b.casefold())-(a.casefold()<b.casefold()))
        connection.create_function('DATEPART',2,lambda part,day:(int(day[4:6])-1)//3+1)
        return connection

    @staticmethod
    def sale(connection,branch,amount,day='20260115',doc='Doc',product='P',discount=1,kind='MASS'):
        # Exercising the actual Price - ITW + Balls base is intentional.
        connection.execute('INSERT INTO PortalPreparedSales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (branch,product,'C',day,doc,day,discount,kind,'Retail','Retail',amount+10,20,10))

    def change(self,sql,params=()):
        connection=self.open()
        try:connection.execute(sql,params);connection.commit()
        finally:connection.close()

    def connector(self,*args):
        connection=Connection(self,self.open());self.connections.append(connection)
        return connection,connection.cursor

    def close(self):self.directory.cleanup()


class Connection:
    timeout=0
    def __init__(self,warehouse,db):self.warehouse=warehouse;self.db=db;self.closed=False;self.cursor=Cursor(self)
    def close(self):self.db.close();self.closed=True


class Cursor:
    def __init__(self,connection):self.connection=connection;self.active=None;self.fixed=[]
    def execute(self,sql,*params):
        w=self.connection.warehouse;db=self.connection.db;w.calls.append(sql)
        self.active=None;self.fixed=[]
        if 'FROM sys.columns c' in sql:
            for name in params:
                schema,table=name.split('.')
                columns=db.execute('PRAGMA table_info("'+table+'")').fetchall()
                self.fixed.extend((schema,table,row[1]) for row in columns)
            return self
        if 'sp_getapplock' in sql:self.fixed=[(w.lock_result,)];return self
        if 'OBJECT_ID' in sql and 'PortalPreparedSalesActive' not in sql and 'PortalMotivationRun' in sql:
            self.fixed=[(1,)];return self
        if sql.lstrip().startswith('SELECT OBJECT_ID(') and 'PortalPreparedSalesActive' in sql:
            self.fixed=[(1,1,1)];return self
        if 'FROM motivation.PortalMotivationRun' in sql:
            self.active=db.execute("SELECT RunId,State_,Parameters_,CompletedAt FROM RunJournal WHERE json_extract(Parameters_,'$.variant')=? ORDER BY StartedAt DESC LIMIT 1",params);return self
        if 'PortalPreparedSalesActive a' in sql:
            self.active=db.execute("SELECT RunId,CompletedAt FROM PreparedReady WHERE State_='completed' AND Rule_=? AND PreparedFrom<='20260101' AND PreparedTo>='20270101' AND SourceSchema='fixture-schema'",params);return self
        adapted=re.sub(r'\b(?:DataWH\.)?(?:dbo|motivation)\.', '',sql)
        adapted=adapted.replace("N'","'").replace('#portal_director_segments','director_segments')
        adapted=adapted.replace('CREATE TABLE director_segments','CREATE TEMP TABLE director_segments')
        if 'DATEPART(QUARTER,' in adapted:
            adapted=adapted.replace('DATEPART(QUARTER,',"DATEPART('QUARTER',")
            pattern=r" OUTER APPLY \(SELECT COUNT\(\*\) AS matches,[\s\S]*?\) m"
            match=re.search(pattern,adapted)
            if not match:raise AssertionError('Unknown projection APPLY dialect')
            adapted=adapted[:match.start()]+adapted[match.end():]
            predicate=" FROM director_segments cfg WHERE f.discount_type LIKE '%' || cfg.type_discount || '%'"
            adapted=adapted.replace('m.matches','(SELECT COUNT(*)'+predicate+') AS matches')
            for key in ('o1','o2','o3','o4'):
                adapted=adapted.replace('m.'+key,'(SELECT MAX('+key+')'+predicate+')')
        self.active=db.execute(adapted,params);return self

    def executemany(self,sql,rows):
        self.connection.warehouse.calls.append(sql)
        adapted=sql.replace('#portal_director_segments','director_segments')
        self.connection.db.executemany(adapted,[tuple(str(v) if isinstance(v,Decimal) else v for v in row) for row in rows])
        return self

    def fetchone(self):return self.active.fetchone() if self.active else self.fixed.pop(0) if self.fixed else None
    def fetchall(self):return self.active.fetchall() if self.active else list(self.fixed)
