# ETF 봇 — Windows 작업 스케줄러 등록 (로그온 시 시작 + 자동 재시작)
# 실행: PowerShell (관리자 불필요)에서 .\setup_bot_scheduler.ps1

$TaskName   = "ETF_Momentum_Bot"
$ProjectDir = "D:\momentum_etf"
$PythonExe  = "$ProjectDir\venv_mom_etf\Scripts\python.exe"
$Module     = "src.notify.bot"

# 사용자 로그온 시 시작 (현재 사용자만 — 관리자 권한 회피)
$CurrentUser = "$env:USERDOMAIN\$env:USERNAME"
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "-m $Module" `
    -WorkingDirectory $ProjectDir

$Settings = New-ScheduledTaskSettingsSet `
    -RestartCount 99 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)
# ExecutionTimeLimit 0 = 무제한 (long-running 봇)
# RestartCount 99: 봇이 죽으면 1분 후 재시작 (사실상 무한)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $Trigger `
    -Action $Action `
    -Settings $Settings `
    -Force

Write-Host "등록 완료: $TaskName (로그온 시 자동 시작)"
Write-Host ""
Write-Host "지금 시작:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "중지:"
Write-Host "  Stop-ScheduledTask  -TaskName '$TaskName'"
Write-Host "상태:"
Write-Host "  Get-ScheduledTask   -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "삭제:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
