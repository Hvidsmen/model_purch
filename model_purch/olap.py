import requests
import xml.etree.ElementTree as ET
from requests.auth import HTTPBasicAuth


def process_olap_cube_via_xmla(
        server_url,  # Например: "http://olap-server/OLAP/msmdpump.dll"
        database_name,  # Имя базы SSAS, например: "ModelPurch"
        cube_name,  # Имя куба, например: "SalesCube"
        username,
        password,
        process_type="ProcessFull"  # ProcessFull, ProcessUpdate, ProcessData
):
    """
    Запускает пересчёт OLAP куба на SSAS через XMLA.

    process_type:
      - ProcessFull   — полная пересборка куба (самый долгий, но надёжный)
      - ProcessUpdate — обновление только изменённых данных
      - ProcessData   — только загрузка данных без пересчёта агрегатов
      - ProcessAdd    — добавление новых данных
    """

    # Формируем XMLA-запрос
    xmla_request = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/">
        <Body>
            <Process xmlns="urn:schemas-microsoft-com:xml-analysis">
                <Object>
                    <DatabaseID>{database_name}</DatabaseID>
                    <CubeID>{cube_name}</CubeID>
                </Object>
                <Type>{process_type}</Type>
                <WriteBackTableCreate>Always</WriteBackTableCreate>
            </Process>
        </Body>
    </Envelope>"""

    headers = {
        'Content-Type': 'text/xml; charset=utf-8',
        'SOAPAction': 'urn:schemas-microsoft-com:xml-analysis:Process'
    }

    try:
        response = requests.post(
            server_url,
            data=xmla_request,
            headers=headers,
            auth=HTTPBasicAuth(username, password),
            timeout=600  # 10 минут на случай долгого пересчёта
        )

        if response.status_code != 200:
            raise Exception(f"HTTP ошибка: {response.status_code}")

        # Проверяем ответ на наличие ошибок
        if '<Fault>' in response.text or '<Error' in response.text:
            raise Exception(f"SSAS вернул ошибку: {response.text[:500]}")

        return {
            'success': True,
            'cube': cube_name,
            'process_type': process_type,
            'message': f'Куб "{cube_name}" успешно пересчитан'
        }

    except requests.exceptions.Timeout:
        raise Exception("Превышено время ожидания ответа от SSAS")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Ошибка подключения к SSAS: {e}")