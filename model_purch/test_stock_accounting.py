import sqlite3
from django.test import SimpleTestCase
from .services.erp_plan_parameters import SQL


class OutdoorStockAccountingTests(SimpleTestCase):
    def stock_query(self):
        # Execute the actual stock aggregation; replace only the SQL Server
        # table function and qualified source names with local fixture tables.
        query=SQL.split('), stock AS (',1)[1].split('\n)\nSELECT',1)[0]
        query=query.replace('FROM parameters dates\n CROSS APPLY DataWH.erp.t_Даичи_ПервичнаяСебестоимостьOnDate(dates.DateFrom) s','FROM stock_rows s')
        return query.replace('DataWH.erp.[Справочники.Номенклатура]','goods').replace('DataWH.dbo.ModelCodeERP','models')

    def database(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE goods (Даичи_ГруппаПланирования, Наименование, ОбъемЧислитель)')
        db.execute('CREATE TABLE models (ModelCode, FlagAccountingQtyBA)')
        db.execute('CREATE TABLE stock_rows (Номенклатура, Количество, DDP_USD)')
        return db

    def test_indoor_cost_quantity_and_volume_do_not_affect_outdoor_average(self):
        db=self.database()
        db.executemany('INSERT INTO goods VALUES (?,?,?)',[
            ('Mixed','Outdoor A',1),('Mixed','Outdoor B',2),('Mixed','Indoor',.5),('Mixed','Unknown',10)])
        db.executemany('INSERT INTO models VALUES (?,?)',[
            ('Outdoor A',1),('Outdoor B',1),('Indoor',0),('Unknown',None)])
        db.executemany('INSERT INTO stock_rows VALUES (?,?,?)',[
            ('Outdoor A',10,1000),('Outdoor B',5,1000),('Indoor',100,50000),('Unknown',10,90000)])
        group,amount,volume,quantity=db.execute(self.stock_query()).fetchone()
        self.assertEqual((group,amount,volume,quantity),('Mixed',2000,20,15))
        self.assertAlmostEqual(amount/quantity,133.33333333333333)

    def test_indoor_only_and_zero_outdoor_quantity_do_not_produce_stock_prices(self):
        db=self.database()
        db.executemany('INSERT INTO goods VALUES (?,?,?)',[
            ('Indoor group','Indoor',1),('Zero group','Outdoor',1)])
        db.executemany('INSERT INTO models VALUES (?,?)',[('Indoor',0),('Outdoor',1)])
        db.executemany('INSERT INTO stock_rows VALUES (?,?,?)',[('Indoor',10,1000),('Outdoor',0,500)])
        self.assertEqual(db.execute(self.stock_query()).fetchall(),[])
