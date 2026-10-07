

def run():
    from PlanningSystem.models import TypeGoodsRef, BrangRef,GroupERPRef,GroupENRef,PlanningGroupRef,PlanningGroupSalesRef,GoodsRef
    from PlanningSystem.connector_dwh import connect_database, create_engine
    from tqdm import tqdm

    import pandas as pd
    con, cursor = connect_database('vm-dwh', 'DAtaWH')
    engine = create_engine('vm-dwh', 'DAtaWH')

    print('Goods update')
    sql = """
    select 
	 ModelCode	goods_key 
	,'ModelCode' type_goods 
	,COALESCE(BrandName,'NA') brand
    ,COALESCE(GroupERP,'NA')  group_ERP
    ,COALESCE(Group_1,'NA') group_EN 
    ,COALESCE(PlanningGroupOZPErp,'NA') planning_group
    ,COALESCE(PlanningGroupSalesErp,'NA') planning_group_sales 
from 
	DataWH.dbo.ModelCodeERP
	"""

    df_goods = pd.read_sql(sql, con=con)
    print(df_goods.shape)
    for i, row in tqdm(df_goods.iterrows()):
        goods_key, type_goods, brand, group_ERP, group_EN, planning_group, planning_group_sales = row

        type_goods = TypeGoodsRef.get_or_create(type_goods)
        brand = BrangRef.get_or_create(brand)
        group_ERP = GroupERPRef.get_or_create(group_ERP)
        group_EN = GroupENRef.get_or_create(group_EN)
        planning_group = PlanningGroupRef.get_or_create(planning_group)
        planning_group_sales =PlanningGroupSalesRef.get_or_create(planning_group_sales)
        if len(GoodsRef.objects.filter(goods_key=goods_key)) == 0:
            obj = GoodsRef.objects.create(
                goods_key=goods_key
                , type_goods=type_goods
                , brand=brand
                , group_ERP=group_ERP
                , group_EN=group_EN
                , planning_group=planning_group
                , planning_group_sales=planning_group_sales
            )
            obj.save()
        else:
            obj = GoodsRef.objects.get(goods_key=goods_key)
            obj.type_goods = type_goods
            obj.brand = brand
            obj.group_ERP = group_ERP
            obj.group_EN = group_EN
            obj.planning_group = planning_group
            obj.planning_group_sales = planning_group_sales
            obj.save()
