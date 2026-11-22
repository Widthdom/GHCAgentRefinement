from datetime import datetime, timedelta, timezone


def get_current_jst():
    # 呼び出した時点の日本標準時を `YYYY-MM-DD HH:MM:SS JST` の形で返す。
    # 日本標準時は夏時間がなく常にUTC+9である。
    #   WindowsのPythonはタイムゾーンデータベースを持たない場合があるため、zoneinfoを使わず固定オフセットで表す。
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S JST')
