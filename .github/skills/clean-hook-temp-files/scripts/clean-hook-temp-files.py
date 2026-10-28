import argparse
import collections
import os
import runpy
import sys
import time


def format_size(size):
    for unit in ('B', 'KB', 'MB'):
        if size < 1024 or unit == 'MB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024


def main(arguments=None):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(
        description='Delete hook states and debug logs that have not been updated for more than 48 hours.',
    )
    parser.add_argument('--dry-run', action='store_true', help='list the files to delete without deleting them')
    parsed_arguments = parser.parse_args(arguments)

    # 判定基準の持ち主である警告用のcheck-hook-temp-files hookから、対象の分類と保持期間を借りる。
    # 4階層上：.github ディレクトリ（scripts → clean-hook-temp-files → skills → .github）
    github_directory = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    temp_files = runpy.run_path(os.path.join(
        github_directory, 'hooks', 'helpers', 'events', 'sessionStart', 'check-hook-temp-files', 'hook-temp-files.py',
    ))
    root = temp_files['get_temp_root']()
    now = time.time()

    print(f'Temporary directory: {root}')
    if parsed_arguments.dry_run:
        stale_files = temp_files['find_stale_files'](root, now)
        print(
            f'Files not updated for more than 48 hours: {len(stale_files)} '
            f'({format_size(sum(size for _, _, size in stale_files))})'
        )
        # 件数が多くなるため、ファイルではなくディレクトリごとにまとめて示す。
        groups = collections.Counter(group for group, _, _ in stale_files)
        for group, count in sorted(groups.items()):
            print(f'  {group}: {count}')
        return 0

    deleted_files, deleted_bytes, deleted_directories, failures = temp_files['delete_stale_files'](root, now)
    print(
        f'Deleted {deleted_files} files ({format_size(deleted_bytes)}) '
        f'and {deleted_directories} empty directories.'
    )
    if failures:
        print(f'Failed to delete {len(failures)} files:', file=sys.stderr)
        for path, reason in failures:
            print(f'  {path}: {reason}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
