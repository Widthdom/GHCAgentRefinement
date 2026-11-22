# check_test_files utility README

## 役割
パスがテストファイルかを判定する。テストファイルを特別に扱うhookが共有し、判定の基準を1か所で管理する。

## 判定の規則
- [test_file_definitions.json](./test_file_definitions.json) の `testFilePatterns` の正規表現を、`load_test_file_patterns` で読み込む。ファイルがなければ `OSError`、形式が不正（空の配列、空の文字列を含むなど）か正規表現が不正なら `ValueError` にする。hookはこれを定義の読み込みエラーとして異常終了する。
- `is_test_file` は、cwdやリポジトリルートからの相対パス（`/` 区切り）が、いずれかのパターンに一致すればテストファイルとみなす。
- 大文字小文字を区別しない場合はパターンの先頭に `(?i)` を付ける。.NET・Java・Kotlinのテストクラスは、名前が大文字の `Test`・`Tests` で終わるものだけを対象とする。

|種類|例|
|--|--|
|テスト用のフォルダー配下|`tests/`・`test/`・`__tests__/`・`spec/`・`specs/`・`*.Tests/`・`*.UnitTests/`・`*.IntegrationTests/`|
|.NET・Java・Kotlinのテストクラス|`*Test.cs`・`*Tests.cs`・`*Test.java`・`*Tests.fs`・`*Test.kt`|
|PythonとPowerShellのテストスクリプト|`test_*.py`・`test-*.py`・`test-*.ps1`|
|Python・Go・Rustのテストファイル|`*_test.py`・`*_test.go`・`*_test.rs`|
|JavaScript・TypeScriptのテストファイル|`*.test.ts`・`*.spec.js`・`*.test.mjs`・`*.spec.tsx`|

## 利用するhook
- [block_test_weakening](../../events/preToolUse/block_test_weakening/README.md)
- [block_goalpost_moving](../../events/preToolUse/block_goalpost_moving/README.md)
- [block_hardcoding_for_tests](../../events/preToolUse/block_hardcoding_for_tests/README.md)
- [block_tautological_tests](../../events/preToolUse/block_tautological_tests/README.md)
- [block_time_bomb_literals](../../events/preToolUse/block_time_bomb_literals/README.md)
- [detect_orphaned_additions](../../events/agentStop/detect_orphaned_additions/README.md)
