import sqlite3
from django.test import SimpleTestCase
from .services.erp_plan_parameters import SQL


class StockAccountingTests(SimpleTestCase):
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

    def test_all_stock_cost_is_divided_by_flagged_quantity(self):
        db=self.database()
        db.executemany('INSERT INTO goods VALUES (?,?,?)',[
            ('Mixed','Outdoor A',1),('Mixed','Outdoor B',2),('Mixed','Indoor',.5)])
        db.executemany('INSERT INTO models VALUES (?,?)',[
            ('Outdoor A',1),('Outdoor B',1),('Indoor',0)])
        db.executemany('INSERT INTO stock_rows VALUES (?,?,?)',[
            ('Outdoor A',10,4000),('Outdoor B',5,3000),('Indoor',15,1500)])
        group,amount,volume,quantity=db.execute(self.stock_query()).fetchone()
        self.assertEqual((group,amount,volume,quantity),('Mixed',8500,27.5,15))
        self.assertAlmostEqual(amount/quantity,566.6666666666666)

    def test_indoor_only_and_zero_outdoor_quantity_do_not_produce_stock_prices(self):
        db=self.database()
        db.executemany('INSERT INTO goods VALUES (?,?,?)',[
            ('Indoor group','Indoor',1),('Zero group','Outdoor',1)])
        db.executemany('INSERT INTO models VALUES (?,?)',[('Indoor',0),('Outdoor',1)])
        db.executemany('INSERT INTO stock_rows VALUES (?,?,?)',[('Indoor',10,1000),('Outdoor',0,500)])
        self.assertEqual(db.execute(self.stock_query()).fetchall(),[])

    def test_unknown_quantity_flag_does_not_remove_stock_cost(self):
        db=self.database()
        db.executemany('INSERT INTO goods VALUES (?,?,?)', [('Mixed','Outdoor',1), ('Mixed','Unknown',.5)])
        db.executemany('INSERT INTO models VALUES (?,?)', [('Outdoor',1), ('Unknown',None)])
        db.executemany('INSERT INTO stock_rows VALUES (?,?,?)', [('Outdoor',10,4000), ('Unknown',10,2000)])
        _, amount, volume, quantity=db.execute(self.stock_query()).fetchone()
        self.assertEqual((amount, volume, quantity), (6000,15,10))
        self.assertEqual(amount/quantity,600)
