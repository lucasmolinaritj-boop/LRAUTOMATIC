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

".venv\Scripts\python.exe" -m lrautomatic.windows_service --startup auto install
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m lrautomatic.windows_service start
if errorlevel 1 goto :fail

set "AGENT_CMD=\"%CD%\.venv\Scripts\pythonw.exe\" -m lrautomatic.session_agent \"%CD%\config.json\""

rem Recria a tarefa para garantir que exista apenas uma instancia gerenciada.
schtasks /End /TN "LRAutomatic Session Agent" >nul 2>&1
schtasks /Create /TN "LRAutomatic Session Agent" /SC ONLOGON /RL HIGHEST /TR "%AGENT_CMD%" /F
if errorlevel 1 goto :fail

rem Executa agora; nao espera novo logon nem reinicializacao.
schtasks /Run /TN "LRAutomatic Session Agent"
if errorlevel 1 goto :fail

echo.
echo ================================================
echo  LRAutomatic instalado com sucesso
echo  - Servico da API iniciado agora e no Windows
echo  - Home Picz faz a primeira verificacao imediatamente
echo  - Novas verificacoes seguem o intervalo do config.json
echo  - Agente de sessao abre ou troca o catalogo correto
echo  - Uma unica instancia do agente fica ativa
echo ================================================
pause
exit /b 0

:fail
echo.
echo [ERRO] A instalacao falhou. Veja a mensagem acima.
pause
exit /b 1
