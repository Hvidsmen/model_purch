from django.test import SimpleTestCase
from .services.team_target_layout import layout


class TargetLayoutTests(SimpleTestCase):
    def row(self,kind,periods):
        return {'kind':kind,'year':2026,'subdivision':'Москва','title':'Всё подразделение' if kind=='Все' else 'По коэффициентам','display_targets':[{'label':p,'remaining':100} for p in periods]}

    def test_periods_share_rows_and_missing_period_does_not_shift_next(self):
        result=layout({'department_remaining_targets':[self.row('Все',['1 кв.','2 кв.','3 кв.'])],'team_sections':[{'kind':'Политики','remaining_targets':[self.row('Политики',['1 кв.','3 кв.'])]},{'kind':'Кураторы','remaining_targets':[]}]})
        self.assertEqual(len(result),3)
        self.assertEqual(len(result[0]['cells']),2)
        self.assertEqual(result[1]['cells'][1]['cards'],[])
        self.assertEqual([c['cards'][0]['target']['label'] for c in result[2]['cells']],['3 кв.','3 кв.'])

    def test_multiple_formulas_stay_in_the_same_period_cell(self):
        result=layout({'department_remaining_targets':[self.row('Все',['IV квартал и годовая часть'])],'team_sections':[{'kind':'Политики','remaining_targets':[self.row('Политики',['4 кв.']),self.row('Политики',['4 кв.'])]}]})
        self.assertEqual(len(result),1)
        self.assertEqual(len(result[0]['cells'][1]['cards']),2)
