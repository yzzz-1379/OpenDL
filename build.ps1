# 一键打包：单文件 exe + 绿色版 zip
# 用法：powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$icon = Join-Path $root 'opendl\icon.ico'
$version = '1.0.0'

Push-Location $root
try {
    foreach ($mode in @('onefile', 'onedir')) {
        $dist = Join-Path $root "dist\$mode"
        $work = Join-Path $root "build\$mode"
        $flag = if ($mode -eq 'onefile') { '--onefile' } else { '--onedir' }
        Write-Host "==> 打包 $mode"
        python -m PyInstaller --noconfirm --clean $flag --noconsole --name OpenDL `
            --icon $icon --add-data "$icon;." --paths . `
            --distpath $dist --workpath $work --specpath (Join-Path $root 'build') `
            'opendl_exe_entry.py'
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（$mode）" }
    }

    # 单文件版
    $exeOut = Join-Path $root "dist\OpenDL-$version-windows-x64.exe"
    Copy-Item (Join-Path $root 'dist\onefile\OpenDL.exe') $exeOut -Force

    # 绿色版：带 portable.txt，配置写在程序目录
    $portableDir = Join-Path $root 'dist\portable\OpenDL'
    Set-Content -Path (Join-Path $portableDir 'portable.txt') -Value 'portable mode' -Encoding UTF8
    $zipOut = Join-Path $root "dist\OpenDL-$version-windows-x64-portable.zip"
    Compress-Archive -Path $portableDir -DestinationPath $zipOut -Force

    Write-Host "`n完成："
    Get-ChildItem (Join-Path $root 'dist') -File | Select-Object Name, @{n='MB'; e={ [math]::Round($_.Length/1MB, 2) } }
}
finally {
    Pop-Location
}
