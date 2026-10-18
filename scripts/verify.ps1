# 验证 .bat / .ps1 文件编码、行尾、BOM、非 ASCII 字节、PS 语法是否符合规范
# 用法: powershell -File verify.ps1 -TargetDir <DIR>

param(
    [Parameter(Mandatory=$true)][string]$TargetDir
)

$utf8Bom = [byte[]](0xEF, 0xBB, 0xBF)
$gbk = [System.Text.Encoding]::GetEncoding(936)
$pass = 0; $fail = 0

function Test-FileEncoding {
    param($path, $expectedEncoding, $expectBom)
    $b = [System.IO.File]::ReadAllBytes($path)
    $hasBom = ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
    $issues = @()

    if ($expectBom -and -not $hasBom) { $issues += "Missing BOM" }
    if (-not $expectBom -and $hasBom) { $issues += "Unexpected BOM" }

    $lf = 0
    for ($i = 0; $i -lt $b.Length; $i++) {
        if ($b[$i] -eq 0x0A -and ($i -eq 0 -or $b[$i-1] -ne 0x0D)) { $lf++ }
    }
    if ($lf -gt 0) { $issues += "$lf orphan LF line endings" }

    if ($expectedEncoding -eq 'ASCII') {
        $nonAscii = 0
        foreach ($byte in $b) {
            if ($byte -gt 127) { $nonAscii++ }
        }
        if ($nonAscii -gt 0) { $issues += "$nonAscii non-ASCII bytes (must be 0 for .bat)" }
    }

    if ($expectedEncoding -eq 'GBK') {
        try { $null = $gbk.GetString($b) } catch { $issues += "Cannot decode as GBK" }
    }

    return @{ issues = $issues; hasBom = $hasBom; lf = $lf }
}

function Test-PsSyntax {
    param($path)
    $errors = $null; $tokens = $null
    try {
        [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors) | Out-Null
        if ($errors -and $errors.Count -gt 0) {
            return ($errors | ForEach-Object { $_.Message }) 
        }
        return @()
    } catch {
        return @("Parser exception: $_")
    }
}

# 验证所有 .bat（纯 ASCII + 无 BOM）
Get-ChildItem $TargetDir -Filter '*.bat' -Recurse | ForEach-Object {
    $r = Test-FileEncoding $_.FullName 'ASCII' $false
    if ($r.issues.Count -eq 0) { Write-Host "[PASS] $($_.Name) (encoding)" -ForegroundColor Green; $script:pass++ }
    else { Write-Host "[FAIL] $($_.Name): $($r.issues -join ', ')" -ForegroundColor Red; $script:fail++ }
}

# 验证所有 .ps1（UTF-8 + BOM + PS 语法）
Get-ChildItem $TargetDir -Filter '*.ps1' -Recurse | ForEach-Object {
    $r = Test-FileEncoding $_.FullName 'UTF8' $true
    $syn = Test-PsSyntax $_.FullName
    $all = @() + $r.issues + $syn
    if ($all.Count -eq 0) { Write-Host "[PASS] $($_.Name) (encoding + syntax)" -ForegroundColor Green; $script:pass++ }
    else { Write-Host "[FAIL] $($_.Name): $($all -join ', ')" -ForegroundColor Red; $script:fail++ }
}

Write-Host ""
Write-Host "Total: $pass PASS, $fail FAIL" -ForegroundColor $(if ($fail -eq 0) { 'Green' } else { 'Red' })
exit $(if ($fail -eq 0) { 0 } else { 1 })
