<#
  *** ADMIN SCRIPT: CHANGES SYSTEM SETTINGS. DRY RUN UNLESS -Apply IS GIVEN. ***

  Batches the approved Windows system trims behind ONE UAC prompt. Pass only the switches the
  owner said yes to. Proven sequence; each part is optional:
    -CapRestoreGB N     cap System Restore storage on the system drive at N GB. Windows deletes the
                        OLDEST restore points beyond the cap. Restore keeps working.
    -ReducedHiberfil    powercfg /h /type reduced: hibernation file about half size. Fast Startup
                        keeps working; full hibernate (the "Hibernate" power option) does not.
    -CompactVhd PATH    compact a WSL or Docker .vhdx with diskpart (attach readonly, compact, detach).
                        Several paths: separate them with ';'. PRECONDITIONS (references/wsl.md):
                        files deleted inside the distro, fstrim run, wsl --shutdown done, a read-only
                        fsck came back clean. This script re-checks that no distro is running and the
                        file is not in use, and refuses otherwise.
    -RunDir DIR         where admin-log.txt and admin.done are written (required).

  Dry run first, from a normal shell (no admin needed):
    powershell -NoProfile -ExecutionPolicy Bypass -File admin_maintenance.ps1 -CapRestoreGB 10 -ReducedHiberfil -RunDir <run>
  Then, after the owner's yes, ONE elevated run (they click Yes on the UAC prompt):
    Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','"<this file>"','-Apply','-CapRestoreGB','10','-ReducedHiberfil','-RunDir','"<run>"'
  Wait for <run>\admin.done (written even on failure), then read <run>\admin-log.txt.

  Never used here on purpose: wsl --manage --set-sparse (reports of corruption), deleting restore
  points by hand, turning hibernation fully off without asking, touching WinSxS or Installer.
#>
param(
  [switch]$Apply,
  [int]$CapRestoreGB = 0,
  [switch]$ReducedHiberfil,
  [string]$CompactVhd = '',
  [Parameter(Mandatory = $true)][string]$RunDir
)
$zzSysDrive = $env:SystemDrive
if (-not $zzSysDrive) { $zzSysDrive = 'C:' }
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$zzDoneFile = Join-Path -Path $RunDir -ChildPath 'admin.done'
$zzLogFile = Join-Path -Path $RunDir -ChildPath 'admin-log.txt'
if ($Apply) { Remove-Item -LiteralPath $zzDoneFile -ErrorAction SilentlyContinue }
function Get-ZzFreeGB { [math]::Round((Get-PSDrive -Name $zzSysDrive.TrimEnd(':')).Free / 1GB, 2) }

