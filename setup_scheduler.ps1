# ETF 모멘텀 스크리너 — Windows 작업 스케줄러 등록
# 실행: PowerShell을 관리자 권한으로 열고 .\setup_scheduler.ps1

$TaskName   = "ETF_Momentum_Screener"
$ProjectDir = "D:\momentum_etf"
$PythonExe  = "$ProjectDir\venv_mom_etf\Scripts\python.exe"
$Script     = "$ProjectDir\main.py"

# 평일 오전 8:50 실행 (장 시작 10분 전)
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At "08:50"

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $Script `
    -WorkingDirectory $ProjectDir

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $Trigger `
    -Action $Action `
    -Settings $Settings `
    -RunLevel Highest `
    -Force

Write-Host "등록 완료: $TaskName (평일 08:50)"
Write-Host "즉시 테스트 실행하려면:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
