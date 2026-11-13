# 理由やメッセージに示す文字列を、指定した文字数で切り詰める。
# コードの行やコマンドなどを理由に引用するhookが共有する。


def truncate_text(text, kept_length):
    # kept_lengthを超える場合は、先頭のkept_length文字に `...` を付けて返す。
    # `...` はkept_lengthの外に付けるため、戻り値は最大でkept_length + 3文字になる。
    return text if len(text) <= kept_length else text[:kept_length] + '...'
