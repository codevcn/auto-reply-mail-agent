# PowerShell E2E Test Execution Wrapper
param (
    [string]$Tier = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Tier -ne "") {
    python "$ScriptDir\run_e2e_tests.py" --tier $Tier -v
} else {
    python "$ScriptDir\run_e2e_tests.py" -v
}
