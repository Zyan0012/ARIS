param(
    [string]$Project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$CodexHome = "$env:USERPROFILE\.codex",
    [string[]]$SourceRoot = @("D:\ML\research_code", "E:\paper")
)

$ErrorActionPreference = "Stop"
$Wrapper = Join-Path $PSScriptRoot "codex_aris_log_wrapper.py"
$SourceArgs = @()
foreach ($Root in $SourceRoot) {
    if (Test-Path -LiteralPath $Root) {
        $SourceArgs += @("--source-root", $Root)
    }
}
python $Wrapper --sync-sessions --project $Project --codex-home $CodexHome --include-other-projects --aris-related-only @SourceArgs
