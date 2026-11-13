import hashlib
import os

# 文字列やパスのSHA-256のダイジェストを、16進表記で作る。
# 状態にパスやコマンドなどの値そのものを保存せず、照合用のダイジェストだけを保存するhookとutilityが共有する。


def get_text_sha256_digest(text):
    # textをUTF-8で符号化したバイト列のダイジェストを返す。
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def get_path_sha256_digest(path):
    # 大文字小文字を区別しないWindowsでも同じファイルを同じダイジェストにするため、normcaseしてから作る。
    return get_text_sha256_digest(os.path.normcase(path))
