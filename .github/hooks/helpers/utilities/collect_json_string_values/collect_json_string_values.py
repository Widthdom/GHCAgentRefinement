# JSONを解析した値（toolArgsやイベント履歴の `arguments` など）から、文字列の値を
# 辞書・リストの入れ子をたどって出現順に集める。辞書のキーは集めない。
# ツールの引数から書き込む内容やパス、コマンドを探すhookとutilityが共有する。


def collect_json_string_values(value, ignored_keys=frozenset()):
    # ignored_keys：たどらない辞書のキー。どの深さの辞書にも適用する。
    # 文字列・辞書・リスト以外の値（数値・真偽値・null）は無視する。
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in ignored_keys:
                yield from collect_json_string_values(item, ignored_keys)
    elif isinstance(value, list):
        for item in value:
            yield from collect_json_string_values(item, ignored_keys)
