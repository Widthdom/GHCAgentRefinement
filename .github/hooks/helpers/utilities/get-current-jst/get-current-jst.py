from datetime import datetime, timedelta, timezone

# 日本標準時は夏時間がなく常にUTC+9である。
#   WindowsのPythonはタイムゾーンデータベースを持たない場合があるため、zoneinfoを使わず固定オフセットで表す。
current_jst = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S JST')
