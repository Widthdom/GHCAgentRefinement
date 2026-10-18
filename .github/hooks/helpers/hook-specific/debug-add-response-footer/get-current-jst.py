from datetime import datetime, timedelta, timezone

# 日本標準時は夏時間がなく常にUTC+9である。
#   WindowsのPythonはタイムゾーンデータベースを持たない場合があるため、zoneinfoを使わず固定オフセットで表す。
JST = timezone(timedelta(hours=9))


def get_current_jst():
    return datetime.now(timezone.utc).astimezone(JST).strftime('%Y-%m-%d %H:%M:%S JST')


if __name__ == '__main__':
    print(get_current_jst())
