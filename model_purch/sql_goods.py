"""Prepare legacy SQL Server goods for scenario/group uniqueness without losing snapshots."""
import json
from collections import defaultdict

from .goods_identity import planning_group_key, source_goods_key


def prepare_sql_goods(cursor):
    # All changes participate in the caller's SQL Server transaction.
    cursor.execute("""
        IF COL_LENGTH('portal.PGGoods', 'planning_group_key') IS NULL
            ALTER TABLE portal.PGGoods ADD planning_group_key CHAR(64) NULL;
        IF COL_LENGTH('portal.PGGoods', 'PlanningKey') IS NULL
            ALTER TABLE portal.PGGoods ADD PlanningKey NVARCHAR(255) NULL;
        IF COL_LENGTH('portal.PGGoods', 'source_code') IS NULL
            ALTER TABLE portal.PGGoods ADD source_code NVARCHAR(255) NULL;
        IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'portal.PGGoods') AND name=N'UQ_PGGoods_native_scenario_group')
            DROP INDEX UQ_PGGoods_native_scenario_group ON portal.PGGoods;
        IF OBJECT_ID(N'portal.PGGoodsDuplicateArchive', N'U') IS NULL
            CREATE TABLE portal.PGGoodsDuplicateArchive (
                id INT IDENTITY(1,1) PRIMARY KEY,
                original_id INT NOT NULL,
                kept_id INT NOT NULL,
                scenario_name NVARCHAR(255) NULL,
                original_data NVARCHAR(MAX) NOT NULL,
                archived_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
            );
    """)
    cursor.execute("UPDATE portal.PGGoods SET PlanningKey=source_code WHERE NULLIF(LTRIM(RTRIM(PlanningKey)),N'') IS NULL AND NULLIF(LTRIM(RTRIM(source_code)),N'') IS NOT NULL")
    cursor.execute('SELECT * FROM portal.PGGoods WITH (TABLOCKX, HOLDLOCK) ORDER BY id')
    columns = [column[0] for column in cursor.description]
    groups = defaultdict(list)
    for values in cursor.fetchall():
        row = dict(zip(columns, values))
        scenario = row.get('scenario_name')
        scenario_identity = scenario.rstrip().casefold() if scenario is not None else None
        code=str(row.get('PlanningKey') or row.get('source_code') or '').strip()
        name=str(row.get('planning_group') or '').strip()
        key=source_goods_key(code) if code else planning_group_key(name) if name else row.get('planning_group_key') if str(row.get('planning_group_key') or '').startswith('ungrouped:') else f"ungrouped:sql:{row['id']}"
        groups[(scenario_identity,key)].append(row)
    for (_, key), rows in groups.items():
        kept = max(rows, key=lambda row: row['id'])
        if len(rows) > 1:
            for row in rows:
                cursor.execute(
                    'INSERT INTO portal.PGGoodsDuplicateArchive '
                    '(original_id, kept_id, scenario_name, original_data) VALUES (?, ?, ?, ?)',
                    row['id'], kept['id'], row.get('scenario_name'), json.dumps(row, ensure_ascii=False, default=str),
                )
            for row in rows:
                if row['id'] != kept['id']:
                    cursor.execute('DELETE FROM portal.PGGoods WHERE id = ?', row['id'])
        cursor.execute('UPDATE portal.PGGoods SET planning_group_key = ? WHERE id = ?', key, kept['id'])
    cursor.execute("""
        IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'portal.PGGoods')
                   AND name = N'planning_group_key' AND is_nullable = 1)
            ALTER TABLE portal.PGGoods ALTER COLUMN planning_group_key CHAR(64) NOT NULL;
        IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'portal.PGGoods')
                       AND name = N'UQ_PGGoods_scenario_group')
            CREATE UNIQUE INDEX UQ_PGGoods_scenario_group
                ON portal.PGGoods (scenario_name, planning_group_key);
        IF EXISTS (SELECT 1 FROM sys.key_constraints WHERE parent_object_id = OBJECT_ID(N'portal.PGGoods')
                   AND name = N'UQ_PGGoods_scenario')
            ALTER TABLE portal.PGGoods DROP CONSTRAINT UQ_PGGoods_scenario;
    """)
