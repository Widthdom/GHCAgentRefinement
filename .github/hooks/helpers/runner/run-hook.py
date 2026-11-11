import io
import json
import os
import runpy
import sqlite3
import sys
import time
import traceback
from typing import cast


_runner_directory = os.path.dirname(os.path.abspath(__file__))
_helpers_directory = os.path.dirname(_runner_directory)
# 共通hookは helpers/events、このリポジトリ専用のhookは helpers/-repo-events に置く。
_events_directories = (
    os.path.join(_helpers_directory, 'events'),
    os.path.join(_helpers_directory, '-repo-events'),
)
# helpers → hooks → .github → リポジトリルート
_repository_root = os.path.dirname(os.path.dirname(os.path.dirname(_helpers_directory)))
# Pythonの標準入出力は、Windowsでは改行コードを変換し、それ以外では変換しない。hookの実行時も同じにする。
_STANDARD_STREAM_NEWLINE = None if os.name == 'nt' else '\n'


def _resolve_hook_script(argument):
    # 実行スクリプトは helpers/events または helpers/-repo-events の <イベント名>/<hook名>/<hook名>.py に限る。
    script_path = os.path.normpath(os.path.join(_repository_root, argument))
    hook_directory = os.path.dirname(script_path)
    event_directory = os.path.dirname(hook_directory)
    hook_name = os.path.basename(hook_directory)
    if (
        os.path.normcase(os.path.dirname(event_directory)) not in {
            os.path.normcase(directory) for directory in _events_directories
        }
        or os.path.basename(script_path) != f'{hook_name}.py'
    ):
        raise ValueError(
            f'Hook script must be helpers/events/<event-name>/<hook-name>/<hook-name>.py or '
            f'helpers/-repo-events/<event-name>/<hook-name>/<hook-name>.py: {argument}'
        )
    if not os.path.isfile(script_path):
        raise FileNotFoundError(f'Hook script not found: {argument}')
    return script_path, os.path.basename(event_directory), hook_name


def _get_session_id(hook_input_bytes):
    # 入力の検証は起動したhook自身が行うため、セッションIDを読めない入力では記録だけを行わない。
    # UUIDでない値も、状態の保存先を決められないため読めない入力とみなす。
    try:
        hook_input = json.loads(hook_input_bytes.decode('utf-8'))
    except ValueError:
        return None
    if not isinstance(hook_input, dict):
        return None
    session_id = hook_input.get('sessionId')
    parse_session_id = runpy.run_path(
        os.path.join(_helpers_directory, 'utilities', 'get-state-path', 'get-state-path.py')
    )['parse_session_id']
    try:
        parse_session_id(session_id)
    except ValueError:
        return None
    return session_id


def _is_fired(exit_code, output_bytes):
    # 終了コードが0以外か、空白や空のJSONオブジェクト以外を出力した場合を「機能した」とみなす。
    if exit_code != 0:
        return True
    output = output_bytes.decode('utf-8', errors='replace').strip()
    if not output:
        return False
    try:
        return json.loads(output) != {}
    except ValueError:
        return True


def _new_stream(buffer, original):
    return io.TextIOWrapper(
        buffer, encoding=original.encoding, errors=original.errors, newline=_STANDARD_STREAM_NEWLINE,
    )


def _get_exit_code(code):
    # sys.exitの引数をPythonインタープリターと同じ規則で終了コードにする。
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def _run_hook_script(script_path, hook_input_bytes):
    # hookを子プロセスにせず、このプロセス内で実行する。
    # Copilotがタイムアウトでこのプロセスを終了させたとき、hookも一緒に止まり、副作用だけが後から残らないようにするため。
    # 戻り値は (終了コード, 標準出力のバイト列, 標準エラーのバイト列)。
    stdout_buffer = io.BytesIO()
    stderr_buffer = io.BytesIO()
    # 差し替えたストリームは、参照がなくなると下のBytesIOごと閉じられるため、値を取り出すまで保持する。
    hook_streams = (
        _new_stream(io.BytesIO(hook_input_bytes), sys.__stdin__),
        _new_stream(stdout_buffer, sys.__stdout__),
        _new_stream(stderr_buffer, sys.__stderr__),
    )
    saved_streams = (sys.stdin, sys.stdout, sys.stderr)
    saved_argv = sys.argv
    saved_path = list(sys.path)
    sys.stdin, sys.stdout, sys.stderr = hook_streams
    sys.argv = [script_path]
    # 直接起動した場合と同じく、スクリプトのディレクトリをimportの探索先の先頭にする。
    sys.path[0] = os.path.dirname(script_path)
    try:
        try:
            runpy.run_path(script_path, run_name='__main__')
            exit_code = 0
        except SystemExit as ex:
            exit_code = _get_exit_code(ex.code)
        except Exception:
            # 捕捉されなかった例外は、Pythonインタープリターと同じくトレースバックを標準エラーに出力して終了コード1にする。
            traceback.print_exc()
            exit_code = 1
        # 書き込み途中の内容をバッファに反映する。
        hook_streams[1].flush()
        hook_streams[2].flush()
        output = (exit_code, stdout_buffer.getvalue(), stderr_buffer.getvalue())
    finally:
        sys.stdin, sys.stdout, sys.stderr = saved_streams
        sys.argv = saved_argv
        sys.path[:] = saved_path
    return output


def main():
    # 型定義上の `sys.stderr` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    if len(sys.argv) != 2:
        print('Usage: run-hook.py <repository-relative hook script path>', file=sys.stderr)
        return 2
    try:
        script_path, event_name, hook_name = _resolve_hook_script(sys.argv[1])
    except (ValueError, OSError) as ex:
        print(f'Failed to resolve hook script: {ex}', file=sys.stderr)
        return 2

    # 改行コードを変換せずに渡すため、標準入出力はバイト列のまま中継する。
    hook_input_bytes = sys.stdin.buffer.read()
    started_at_ms = time.time_ns() // 1_000_000
    exit_code, output_bytes, error_bytes = _run_hook_script(script_path, hook_input_bytes)

    sys.stdout.buffer.write(output_bytes)
    sys.stdout.buffer.flush()
    sys.stderr.flush()
    sys.stderr.buffer.write(error_bytes)
    sys.stderr.buffer.flush()

    # 記録に失敗してもhookの判定は変えず、終了コードはhookのものを返す。原因は標準エラーに出力する。
    try:
        session_id = _get_session_id(hook_input_bytes)
        if session_id is not None:
            runpy.run_path(os.path.join(_helpers_directory, 'utilities', 'hook-activity-state', 'hook-activity-state.py'))['record_hook_run'](
                session_id, event_name, hook_name, started_at_ms, _is_fired(exit_code, output_bytes),
            )
    except (OSError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to record hook run for {hook_name}: {ex}', file=sys.stderr)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
