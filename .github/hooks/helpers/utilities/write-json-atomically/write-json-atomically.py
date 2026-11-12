import json
import os
import tempfile

# JSONの値を、一時ファイルに書いてから置き換えて保存する。
# 状態ファイルを書くhookが共有する。書き込みの途中で別のhookが読んでも、不完全な内容を読ませない。


def write_json_atomically(path, value):
    # 親のフォルダーがなければ作る。書き込みや置き換えに失敗した場合は一時ファイルを消し、OSErrorをそのまま送出する。
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    # 置き換えを同じファイルシステム内で行うため、一時ファイルは保存先と同じフォルダーに作る。
    with tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', newline='\n', dir=directory, prefix='.tmp-', delete=False,
    ) as file:
        temporary_path = file.name
        try:
            json.dump(value, file, ensure_ascii=False, separators=(',', ':'))
        except BaseException:
            file.close()
            os.unlink(temporary_path)
            raise
    try:
        os.replace(temporary_path, path)
    except BaseException:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise
