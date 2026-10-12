# check-powershell hook README

## 発動条件
sessionStart（セッション開始時）

## 処理詳細
1. `pwsh`が利用できるか、PowerShellのメジャーバージョンが7以降かを確認する。
2. 条件を満たさない場合はエラーを返し、OSに応じた導入方法を案内する。

## 備考
- 導入や更新は自動実行しない。
- `pwsh` によるps1の呼び出しができないため、特別に[cmd](./check-powershell.cmd), [sh](./check-powershell/check-powershell.sh)を呼び出す。
- Linuxの場合はインストールガイドの案内となる。
