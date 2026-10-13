param(
    # ValidateSet は直後の引数に設定可能な値に制限を加える。
    [ValidateSet('Auto', 'Windows', 'MacOS', 'Linux')] [string]$Platform = 'Auto',
    [string]$WingetCommand = 'winget',
    [string]$BrewCommand = 'brew',
    [string]$AptCommand = 'apt-cache',
    [string]$DnfCommand = 'dnf',
    [string]$PacmanCommand = 'pacman'
)

# PowerShellの自動設定変数ErrorActionPreferenceにStopを設定すると、
# 以降のコマンドにおけるエラー処理が（既定処理に上書きで）「停止」になる。
$ErrorActionPreference = 'Stop'

# 冒頭のドットは、指定したスクリプトを現在のスコープで実行することを意味する。
#   ドットなしで実行すると、スクリプトは新しいスコープで実行され、その中で定義された関数や変数は終了時に破棄される。
. (Join-Path $PSScriptRoot 'common.ps1')

$resolvedPlatform = Get-DevelopmentPlatform -Platform $Platform

# $PSBoundParametersはPowerShellの自動変数で、現在の関数やスクリプトに渡された引数をハッシュテーブルとして保持している。
# 先頭の @ は スプラッティングを表す。配列やハッシュテーブルの中身を、コマンドの引数として展開して渡す。
#   例えば以下の行は、実質的には `Get-DevelopmentPackageManager -Platform AAA -BrewCommand BBB` に等しい。
$packageManager = Get-DevelopmentPackageManager @PSBoundParameters

$packageId = if ($resolvedPlatform -eq 'Windows') { 'BurntSushi.ripgrep.MSVC' } else { 'ripgrep' }

$installedRipgrepVersion = Get-ToolVersion `
    -Command 'rg' `
    -Arguments @('--version') `
    -Pattern 'ripgrep (?<version>\d+(?:\.\d+){2,3})'

# ripgrepのVersionが取得できなかった場合の処理。
if ($null -eq $installedRipgrepVersion) {
    # パッケージマネージャの有無によってインストール方法の案内を変える
    if ($null -eq $packageManager) {
        "- ripgrep: not installed. No supported package manager is available for $resolvedPlatform, so no install command can be suggested."
    } else {
        $ripgrepInstallCommand = Get-PackageInstallCommand -PackageManager $packageManager -PackageId $packageId
        "- ripgrep: not installed. Install: $ripgrepInstallCommand"
    }
    return
}

# VS Code同梱のripgrepかどうかを判定するための処理。
$vsCodePackageId = if ($installedRipgrepVersion.Source -match '[\\/]Microsoft VS Code Insiders[\\/]') {
    'Microsoft.VisualStudioCode.Insiders'
} elseif ($installedRipgrepVersion.Source -match '[\\/]Microsoft VS Code(?:\.app)?[\\/]') {
    'Microsoft.VisualStudioCode'
} else {
    $null
}

# VS Code同梱のripgrepであった場合の処理。
if ($null -ne $vsCodePackageId) {
    # Windowsでない、またはパッケージマネージャが利用できない場合は終了。
    if ($resolvedPlatform -ne 'Windows' -or $null -eq $packageManager) {
        "- ripgrep: available through VS Code ($($installedRipgrepVersion.Display)). Update VS Code through its built-in updater to keep the bundled version current."
        return
    }

    # 以下からは、Windowsであり、かつパッケージマネージャが利用できる場合のみ実行される。
    $IsVsCodeManaged = Test-PackageManaged -PackageManager $packageManager -PackageId $vsCodePackageId
    if ($IsVsCodeManaged -and
        (Test-WingetPackageUpdateAvailable -WingetCommand $WingetCommand -PackageId $vsCodePackageId)) {
        "- ripgrep: available through VS Code ($($installedRipgrepVersion.Display)); a VS Code update is available. Update: winget upgrade --id $vsCodePackageId --exact --source winget"
    }
    return
}

# 以下からはVS Code同梱でないripgrepであった場合のみ実行される。
if ($null -eq $packageManager) {
    # パッケージマネージャが利用できない場合はここで終了する。
    "- ripgrep: installed ($($installedRipgrepVersion.Display)), but the latest version could not be checked because no supported package manager is available for $resolvedPlatform."
    return
}

# 以下からはVS Code同梱でないripgrepであり、かつパッケージマネージャが利用できる場合のみ実行される。
$latestRipgrepVersion = Get-LatestPackageVersion -PackageManager $packageManager -PackageId $packageId
if ($null -eq $latestRipgrepVersion) {
    # 最新バージョンが取得できなかった場合はここで終了する。
    "- ripgrep: installed ($($installedRipgrepVersion.Display)), but the latest version check failed."
    return
}

# -lt は less than の略。
if ($installedRipgrepVersion.Value -lt $latestRipgrepVersion.Value) {
    $isRipgrepManaged = Test-PackageManaged -PackageManager $packageManager -PackageId $packageId
    $ripgrepInstallCommand = Get-PackageInstallCommand `
        -PackageManager $packageManager `
        -PackageId $packageId `
        -Upgrade:$isRipgrepManaged

    "- ripgrep: update available ($($installedRipgrepVersion.Display) -> $($latestRipgrepVersion.Display)). Update: $ripgrepInstallCommand"
}
