---
name: clean-hook-temp-files
description: 'hookが一時フォルダーに残した状態ファイルとデバッグログを削除するときに使う。'
---

削除は [clean-hook-temp-files.py](./scripts/clean-hook-temp-files.py) で行い、ファイルを直接削除しない。macOS/Linuxでは `python3`、Windowsでは `python` で起動する。

1. `--dry-run` 付きで実行し、48時間を超えて更新されていないファイルの件数・容量・ディレクトリごとの内訳を確認する。0件なら、その旨を報告して終了する。
2. 結果をユーザーに示し、削除してよいか確認する。
3. 承認されたら `--dry-run` なしで実行し、削除した件数と容量を報告する。削除できなかったファイルがあれば、パスと理由を示す。
