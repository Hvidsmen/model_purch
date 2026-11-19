"""Filter frozen recipient rows before rebuilding their financial totals."""
class ResultFilters:
    def __init__(self,query,results,names):
        self.selected={key:set(query.getlist('result_'+key)) for key in ('year','branch','kind')}
        available={'year':set(),'branch':set(),'kind':set()}
        for result in results.values():
            if result.get('year') is not None:available['year'].add(str(result['year']))
            for row in result.get('rows') or []:
                available['branch'].add(row['branch']);available['kind'].add(row['kind'])
        self.fields=[]
        for key,label in (('year','Год расчёта'),('branch','Подразделение'),('kind','Тип коэффициента')):
            options=[{'value':value,'label':names.get(value,value) if key=='branch' else value,
                      'selected':value in self.selected[key]} for value in available[key]]
            options.sort(key=lambda item:item['label'])
            self.fields.append({'key':key,'label':label,'count':len(self.selected[key]),'options':options})

    def year(self,result):
        return not self.selected['year'] or str(result.get('year')) in self.selected['year']

    def rows(self,rows):
        return [row for row in rows if all(not self.selected[key] or row.get(key) in self.selected[key] for key in ('branch','kind'))]

    @staticmethod
    def currency_url(query,run_id,currency):
        values=query.copy();values.pop('export',None);values['run']=str(run_id);values['currency']=currency
        return '?'+values.urlencode()
