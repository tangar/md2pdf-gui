# Собирает переносимую версию программы:
#   dist\md2pdf-gui.exe  — однофайловое приложение (Python и Qt внутри);
#   dist\style.css       — оформление, которое можно править без пересборки;
#   dist\README.md       — инструкция;
#   dist\docs, examples  — картинки для README и пример разметки.
#
# Требуется: Python 3.8+, PyQt5, PyInstaller, Pillow (для значка).
# Запуск:  .\build_exe.ps1

param(
    [switch]$SkipIcon
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "1/3  Проверка зависимостей"
python -c "import PyQt5, PyInstaller" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Не установлены PyQt5 и/или PyInstaller: python -m pip install PyQt5 pyinstaller"
}

if (-not $SkipIcon -or -not (Test-Path "assets\md2pdf.ico")) {
    Write-Host "2/3  Значок приложения"
    python tools\make_icon.py
    if ($LASTEXITCODE -ne 0) { throw "Не удалось нарисовать значок" }
}

Write-Host "3/3  Сборка .exe (это занимает минуту-две)"
$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "md2pdf-gui",
    "--icon", "assets\md2pdf.ico",
    "--version-file", "version_info.txt",
    "--add-data", "style.css;.",
    "--exclude-module", "tkinter",
    "--exclude-module", "unittest",
    "--exclude-module", "pydoc",
    "md2pdf_gui.py"
)
python @pyinstallerArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с ошибкой" }

Copy-Item "style.css" "dist\style.css" -Force
Copy-Item "README.md" "dist\README.md" -Force
if (Test-Path "docs") {
    if (Test-Path "dist\docs") { Remove-Item -Recurse -Force "dist\docs" }
    Copy-Item "docs" "dist\docs" -Recurse -Force
}
if (Test-Path "examples") {
    if (Test-Path "dist\examples") { Remove-Item -Recurse -Force "dist\examples" }
    Copy-Item "examples" "dist\examples" -Recurse -Force
}

$exe = Get-Item "dist\md2pdf-gui.exe"
Write-Host ""
Write-Host ("Готово: {0} ({1:N1} МБ)" -f $exe.FullName, ($exe.Length / 1MB))
Write-Host ("Рядом лежит стиль: {0}" -f (Resolve-Path "dist\style.css"))
