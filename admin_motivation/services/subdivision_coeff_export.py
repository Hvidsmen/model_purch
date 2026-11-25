"""Saved coefficient matrices for every physical subdivision in one workbook."""
from io import BytesIO
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from ..models import SubdivisionCoeff,SubdivisionDirectorCoeff,CuratorCoefficient,SubdivisionManagerCoeff,PeriodMotivationCoefficient


def workbook(version):
    book=Workbook(write_only=True)
    info=book.create_sheet('О выгрузке');info.append(['Версия',str(version),'Статус',version.get_status_display()])
    info.append(['Сохранённые ставки выбранной версии. Пустая K-ячейка означает отсутствие ставки.'])
    info.append(['Подтверждённые периоды выгружаются за все годы: они не привязаны к версии.'])
    info.append(['Физические подразделения сохранены отдельно; формулы и K0–K4 не пересчитываются.'])
    def sheet(name,headers):
        s=book.create_sheet(name);s.freeze_panes='F2'
        s.append(headers)
        return s
    def append(s,values,percent_indices=()):
        cells=[]
        for index,value in enumerate(values):
            cell=WriteOnlyCell(s,value=value)
            if isinstance(value,str):cell.data_type='s'
            if index in percent_indices:cell.number_format='0.0000%'
            cells.append(cell)
        s.append(cells)
    core=SubdivisionCoeff.objects.filter(version=version,type_coeff__type_coeff_name__in=('Политики','Продажи')).select_related('subdivision','goods','type_coeff','segment','variation_calculate').order_by('type_coeff__type_coeff_name','subdivision__subdivision_key','goods_id','variation_calculate_id','segment_id')
    sheets={kind:sheet(kind,['Подразделение','Группа планов продаж','Группа ERP','Марка / бренд','Формула','K0','K1','K2','K3','K4']) for kind in ('Политики','Продажи')}
    current=None;values=None
    for cell in core.iterator(chunk_size=2000):
        key=(cell.type_coeff.type_coeff_name,cell.subdivision_id,cell.goods_id,cell.variation_calculate_id)
        if key!=current:
            if current is not None:append(sheets[current[0]],values,range(5,10))
            current=key;values=[cell.subdivision.subdivision_key,cell.goods.planning_group_sales,cell.goods.group,cell.goods.brand,str(cell.variation_calculate),*([None]*5)]
        segment=cell.segment.segment_name.upper().replace('К','K').replace('О','O')
        if len(segment)==2 and segment[0] in 'KO' and segment[1] in '01234':values[5+int(segment[1])]=cell.motivation_coeff
    if current is not None:append(sheets[current[0]],values,range(5,10))
    for label,model in (('Директора',SubdivisionDirectorCoeff),('Кураторы',CuratorCoefficient)):
        s=sheet(label,['Подразделение','Сотрудник','Группа планов продаж','Группа ERP','Марка / бренд','Формула','K0','K1','K2','K3','K4','Архив сотрудника'])
        for row in model.objects.filter(version=version).select_related('subdivision','employee','goods','variation_calculate').order_by('subdivision__subdivision_key','employee_id','goods_id').iterator(chunk_size=2000):
            append(s,[row.subdivision.subdivision_key,row.employee.full_name,row.goods.planning_group_sales,row.goods.group,row.goods.brand,str(row.variation_calculate or 'По умолчанию'),*[getattr(row,f'k{i}') for i in range(5)],'Да' if row.employee.is_archived else 'Нет'],range(6,11))
    s=sheet('Шаблоны руководителя',['Подразделение','Период шаблона','Множитель'])
    for row in SubdivisionManagerCoeff.objects.filter(version=version).select_related('subdivision','kind').iterator(chunk_size=2000):append(s,[row.subdivision.subdivision_key,row.kind.name,row.coeff],(2,))
    s=sheet('Подтверждённые периоды',['Подразделение','Год','Период','Множитель','Ревизия'])
    for row in PeriodMotivationCoefficient.objects.select_related('subdivision').order_by('subdivision__subdivision_key','year','period').iterator(chunk_size=2000):append(s,[row.subdivision.subdivision_key,row.year,row.get_period_display(),row.coefficient,row.revision],(3,))
    result=BytesIO();book.save(result);result.seek(0);return result
