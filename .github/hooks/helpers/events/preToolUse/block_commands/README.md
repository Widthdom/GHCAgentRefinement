# block_commands hook README

## 発動条件
preToolUse（ツール `bash`・`powershell` の実行前）

## 処理概要
定義ファイルの正規表現に一致する危険なコマンドの実行を、定義された理由とともに拒否する。Pythonが使えない環境では、すべてのコマンドを拒否する。

## 処理詳細
1. Windowsでは `python`、macOS/Linuxでは `python3` が見つからない場合、またはPython 3.14未満の場合、標準エラーにその旨を出力したうえでツール実行拒否JSONを標準出力する。
2. 1.以外の場合、標準入力を引き継いで[py](./block_commands.py)を実行する。
3. [blocked_command_definitions.json](./blocked_command_definitions.json) を [read_definitions](../../../utilities/read_definitions/README.md) で読み込む。定義は `name`・`pattern`・`reason` を持つオブジェクトの空でない配列とする。ファイルがない場合、フォーマット違反（空の配列を含む）の場合、不正な正規表現がひとつでも含まれていた場合は、危険なコマンドを止められないため異常終了し、標準エラーにその旨を出力したうえでツール実行拒否JSONを標準出力する。
4. 呼び出すツール（`toolName`・`toolArgs`）がbash・powershellで、そのコマンドが `blocked_command_definitions.json` に定義された正規表現に一致した場合は、定義の `reason` を拒否理由とするツール実行拒否JSONを標準出力し、正常終了する。標準エラーには出力しない。
   - `toolArgs` がJSON文字列に包まれている場合は、[parse_tool_calls.py](../../../utilities/parse_tool_calls/parse_tool_calls.py) の `unwrap_tool_args` で包みを外してから `command` を取り出す。JSONとして解析できない文字列は、そのままコマンドとして扱う。

## 検出するコマンド
正規表現の詳細は [blocked_command_definitions.json](./blocked_command_definitions.json) を参照。

|分類|拒否するコマンド|許可する例|
|---|---|---|
|gitの履歴・作業内容の消失|`git rebase`・`git reset`・`git commit --amend`・強制push（`-f`・`--force*`・`+<ref>`）・`git clean -f`・`git checkout`（`-f`・`--`・`.`）・`git switch`（`-f`・`--discard-changes`）・`git restore`（`--staged` だけを付けた場合を除く）・`git branch`（`-D`・`-f`・`--force`）・リモートの参照の削除（`git push` の `-d`・`--delete`・`--mirror`・`--prune`・`:<ref>`）・`git stash drop`・`git stash clear`・`git filter-branch`・`git filter-repo`・`git reflog expire`・`git reflog delete`・`git update-ref`（`-d`・`--stdin`）・`git gc --prune=now`|`git restore --staged`・`git branch -d`・`git clean -n`|
|ファイルの一括削除|再帰的な削除（`rm -r`・`Remove-Item -Recurse` とそのエイリアス）・`find -delete`・`find -exec rm`・`xargs rm`・`shred`・`srm`・`wipe`|`rm -f <ファイル>`・`Remove-Item <ファイル> -Force`|
|ディスク・システムの破壊|ディスクの初期化・パーティション操作（`mkfs`・`fdisk`・`diskutil erase*`・`Format-Volume` など）・ディスクデバイスへの書き込み（`dd of=/dev/…`・`> /dev/sda`）・再帰的な権限・所有者の変更（`chmod -R`・`chown -R`・`chgrp -R`）・フォークボム・シャットダウン・再起動・全プロセスの終了（`kill -1`・`killall5`）・`crontab -r`|`chmod +x`・`kill <PID>`|
|ネットワークから取得したスクリプトの実行|`curl`・`wget`・`iwr`・`irm` の出力をシェル・インタープリターに渡す実行（`curl … \| sh`・`bash <(curl …)`・`iex (irm …)` など）|`curl … \| jq`・`curl -o <ファイル>`|
|外部への取り消せない操作|`gh repo delete`・`gh release delete`・`gh api -X DELETE`・パッケージの公開（`npm publish`・`cargo publish`・`twine upload`・`nuget push` など）・`docker`・`podman` の `prune`・`terraform destroy`・`kubectl delete`・破壊的なSQL（`DROP DATABASE`・`DROP SCHEMA`・`DROP TABLE`・`TRUNCATE TABLE`）|`gh api <GET>`・`npm install`|

