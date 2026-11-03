import os
import subprocess
import sys

_helpers_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'helpers',
)
_repository_root = os.path.dirname(os.path.dirname(_helpers_directory))
_test_paths = []

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
for test_path in _test_paths:
    relative_test_path = os.path.relpath(test_path, _repository_root)
    print(f'Running {relative_test_path}...', flush=True)
    try:
        result = subprocess.run([sys.executable, test_path], cwd=_repository_root, check=False)
    except OSError as ex:
        print(f'Could not run {relative_test_path}: {ex}', file=sys.stderr)
        _failed_tests.append((relative_test_path, 'could not start'))
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
