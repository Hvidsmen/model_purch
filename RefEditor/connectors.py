


def connect_database(server, database):
    import pyodbc
    conn = pyodbc.connect(
        'DRIVER={SQL Server}; SERVER=' + server + ';DATABASE=' + database + '; Trusted_Connection=yes;')
    cursor = conn.cursor()
    return conn, cursor

def create_engine(server, database):
    from sqlalchemy import create_engine
    import urllib

    quoted = urllib.parse.quote_plus(
        'DRIVER={SQL Server}; SERVER=' + server + ';DATABASE=' + database + '; Trusted_Connection=yes;')
    engine = create_engine('mssql+pyodbc:///?odbc_connect={}'.format(quoted))
    return engine