# diskpart reads its /s script in the system ANSI code page (Windows PowerShell 5.1's -Encoding Default).
# The code page comes from the registry so PowerShell 7, where "Default" means UTF-8, writes the same bytes.
$zzAnsi = [Text.Encoding]::Default
try { $zzAnsi = [Text.Encoding]::GetEncoding([int](Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage' -Name ACP -ErrorAction Stop).ACP) } catch { }
function Write-ZzAnsiLines([string]$zzFile, [string[]]$zzLines) {
  [IO.File]::WriteAllBytes($zzFile, $zzAnsi.GetBytes(($zzLines -join "`r`n") + "`r`n"))
}

function Test-ZzVhdReady([string]$zzPath) {
  if (-not ($zzPath -match '\.vhdx$')) { return "not a .vhdx file" }
  if ($zzAnsi.GetString($zzAnsi.GetBytes($zzPath)) -cne $zzPath) {
    return "the path has characters the system code page ($($zzAnsi.WebName)) can't hold, so a diskpart script can't name it. Compact this one by hand in an elevated diskpart session"
  }
  if (-not (Test-Path -LiteralPath $zzPath)) { return "file not found" }
  $env:WSL_UTF8 = '1'
  $zzRunning = @(& wsl.exe --list --running --quiet 2>$null | Where-Object { $_.Trim() })
  if ($zzRunning.Count) { return "WSL distros still running ($($zzRunning -join ', ')): run wsl --shutdown first (with the owner's yes)" }
  try { $zzStream = [IO.File]::Open($zzPath, 'Open', 'Read', 'None'); $zzStream.Close() } catch { return "file is in use (Docker Desktop, an Explorer window on \\wsl.localhost, a terminal tab?)" }
  return ''
}

$zzBody = {
  $zzMode = if ($Apply) { 'APPLY' } else { 'DRY RUN (nothing changes)' }
  "=== $zzMode  $(Get-Date)  free on ${zzSysDrive} $(Get-ZzFreeGB) GB"
  $zzAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if ($Apply -and -not $zzAdmin) { "ABORT: -Apply needs an elevated shell (launch it with Start-Process -Verb RunAs)"; return }

  if ($CapRestoreGB -gt 0) {
    "--- restore points: cap shadow storage on $zzSysDrive at $CapRestoreGB GB"
    $zzNow = & vssadmin.exe list shadowstorage "/for=$zzSysDrive" 2>&1 | Select-String -Pattern 'Used Shadow|Maximum Shadow'
    if ($zzNow) { $zzNow | ForEach-Object { "  now: " + $_.Line.Trim() } } else { "  now: unreadable without admin" }
    if ($Apply) {
      & vssadmin.exe resize shadowstorage "/for=$zzSysDrive" "/on=$zzSysDrive" "/maxsize=$($CapRestoreGB)GB"
      & vssadmin.exe list shadowstorage "/for=$zzSysDrive" | Select-String -Pattern 'Used Shadow|Maximum Shadow' | ForEach-Object { "  after: " + $_.Line.Trim() }
      "  free on ${zzSysDrive} $(Get-ZzFreeGB) GB"
    } else { "  would run: vssadmin resize shadowstorage /for=$zzSysDrive /on=$zzSysDrive /maxsize=$($CapRestoreGB)GB" }
  }

  if ($ReducedHiberfil) {
    "--- hibernation file: reduced"
    $zzHib = Get-ChildItem -LiteralPath "$zzSysDrive\" -Force -Filter 'hiberfil.sys' -ErrorAction SilentlyContinue
    if ($zzHib) { "  now: hiberfil.sys {0:N1} GB" -f ($zzHib.Length / 1GB) } else { "  now: no hiberfil.sys (hibernation already off)" }
    if (-not $zzHib) { "  skipped: hibernation is off, and this switch must not turn it back on" }
    elseif ($Apply) {
      & powercfg.exe /h /type reduced
      Start-Sleep -Seconds 3
      "  free on ${zzSysDrive} $(Get-ZzFreeGB) GB"
    } else { "  would run: powercfg /h /type reduced" }
  }

  foreach ($zzVhd in @($CompactVhd -split ';' | Where-Object { $_.Trim() })) {
    $zzVhd = $zzVhd.Trim()
    "--- compact $zzVhd"
    $zzProblem = Test-ZzVhdReady $zzVhd
    if ($zzProblem) { "  REFUSED: $zzProblem"; continue }
    "  size now: {0:N2} GB" -f ((Get-Item -LiteralPath $zzVhd).Length / 1GB)
    if (-not $Apply) { "  would run diskpart: select vdisk / attach vdisk readonly / compact vdisk / detach vdisk"; continue }
    $zzScript = Join-Path -Path $RunDir -ChildPath 'compact.diskpart.txt'
    $zzDetach = Join-Path -Path $RunDir -ChildPath 'detach.diskpart.txt'
    Write-ZzAnsiLines $zzScript @("select vdisk file=`"$zzVhd`"", 'attach vdisk readonly', 'compact vdisk', 'detach vdisk', 'exit')
    Write-ZzAnsiLines $zzDetach @("select vdisk file=`"$zzVhd`"", 'detach vdisk', 'exit')
    & diskpart.exe /s $zzScript | Where-Object { $_ -notmatch 'percent completed' }
    "  diskpart exit code: $LASTEXITCODE"
    "  safety detach (an error here only means it is already detached):"
    & diskpart.exe /s $zzDetach | Select-String -Pattern 'detach|error' | ForEach-Object { "    " + $_.Line.Trim() }
    "  size after: {0:N2} GB" -f ((Get-Item -LiteralPath $zzVhd).Length / 1GB)
    "  NEXT: run the read-only fsck again and a boot test (references/wsl.md) before calling it done"
  }
  "=== done $(Get-Date)  free on ${zzSysDrive} $(Get-ZzFreeGB) GB"
}

try {
  & $zzBody *>&1 | Tee-Object -FilePath $zzLogFile -Append
}
finally {
  if ($Apply) { Set-Content -LiteralPath $zzDoneFile -Value (Get-Date).ToString('s') }
}
