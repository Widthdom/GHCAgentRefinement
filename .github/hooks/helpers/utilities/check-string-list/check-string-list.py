# 値が、空白以外を含む文字列（non-blank）だけのリストかを判定する。
# 定義ファイルの配列を検証するhookとutilityが共有する。
# 呼び出し元のエラーメッセージでは、このリポジトリの慣例に合わせて「non-empty strings」と書く。


def is_non_blank_string_list(value, allow_empty_list=False):
    # allow_empty_list：要素のない空のリストを認めるか。空文字列や空白だけの要素は、この指定によらず認めない。
    return (
        isinstance(value, list) and (allow_empty_list or bool(value))
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )
