def run():
    from RefEditor.models import Subdivision, GroupOZP, SubGroupOZP, StoreGroupOZP
    from PlanningSystem.connector_dwh import connect_database, create_engine
    from tqdm import tqdm

    import pandas as pd
    con, cursor = connect_database('vm-dwh', 'DAtaWH')
    engine = create_engine('vm-dwh', 'DAtaWH')

    print('StoreGroup update')
    sql = """
SELECT  [Subdivision]
      ,[StoreName]
      ,[Group]
      ,[SupGroup]
FROM [DataWH].[erp].[StoreGroup]

	"""

    df_goods = pd.read_sql(sql, con=con)
    print(df_goods.shape)
    for i, row in tqdm(df_goods.iterrows()):
        sub, store, g, sg = row
        sub = sub.strip()
        store = store.strip()
        g = g.strip()
        sg = g.strip()
        if len(Subdivision.objects.filter(subdivision_name=sub)) == 0:
            obj = Subdivision.objects.create(subdivision_name=sub)
            obj.save()

        sub_obj = Subdivision.objects.get(subdivision_name=sub)

        if len(GroupOZP.objects.filter(group_ozp_name=g)) == 0:
            obj = GroupOZP.objects.create(group_ozp_name=g)
            obj.save()

        grou_ozp_obj = GroupOZP.objects.get(group_ozp_name=g)

        if len(SubGroupOZP.objects.filter(sub_group_ozp_name=sg)) == 0:
            obj = SubGroupOZP.objects.create(sub_group_ozp_name=sg)
            obj.save()

        sub_grou_ozp_obj = SubGroupOZP.objects.get(sub_group_ozp_name=sg)

        if len(StoreGroupOZP.objects.filter(store_name=store, subdivision=sub_obj)) > 0:
            StoreGroupOZP.objects.get(store_name=store, subdivision=sub_obj).delete()

        obj = StoreGroupOZP.objects.create(
            key_store_sub=StoreGroupOZP.make_key(store, sub)
            , store_name=store.strip()

            , subdivision=sub_obj
            , group_ozp=grou_ozp_obj
            , sub_group_ozp=sub_grou_ozp_obj

        )