## 前提条件
- Python 3.14以降が必要。見つからない場合や3.14未満の場合は、コマンドを拒否する。

## 例外
- bashとpowershell以外のツール呼び出しは処理対象外。

## 制約と限界
- コマンドの始まりとみなす位置は、行頭（空白を含む）、`;`・`&`・`|`・`(`・`)`・`{`・`}`・`` ` `` の後、およびその位置に続く予約語（`then`・`do`・`else`・`elif`・`if`・`while`・`until`・`!`）、前置きのコマンド（`time`・`env`・`sudo`・`command`・`exec`・`nohup`・`nice`・`builtin`）とそのオプション、変数の代入（`FOO=1`）の後に限る。git以外のコマンドは、パス付きの起動（`/bin/rm`）も検出する。フォークボム・ディスクデバイスへのリダイレクト・ネットワークから取得したスクリプトの実行・破壊的なSQLは、コマンドの始まりに限らずコマンド中のどこにあっても検出する。次の書き方は検出できない。
  - 前置きのコマンドが引数を取るオプション（`env -u NAME git …`・`sudo -u user git …` など）や、ここに挙げていない前置きのコマンド（`xargs git reset` など。`xargs rm` だけは個別に検出する）
  - 変数や文字列の連結で組み立てたコマンド、エイリアス、`bash -c 'git reset'` のように引用符の中で始まるコマンド
  - `git push` の引数に `-f`・`+` を含めず、設定（`remote.<name>.push` の `+`・`push.default` など）で強制pushになる場合
  - `git checkout HEAD <ファイル>` のように `--` を付けずにファイルを上書きする場合
  - いったんファイルに保存したスクリプトを実行する場合（`curl -o x.sh … && sh x.sh`）
- 引用符の中を区別しないため、`git commit -m "fix; git reset is bad"` のように引用符の中に区切りと `git` のコマンドがあると拒否する。引用符の中を除くと `bash -c "cd x; git reset"` を検出できなくなるため、誤拒否として許容する。メッセージを書き換えるか、ファイルから渡す（`git commit -F`）。破壊的なSQLも同じく、`grep "DROP TABLE"` のように文字列として含めるだけで拒否する。

## 備考
- Python未インストール時であっても機能するように、Windowsでは[cmd](./block_commands.cmd)、macOS/Linuxでは[sh](./block_commands.sh)を呼び出す。
- Windows用バッチはコメントも含めASCIIで記述する。UTF-8の日本語コメントは、cmd.exeのコードページによってコメントの一部がコマンドとして解釈されるため、使用しない。

### check_python_version.pyの呼び出しを `call python "%%check_python_version_path%%"` とする理由
- Windowsでは、`python.exe` でなく `python.cmd` が使用される場合がある。
  - 後者の場合、call無しで実行すると呼び出し元に戻らないため、callで実行する必要がある。
- `call` は構文解析時（1度目）と実行時（2度目）の計2回、パーセント記号（%）の変数展開を行う仕様になっている。
  - %%check_python_version_path%% のようにすることで、仮にcheck_python_version_pathの中に文字通り `%USERPROFILE%` のようなフォルダ名が含まれていても、 `call` の2度の展開に耐える、pathの崩れを回避可能。

### block_commands.pyの呼び出しを `python "%~dp0block_commands.py" %*` とする理由
- `call` を使うと、`%*`で渡した引数が再解析され、引数中の `%USERPROFILE%` などが展開される可能性がある。
- `call` を使わない以上、`python.cmd` の場合は戻ってこないが、後続の処理は終了コードを返すのみなので、対応は見送りとする。
