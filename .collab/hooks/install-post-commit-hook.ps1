[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$customHooksPath = (& git -C $root config --get core.hooksPath 2>$null)
if ($LASTEXITCODE -eq 0 -and $customHooksPath) { throw "core.hooksPath가 설정돼 있어 .git/hooks 설치가 적용되지 않습니다. 사용자 결정 후 해당 경로에 별도 설치해야 합니다: $customHooksPath" }
foreach ($hookName in @('post-commit','pre-commit','pre-push')) {
    $source = Join-Path $PSScriptRoot $hookName
    $target = Join-Path $root ('.git\hooks\' + $hookName)
    $backup = $target + '.qms-original'
    if (-not (Test-Path -LiteralPath $source)) { throw "QMS hook 원본을 찾지 못했습니다: $source" }
    if (Test-Path -LiteralPath $target) {
        $existing = [System.IO.File]::ReadAllText($target)
        if ($existing -match 'QMS collaboration (audit hook|pre-commit boundary|pre-push boundary)') { $content = [System.IO.File]::ReadAllText($source); [System.IO.File]::WriteAllText($target, $content, (New-Object System.Text.UTF8Encoding($false))); Write-Host "QMS $hookName hook을 최신 원본으로 갱신했습니다: $target"; continue }
        if (Test-Path -LiteralPath $backup) { throw "기존 hook 백업이 이미 있어 안전하게 체인할 수 없습니다: $backup" }
        Move-Item -LiteralPath $target -Destination $backup -ErrorAction Stop
        $original = "`nORIGINAL=`"$backup`"`nif [ -f `"`$ORIGINAL`" ]; then sh `"`$ORIGINAL`" || exit `$?; fi`n"
        $content = [System.IO.File]::ReadAllText($source) + $original
    } else { $content = [System.IO.File]::ReadAllText($source) }
    [System.IO.File]::WriteAllText($target, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "설치 완료: $target"
}