import os
import subprocess
import sys

helpers_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'helpers',
)
repository_root = os.path.dirname(os.path.dirname(helpers_directory))
test_paths = []

for current_directory, _, filenames in os.walk(helpers_directory):
    for filename in filenames:
        if filename.startswith('test-') and filename.endswith('.py'):
            test_paths.append(os.path.abspath(os.path.join(current_directory, filename)))

test_paths.sort()
if not test_paths:
    print(f'No hook tests found under {helpers_directory}.', file=sys.stderr)
    print('NG', file=sys.stderr)
    sys.exit(2)

failed_tests = []
for test_path in test_paths:
    relative_test_path = os.path.relpath(test_path, repository_root)
    print(f'Running {relative_test_path}...', flush=True)
    try:
        result = subprocess.run([sys.executable, test_path], cwd=repository_root, check=False)
    except OSError as ex:
        print(f'Could not run {relative_test_path}: {ex}', file=sys.stderr)
        failed_tests.append((relative_test_path, 'could not start'))
        continue
    finally:
        print('\n', flush=True)

    if result.returncode != 0:
        failed_tests.append((relative_test_path, f'exited with code {result.returncode}'))

if failed_tests:
    print(f'Failed {len(failed_tests)} of {len(test_paths)} hook test files:', file=sys.stderr)
    for test_path, reason in failed_tests:
        print(f'- {test_path}: {reason}', file=sys.stderr)
    print('NG', file=sys.stderr)
    sys.exit(2)

print(f'Passed all {len(test_paths)} hook test files.')
print('OK', flush=True)
