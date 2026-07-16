@echo off
setlocal EnableExtensions
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo [ERRO] Execute este arquivo como Administrador.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [ERRO] Ambiente .venv nao encontrado. Execute instalar.bat primeiro.
  pause
  exit /b 1
)

if not exist "config.json" (
  copy /y "config.example.json" "config.json" >nul
  echo [ATENCAO] config.json criado. Configure-o e execute este instalador novamente.
  notepad "config.json"
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :fail

rem Atualiza o servico mesmo quando uma versao anterior ja existe.
".venv\Scripts\python.exe" -m lrautomatic.windows_service stop >nul 2>&1
".venv\Scripts\python.exe" -m lrautomatic.windows_service remove >nul 2>&1
".venv\Scripts\python.exe" -m lrautomatic.windows_service --startup auto install
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m lrautomatic.windows_service start
if errorlevel 1 goto :fail

set "AGENT_CMD=\"%CD%\.venv\Scripts\pythonw.exe\" -m lrautomatic.session_agent \"%CD%\config.json\""

rem Encerra qualquer agente antigo, recria uma unica tarefa e executa agora.
schtasks /End /TN "LRAutomatic Session Agent" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -like '*lrautomatic.session_agent*' } ^| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
schtasks /Create /TN "LRAutomatic Session Agent" /SC ONLOGON /RL HIGHEST /TR "%AGENT_CMD%" /F
if errorlevel 1 goto :fail
schtasks /Run /TN "LRAutomatic Session Agent"
if errorlevel 1 goto :fail

echo.
echo ================================================
echo  LRAutomatic instalado com sucesso
echo  - Servidor da API ja esta rodando
echo  - Home Picz esta sendo consultado agora
echo  - O catalogo correto sera criado ou selecionado
echo  - O Lightroom abrira automaticamente
echo  - O plugin deve iniciar a importacao poucos segundos depois
echo  - Proximas verificacoes seguem o intervalo do config.json
echo.
echo  Para remover, execute: desinstalar_automacao_windows.bat
echo ================================================
pause
exit /b 0

:fail
echo.
echo [ERRO] A instalacao falhou. Veja a mensagem acima.
pause
exit /b 1
