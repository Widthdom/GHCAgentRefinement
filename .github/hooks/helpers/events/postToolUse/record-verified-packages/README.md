# record-verified-packages hook README

## 発動条件
postToolUse（ツール `bash`・`powershell`・`web_fetch` が成功した直後）

## 処理概要
レジストリへの問い合わせやパッケージのページの取得で実在を確かめた依存パッケージを、[block-unverified-dependencies](../../preToolUse/block-unverified-dependencies/README.md) hookのために記録する。

## 処理詳細
1. `bash`・`powershell` では、コマンドを `&&`・`||`・`;`・`|`・改行で分け、それぞれを[問い合わせとみなすコマンド](#検出する内容)と照合して、エコシステムと名前を取り出す。`name@1.2.3`・`name==1.2.3` のようなバージョンの指定は除く。
2. 出力（`toolResult.textResultForLlm`）に見つからない旨（`No results`・`Not Found`・`E404`・`No matching distribution`・`NU1101`・`No module named` など）があれば記録しない。それ以外は、出力に名前を含むものだけを記録する。`python -c "import x"` は成功しても何も出力しないため、見つからない旨がないことだけで記録する。
3. `web_fetch` では、URLが[パッケージのページ](#検出する内容)なら名前を取り出す。出力が空、または先頭2000文字に `404`・`Not Found`・`does not exist` があれば記録しない。
4. 記録は [verified-packages-state.py](../../../utilities/verified-packages-state/verified-packages-state.py) で、エコシステムと正規化した名前の組として保存する。

## 検出する内容
|エコシステム|問い合わせとみなすコマンド|パッケージのページ|
|--|--|--|
|NuGet|`dotnet package search <id>`・`dotnet add [<project>] package <id>`・`nuget list <id>`・`nuget search <id>`|`https://www.nuget.org/packages/<id>`・`https://api.nuget.org/v3-flatcontainer/<id>/`・`https://api.nuget.org/v3/registration*/<id>/`|
|PyPI|`pip index versions <name>`・`pip download <name>`・`uv add <name>`（`python -m pip`・`pip3` も含む）|`https://pypi.org/project/<name>`・`https://pypi.org/pypi/<name>`・`https://pypi.org/simple/<name>`|
|npm|`npm view <name>`・`npm info <name>`・`npm show <name>`・`yarn info <name>`・`pnpm view <name>`・`pnpm add <name>`|`https://www.npmjs.com/package/<name>`・`https://registry.npmjs.org/<name>`|
|Pythonのモジュール|`python -c "import <name>"`（`python3`・`python3.14`・`python.exe` も含む）|—|

## 前提条件
- Python 3.14以降の標準の `sqlite3` を使うため、SQLite自体のインストールは不要（公式Windows版Pythonにも同梱）。

## 例外
- `pip install`・`npm install` などのインストールは、確認とみなさない。インストールに成功した依存は手元の照合先に入るため、記録しなくても block-unverified-dependencies hookを通る。
- 記録だけを行い、エージェントへの出力はしない。

## 制約と限界
- 出力の文言で成否を判定するため、見つからない旨を定義にない文言で出力するツールでは誤って記録する。逆に、出力全体に見つからない旨が1つでもあれば、同じコマンド内の他の問い合わせも記録しない。
- オプションの値と名前は、`-` で始まる語の直後の語をオプションの値とみなせる限りそうみなして区別する。値を取らないオプションの直後に名前があり、さらに後ろに別の語が続く書き方では、名前を取り違える場合がある。

## 備考
- 状態は `<一時フォルダー>/ghc-agent-refinement/hook-states/verified-packages/<セッションID>.sqlite3` に保存する。DBファイル自体は自動削除しない。
