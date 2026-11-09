# 一键打包：单文件 exe + 绿色版 zip
# 用法：powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$icon = Join-Path $root 'opendl\icon.ico'
$version = '1.0.0'
$targets = @(
    @{ out = 'onefile';  flag = '--onefile' },
    @{ out = 'portable'; flag = '--onedir'  }
)

Push-Location $root
try {
    foreach ($target in $targets) {
        $dist = Join-Path $root ("dist\" + $target.out)
        $work = Join-Path $root ("build\" + $target.out)
        Write-Host ("==> 打包 " + $target.out)
        python -m PyInstaller --noconfirm --clean $target.flag --noconsole --name OpenDL --icon $icon --add-data "$icon;." --paths . --distpath $dist --workpath $work --specpath (Join-Path $root 'build') 'opendl_exe_entry.py'
        if ($LASTEXITCODE -ne 0) { throw ("PyInstaller 失败：" + $target.out) }
    }

    $exeOut = Join-Path $root "dist\OpenDL-$version-windows-x64.exe"
    Copy-Item (Join-Path $root 'dist\onefile\OpenDL.exe') $exeOut -Force

    $portableDir = Join-Path $root 'dist\portable\OpenDL'
    Set-Content -Path (Join-Path $portableDir 'portable.txt') -Value 'portable mode' -Encoding UTF8
    $zipOut = Join-Path $root "dist\OpenDL-$version-windows-x64-portable.zip"
    Compress-Archive -Path $portableDir -DestinationPath $zipOut -Force

    Write-Host ''
    Write-Host '完成：'
    Get-ChildItem (Join-Path $root 'dist') -File | Select-Object Name, @{n='MB'; e={ [math]::Round($_.Length/1MB, 2) }}
}
finally {
    Pop-Location
}