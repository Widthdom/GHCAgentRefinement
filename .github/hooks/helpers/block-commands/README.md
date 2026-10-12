# block-commands hook README

## 発動条件
preToolUse（bashまたはpowershellツールの実行前）

## 処理詳細
1. ツール呼び出しのコマンドを読み取る。
2. `blocked-command-patterns.json` に定義された正規表現と照合する。
3. 一致した場合は実行を拒否し、定義に記載された理由を返す。一致しない場合は拒否しない。

## 備考
- bashとpowershell以外のツール呼び出しは処理対象外。
