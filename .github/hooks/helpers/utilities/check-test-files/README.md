# check-test-files utility README

## 役割
パスがテストファイルかを判定する。テストファイルを特別に扱うhookが共有し、判定の基準を1か所で管理する。

## 判定の規則
- [test-file-definitions.json](./test-file-definitions.json) の `testFilePatterns` の正規表現を、`load_test_file_patterns` で読み込む。ファイルがなければ `OSError`、形式が不正（空の配列、空の文字列を含むなど）か正規表現が不正なら `ValueError` にする。hookはこれを定義の読み込みエラーとして異常終了する。
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
- [block-test-weakening](../../events/preToolUse/block-test-weakening/README.md)
- [block-goalpost-moving](../../events/preToolUse/block-goalpost-moving/README.md)
- [block-hardcoding-for-tests](../../events/preToolUse/block-hardcoding-for-tests/README.md)
- [block-tautological-tests](../../events/preToolUse/block-tautological-tests/README.md)
- [block-time-bomb-literals](../../events/preToolUse/block-time-bomb-literals/README.md)
- [detect-orphaned-additions](../../events/agentStop/detect-orphaned-additions/README.md)
