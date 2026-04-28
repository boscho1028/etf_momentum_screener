# ETF 모멘텀 스크리너 — Windows 작업 스케줄러 등록
# 실행: PowerShell에서 .\setup_scheduler.ps1 (관리자 권한 불필요)

$TaskName   = "ETF_Momentum_Screener"
$ProjectDir = "D:\momentum_etf"
$PythonExe  = "$ProjectDir\venv_mom_etf\Scripts\python.exe"
$Script     = "$ProjectDir\main.py"

# 평일 오전 8:00 실행
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At "08:00"

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $Script `
    -WorkingDirectory $ProjectDir

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
# StartWhenAvailable: PC가 꺼져 있어 놓친 실행을 켜자마자 보충
# AllowStartIfOnBatteries: 노트북 배터리 상태에서도 실행

Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $Trigger `
    -Action $Action `
    -Settings $Settings `
    -Force

Write-Host "등록 완료: $TaskName (평일 08:00)"
Write-Host ""
Write-Host "확인:"
Write-Host "  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "즉시 테스트 실행:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "삭제:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
