# ==============================================================================
# Mail Agent — One-Click Deployment Script for Windows PowerShell
# Usage:
#   .\deploy.ps1
# ==============================================================================

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "   MAIL AGENT — STARTING ONE-CLICK DEPLOYMENT WORKFLOW            " -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

python deploy.py

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n[SUCCESS] Deploy hoan tat thanh cong!" -ForegroundColor Green
} else {
    Write-Host "`n[ERROR] Deploy that bai voi ma loi $LASTEXITCODE" -ForegroundColor Red
}
