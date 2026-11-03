import os
import tempfile


# check-hook-temp-files hook（件数の警告）とclean-hook-temp-files skill（削除）が共有する判定基準。
# 両者で基準がずれないよう、対象の分類と保持期間はここだけで定める。
# hookが一時フォルダー配下に書き込む分類。どちらも48時間更新がなければ削除してよい。
# 使用中のセッションの状態は直近に更新されるため、更新時刻だけで削除対象から外れる。
_TEMP_ROOT_NAME = 'ghc-agent-refinement'
_TEMP_CATEGORIES = ('hook-states', 'debug-logs')
_RETENTION_SECONDS = 48 * 60 * 60


def get_temp_root():
    return os.path.join(tempfile.gettempdir(), _TEMP_ROOT_NAME)


def _is_stale(path, now):
    # lstat：シンボリックリンク自体の更新時刻を見る。リンク先は削除対象ではないため。
    # 調べている間に他のhookが削除したファイルは対象外とする。
    try:
        return now - os.lstat(path).st_mtime > _RETENTION_SECONDS
    except OSError:
        return False


def find_stale_files(root, now):
    # 戻り値は (分類からの相対ディレクトリ, パス, バイト数) のリスト。
    stale_files = []
    for category in _TEMP_CATEGORIES:
        category_directory = os.path.join(root, category)
        # os.walkは、読めないディレクトリを例外にせず読み飛ばす。
        for directory, _, filenames in os.walk(category_directory):
            for filename in filenames:
                path = os.path.join(directory, filename)
                if not _is_stale(path, now):
                    continue
                try:
                    size = os.lstat(path).st_size
                except OSError:
                    continue
                # 分類は表示に使うため、OSによらず `/` 区切りにする。
                group = os.path.relpath(directory, root).replace(os.sep, '/')
                stale_files.append((group, path, size))
    return sorted(stale_files)


def _find_stale_directories(root, now):
    # 分類のディレクトリ自体は残し、その配下のディレクトリだけを対象にする。深い順に並べる。
    stale_directories = []
    for category in _TEMP_CATEGORIES:
        for directory, subdirectories, _ in os.walk(os.path.join(root, category)):
            for subdirectory in subdirectories:
                path = os.path.join(directory, subdirectory)
                if _is_stale(path, now):
                    stale_directories.append(path)
    return sorted(stale_directories, key=lambda path: path.count(os.sep), reverse=True)


def delete_stale_files(root, now):
    # 戻り値は (削除したファイル数, 削除したバイト数, 削除したディレクトリ数, 失敗した (パス, 理由) のリスト)。
    # ディレクトリの更新時刻はファイルを削除すると更新されるため、削除前に対象を決めておく。
    # 48時間更新のないディレクトリだけを消し、hookが作成した直後の空ディレクトリを消さない。
    stale_directories = _find_stale_directories(root, now)
    deleted_files = 0
    deleted_bytes = 0
    failures = []
    for _, path, size in find_stale_files(root, now):
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError as ex:
            # Windowsでは、使用中のファイルを削除できない場合がある。
            failures.append((path, ex.strerror or str(ex)))
            continue
        deleted_files += 1
        deleted_bytes += size
    deleted_directories = 0
    for path in stale_directories:
        try:
            os.rmdir(path)
        except OSError:
            # 空でないディレクトリは残す。
            continue
        deleted_directories += 1
    return deleted_files, deleted_bytes, deleted_directories, failures
