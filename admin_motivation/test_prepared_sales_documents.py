"""Exercise the producer's actual attribute projection on synthetic documents."""
import sqlite3
from django.test import SimpleTestCase
from .services.prepared_sales import DOCUMENT_VALUES,procedure_sql,DDL

COLUMNS=('Ссылка','DocumentKey','Номенклатура','ВидЦены','ДаичиТипСкидкиНаДату',
 'даичи_ВидЦеныВходнаяСтоимостьНаименование','Даичи_АвторСделкиНаименование',
 'СпособДоставки','даичи_УсловияДоставки','ROW_ID')


class PreparedDocumentValuesTests(SimpleTestCase):
    def project(self,rows):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE document_sales ('+','.join('"'+key+'"' for key in COLUMNS)+')')
            db.executemany('INSERT INTO document_sales VALUES ('+','.join('?' for _ in COLUMNS)+')',rows)
            projection=DOCUMENT_VALUES.split('INTO #portal_document_sales_values',1)[0]
            db.execute('CREATE TABLE prepared_values AS '+projection+' FROM document_sales')
            values=db.execute('SELECT * FROM prepared_values').fetchall()
            conflicts=db.execute('SELECT DocumentKey,Номенклатура FROM prepared_values GROUP BY DocumentKey,Номенклатура HAVING COUNT(*)>1').fetchall()
            return values,conflicts

    def base(self,**changes):
        row=dict(zip(COLUMNS,('reference-1','document-1','product-1','Retail','Promo','USD','Actor','Delivery','Terms',1)))
        row.update(changes)
        return tuple(row[key] for key in COLUMNS)

    def test_different_technical_reference_and_row_id_do_not_make_a_conflict(self):
        values,conflicts=self.project([self.base(),self.base(Ссылка='reference-2',ROW_ID=2)])
        self.assertEqual(len(values),1);self.assertEqual(conflicts,[])
        self.assertEqual(values[0][2:6],('Retail','Promo','USD','Actor'))

    def test_null_and_empty_are_equivalent_only_where_update_already_coalesces_them(self):
        values,conflicts=self.project([
            self.base(ВидЦены=None,ДаичиТипСкидкиНаДату=None,даичи_ВидЦеныВходнаяСтоимостьНаименование=None),
            self.base(Ссылка='other',ВидЦены='',ДаичиТипСкидкиНаДату='',даичи_ВидЦеныВходнаяСтоимостьНаименование='')])
        self.assertEqual(len(values),1);self.assertEqual(values[0][2:5],('','',''));self.assertEqual(conflicts,[])

    def test_each_distinct_business_attribute_keeps_conflict_and_never_picks_first(self):
        for field in COLUMNS[3:9]:
            with self.subTest(field=field):
                values,conflicts=self.project([self.base(),self.base(**{field:'Other'})])
                self.assertEqual(len(values),2);self.assertEqual(conflicts,[('document-1','product-1')])

    def test_nullable_author_and_delivery_are_not_silently_normalized(self):
        for field in COLUMNS[6:9]:
            with self.subTest(field=field):
                values,conflicts=self.project([self.base(**{field:None}),self.base(**{field:''})])
                self.assertEqual(len(values),2);self.assertTrue(conflicts)

    def test_binary_delivery_references_keep_original_bytes(self):
        values,conflicts=self.project([self.base(СпособДоставки=b'AB'),self.base(СпособДоставки=b'ab')])
        self.assertTrue(conflicts)
        self.assertEqual({row[6] for row in values},{b'AB',b'ab'})

    def test_different_products_remain_separate(self):
        values,conflicts=self.project([self.base(),self.base(Номенклатура='product-2',ВидЦены='Wholesale')])
        self.assertEqual(len(values),2);self.assertEqual(conflicts,[])

    def test_real_conflicts_are_logged_before_throw_and_before_publication(self):
        sql=procedure_sql()
        self.assertIn('PortalPreparedSalesConflict',DDL)
        self.assertIn('LEFT JOIN #portal_document_sales_values ds',sql)
        self.assertNotIn('LEFT JOIN #document_sales ds',sql)
        log=sql.index('INSERT INTO motivation.PortalPreparedSalesConflict')
        stop=sql.index('THROW 50005,@ConflictMessage,1')
        publish=sql.index('BEGIN TRANSACTION;')
        self.assertLess(log,stop);self.assertLess(stop,publish)
        self.assertIn('SELECT TOP(20) @RunId',sql)
        self.assertIn('ProductXml,VariantsXml',sql)
        self.assertNotIn('PARTITION BY DocumentKey,Номенклатура ORDER BY Ссылка',sql)
