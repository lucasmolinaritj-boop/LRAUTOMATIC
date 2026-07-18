@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ================================================
echo  DESINSTALADOR LRAUTOMATIC
echo ================================================
echo.
echo Este processo remove:
echo  - servidor da API em segundo plano
echo  - tarefa agendada do agente Home Picz
echo  - processos de automacao ainda ativos
echo.
echo NAO remove fotos nem catalogos do Lightroom.
echo.

fltmc >nul 2>&1
if errorlevel 1 (
  echo [INFO] Solicitando permissao de administrador...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set /p CONFIRM=Digite SIM para continuar: 
if /I not "%CONFIRM%"=="SIM" (
  echo Desinstalacao cancelada.
  pause
  exit /b 0
)

echo.
echo [1/5] Encerrando agente Home Picz...
schtasks /End /TN "LRAutomatic Session Agent" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*lrautomatic.session_agent*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo [2/5] Encerrando servidor API...
schtasks /End /TN "LRAutomatic API" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*lrautomatic.cli serve*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo [3/5] Removendo tarefas agendadas...
schtasks /Delete /TN "LRAutomatic Session Agent" /F >nul 2>&1
schtasks /Delete /TN "LRAutomatic API" /F >nul 2>&1

echo [4/5] Removendo servico legado, se existir...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m lrautomatic.windows_service stop >nul 2>&1
  ".venv\Scripts\python.exe" -m lrautomatic.windows_service remove >nul 2>&1
)
sc.exe stop LRAutomatic >nul 2>&1
sc.exe delete LRAutomatic >nul 2>&1

echo [5/5] Limpando marcadores de execucao...
if exist "%LOCALAPPDATA%\LRAutomatic\control\startup_flow.json" del /q "%LOCALAPPDATA%\LRAutomatic\control\startup_flow.json" >nul 2>&1
if exist "%LOCALAPPDATA%\LRAutomatic\control\agent_open_catalog.txt" del /q "%LOCALAPPDATA%\LRAutomatic\control\agent_open_catalog.txt" >nul 2>&1

set /p REMOVEDATA=Deseja apagar tambem fila, logs e configuracoes locais em %%LOCALAPPDATA%%\LRAutomatic? [S/N]: 
if /I "%REMOVEDATA%"=="S" (
  rmdir /s /q "%LOCALAPPDATA%\LRAutomatic" >nul 2>&1
  echo Dados locais removidos.
) else (
  echo Dados locais preservados para diagnostico ou reinstalacao.
)

echo.
echo ================================================
echo  LRAutomatic removido com sucesso
echo  Fotos e catalogos foram preservados.
echo ================================================
pause
exit /b 0
