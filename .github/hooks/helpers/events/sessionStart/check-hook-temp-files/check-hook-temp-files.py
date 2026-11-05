import io
import json
import os
import runpy
import sys
import time
from typing import cast


_WARNING_THRESHOLD = 500
_WARNING_MESSAGE = (
    '{count} temporary files under {root} (hook states and debug logs) have not been updated for more than '
    '48 hours. At the start of your first response, tell the user about this in the language you respond in and '
    'suggest running /clean-hook-temp-files to delete them. Do not delete them yourself.'
)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    # 削除用のclean-hook-temp-files skillと、対象の分類と保持期間を共有する。
    temp_files = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hook-temp-files.py'))
    root = temp_files['get_temp_root']()
    count = len(temp_files['find_stale_files'](root, time.time()))
    if count < _WARNING_THRESHOLD:
        return 0

    # Copilot SDKはhookの出力のうちsystemMessageを解釈しないため、エージェントに渡してユーザーへ伝えさせる。
    print(json.dumps(
        {'additionalContext': _WARNING_MESSAGE.format(count=count, root=root)},
        ensure_ascii=False, separators=(',', ':'),
    ))
    return 0


if __name__ == '__main__':
    sys.exit(main())
