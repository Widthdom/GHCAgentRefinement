import os
import subprocess
import sys

_helpers_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'helpers',
)
_repository_root = os.path.dirname(os.path.dirname(os.path.dirname(_helpers_directory)))
_test_paths = []
# 1ファイルの実行時間の上限。止まったテストで一括実行全体が止まらないようにする。
_TIMEOUT_SECONDS = 300

for current_directory, _, filenames in os.walk(_helpers_directory):
    for filename in filenames:
        if filename.startswith('test-') and filename.endswith('.py'):
            _test_paths.append(os.path.abspath(os.path.join(current_directory, filename)))

_test_paths.sort()
if not _test_paths:
    print(f'No hook tests found under {_helpers_directory}.', file=sys.stderr)
    print('NG', file=sys.stderr)
    sys.exit(2)

_failed_tests = []
# 進捗が分かるよう、通し番号と総数を桁を揃えて表示する。
_total_width = len(str(len(_test_paths)))
for test_index, test_path in enumerate(_test_paths, start=1):
    relative_test_path = os.path.relpath(test_path, _repository_root).replace(os.sep, '/')
    print(f'[{test_index:>{_total_width}}/{len(_test_paths)}] Running {relative_test_path}...', flush=True)
    try:
        result = subprocess.run([sys.executable, test_path], cwd=_repository_root, check=False, timeout=_TIMEOUT_SECONDS)
    except OSError as ex:
        print(f'Could not run {relative_test_path}: {ex}', file=sys.stderr)
        _failed_tests.append((relative_test_path, 'could not start'))
        continue
    except subprocess.TimeoutExpired:
        print(f'Timed out {relative_test_path} after {_TIMEOUT_SECONDS} seconds.', file=sys.stderr)
        _failed_tests.append((relative_test_path, f'timed out after {_TIMEOUT_SECONDS} seconds'))
        continue
    finally:
        print('\n', flush=True)

    if result.returncode != 0:
        _failed_tests.append((relative_test_path, f'exited with code {result.returncode}'))

if _failed_tests:
    print(f'Failed {len(_failed_tests)} of {len(_test_paths)} hook test files:', file=sys.stderr)
    for test_path, reason in _failed_tests:
        print(f'- {test_path}: {reason}', file=sys.stderr)
    print('NG', file=sys.stderr)
    sys.exit(2)

print(f'Passed all {len(_test_paths)} hook test files.')
print('OK', flush=True)
