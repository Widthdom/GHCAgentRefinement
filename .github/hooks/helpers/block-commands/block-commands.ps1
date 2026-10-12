param(
    # ブロック対象定義ファイルは引数として渡すことが可能
    # デフォルトは同ディレクトリの 'blocked-command-patterns.json'
    [string]$DefinitionsPath = (Join-Path $PSScriptRoot 'blocked-command-patterns.json')
)

# ブロック対象定義ファイルが無ければ異常終了。
#   -LiteralPath を指定することで引数をワイルドカードとして解釈せず、文字どおりのパスとして扱う。
#   -PathType Leaf を指定することで、ファイルとして存在するかどうかを確認する。
if (-not (Test-Path -LiteralPath $DefinitionsPath -PathType Leaf)) {
    [System.Console]::Error.WriteLine("Blocked command definitions not found: $DefinitionsPath")
    exit 2
}

# ブロック対象定義ファイルを読み込む。
#   -Raw を指定することで、ファイルを行ごとに分けず、全体をひとつの文字列として読む。
try {
    $definitions = Get-Content -LiteralPath $DefinitionsPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    [System.Console]::Error.WriteLine("Failed to parse blocked command definitions: $($_.Exception.Message)")
    exit 2
}

# 各定義を検証する。
foreach ($definition in $definitions) {
    # 必須項目に欠落があれば異常終了。
    if ([string]::IsNullOrWhiteSpace($definition.name) -or
        [string]::IsNullOrWhiteSpace($definition.pattern) -or
        [string]::IsNullOrWhiteSpace($definition.reason)) {
        [System.Console]::Error.WriteLine('Each blocked command definition requires name, pattern, and reason.')
        exit 2
    }
    # 正規表現が正しく書けているか検証する。
    try {
        # 正規表現オブジェクトのコンストラクタに渡し、例外発生有無を以て妥当性を検証するため、戻り値は使用しない。
        #   CultureInvariantは正規表現の判定結果が実行環境の言語・地域設定の影響を抑制するオプションである。
        $null = [System.Text.RegularExpressions.Regex]::new(
            $definition.pattern,
            [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
        )
    } catch {
        [System.Console]::Error.WriteLine(
            "Invalid regular expression in blocked command definition '$($definition.name)': $($_.Exception.Message)"
        )
        exit 2
    }
}

# PowerShellの自動設定変数ErrorActionPreferenceにStopを設定すると、
# 以降のコマンドにおけるエラー処理が（既定処理に上書きで）「停止」になる。
$ErrorActionPreference = 'Stop'

# 標準入力をUTF-8として復号化し、JSON内の日本語などを正しく読み取る。
#   左辺はSystem.Consoleクラスに属する静的メンバInputEncodingにアクセスしていることを意味する。
[System.Console]::InputEncoding = [System.Text.UTF8Encoding]::new()

# 標準出力をUTF-8に統一することで、先頭にBOMが含まれJSON解析を妨げることのないようにする。
#   右辺はSystem.Text.UTF8Encodingクラスのインスタンス化。コンストラクタの引数にfalseを渡すとエンコーダによるBOMの出力を無効にする。
[System.Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# preToolUseのタイミングで本スクリプトの呼び出し元であるCopilotがPowerShellプロセスを起動し
# 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
# このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
$inputJson = [System.Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($inputJson)) {
    [System.Console]::Error.WriteLine('preToolUse hook input is empty.')
    exit 2
}

# 読み取ったJSONをPowerShellのオブジェクトに変換する。
#   -NoEnumerate を指定することで最外側の配列を展開せず、そのまま保持する。
#     [{"toolName":"bash", ...}]のように最外側が[]のものは拒否したいが、
#     -NoEnumerate なしだと展開され拒否条件を満たさなくなってしまう。
try {
    $hookInput = $inputJson | ConvertFrom-Json -NoEnumerate
} catch {
    [System.Console]::Error.WriteLine("Failed to parse preToolUse hook input: $($_.Exception.Message)")
    exit 2
}

# 変換結果がPSCustomObjectでなければ異常終了。
#   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
if ($hookInput -isnot [System.Management.Automation.PSCustomObject]) {
    [System.Console]::Error.WriteLine('preToolUse hook input must be a JSON object.')
    exit 2
}

# `toolCalls` が存在する場合はその配列を、存在しない場合は単体入力を配列に変換したものを `$toolCalls` に格納する。
# $null -ne $hookInput.PSObject.Properties['toolCalls'] のようにYoda記法で書くのが慣用的。以下のようなPowershellの仕様があるためである。
#   PowerShellでは、比較演算子は左辺が配列の場合に条件に合う要素を抽出する動きをする。
#   ```powershell
#   $items = @('a', $null, 'b')
#   $items -eq 'a'    # 'a' が返る。$trueではない
#   $items -ne $null  # 'a' と 'b' が返る
#   ```
if ($null -ne $hookInput.PSObject.Properties['toolCalls']) {
    if ($hookInput.toolCalls -isnot [array] -or $hookInput.toolCalls.Count -eq 0) {
        [System.Console]::Error.WriteLine('preToolUse hook toolCalls must be a non-empty array.')
        exit 2
    }
    $toolCalls = $hookInput.toolCalls
} else {
    # 外側の @() は配列部分式で、単体入力も1要素の配列として保持する。
    # 内側の @{} はハッシュテーブルを作り、[PSCustomObject]でnameとargsを持つオブジェクトに変換する。
    # ここで[System.Management.Automation.PSCustomObject]でなく、型アクセラレータ（型を短く書くための別名）でなければならない。
    #   単なる型名の短縮のみならず、ハッシュテーブルを変換する特殊処理が行われる。
    $toolCalls = @(
        [PSCustomObject]@{
            name = $hookInput.toolName
            args = $hookInput.toolArgs
        }
    )
}

# 空の配列として初期化
$commands = @()
# 複数のツール呼び出しをまとめて要求される場合があるため、ひとつずつ処理する。
foreach ($toolCall in $toolCalls) {
    # 各toolCallがPSCustomObjectでなければ異常終了。
    if ($toolCall -isnot [System.Management.Automation.PSCustomObject]) {
        [System.Console]::Error.WriteLine('preToolUse hook requires each tool call to be a JSON object.')
        exit 2
    }

    # 各toolCallのnameが文字列でなければ異常終了。空白のみの文字列も許容しない。
    if ($toolCall.name -isnot [string] -or
        [string]::IsNullOrWhiteSpace($toolCall.name)) {
        [System.Console]::Error.WriteLine('preToolUse hook requires a tool name for each tool call.')
        exit 2
    }

    # 以下に一致しない場合はスキップする。
    #   bash
    #   powershell
    #   functions.bash
    #   functions.powershell
    if ($toolCall.name -notmatch '^(?:functions\.)?(?:bash|powershell)$') {
        continue
    }

    $toolArgs = $toolCall.args
    $command = if ($toolArgs -is [string]) {
        $toolArgs
    } elseif ($toolArgs -is [System.Management.Automation.PSCustomObject] -and $toolArgs.command -is [string]) {
        # PSCustomObjectの型チェックが実質的にnullチェックも兼ねている。
        # ちなみにPowerShellの-andは短絡評価なので, 前半がfalseなら後半は評価されない。
        $toolArgs.command
    } else {
        $null
    }

    if ([string]::IsNullOrWhiteSpace($command)) {
        [System.Console]::Error.WriteLine(
            "preToolUse hook input for tool '$($toolCall.name)' requires a non-empty command string."
        )
        exit 2
    }

    # $commandを末尾に追加した新しい配列を$commandsへ代入
    $commands += $command
}

# コマンドが各定義のパターンに一致するかを順に確認する。
foreach ($command in $commands) {
    foreach ($definition in $definitions) {
        try {
            $isMatch = [System.Text.RegularExpressions.Regex]::IsMatch(
                $command,
                $definition.pattern,
                [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
            )
        } catch {
            # 極めて稀なケースではあるが、正規表現の評価中に例外が発生する可能性があるため、ここで捕捉する。
            [System.Console]::Error.WriteLine(
                "Failed to match command against regular expression in blocked command definition '$($definition.name)': $($_.Exception.Message)"
            )
            exit 2
        }

        if ($isMatch) {
            # @{}はハッシュテーブルを作る構文。項目名と値を組にして保持する。
            #   拒否とその理由をハッシュテーブルにまとめ、JSON形式に変換して返却する。
            #     -Compress はJSONの余分な空白や改行を省き、1行のJSONにする指定
            @{
                permissionDecision = 'deny'
                permissionDecisionReason = $definition.reason
            } | ConvertTo-Json -Compress
            exit 0
        }
    }
}

# 標準出力へ空のJSONオブジェクトを返すことで、「拒否する判断はない」ことを呼び出し元に伝える。
'{}'
