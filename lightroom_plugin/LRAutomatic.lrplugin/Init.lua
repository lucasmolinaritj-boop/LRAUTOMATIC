local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'

_G.LRAutomaticShutdown = false
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticVersion = '0.2.1-instrumented'
_G.LRAutomaticLastError = nil

local okDebug, Debug = pcall(require, 'DebugLog')
if not okDebug then
    return
end

local function catalogPath()
    local ok, value = pcall(function()
        local catalog = LrApplication.activeCatalog()
        return catalog and catalog:getPath() or '(nenhum catálogo ativo)'
    end)
    return ok and tostring(value) or ('erro: ' .. tostring(value))
end

Debug.info('bootstrap_enter', 'version=' .. _G.LRAutomaticVersion)
Debug.info('plugin_path', tostring(_PLUGIN and _PLUGIN.path or '(indisponível)'))
Debug.info('localappdata', tostring(os.getenv('LOCALAPPDATA')))
Debug.info('active_catalog', catalogPath())
Debug.writeState('bootstrap.txt', 'OK\nversion=' .. _G.LRAutomaticVersion .. '\nplugin_path=' .. tostring(_PLUGIN and _PLUGIN.path or '') .. '\ncatalog=' .. catalogPath())

LrTasks.startAsyncTask(function()
    Debug.info('async_task_enter', 'carregando JobRunner')
    local okRequire, Runner = pcall(require, 'JobRunner')
    if not okRequire then
        _G.LRAutomaticLastError = tostring(Runner)
        Debug.error('jobrunner_require_failed', tostring(Runner))
        Debug.writeState('fatal_error.txt', tostring(Runner))
        return
    end

    _G.LRAutomaticLoopRunning = true
    Debug.info('loop_start', 'monitoramento iniciado')
    Debug.heartbeat('loop=running\ncatalog=' .. catalogPath())

    local okLoop, err = xpcall(function()
        Runner.runLoop(function()
            return _G.LRAutomaticShutdown == true
        end)
    end, function(message)
        return debug.traceback(tostring(message), 2)
    end)

    _G.LRAutomaticLoopRunning = false
    if not okLoop then
        _G.LRAutomaticLastError = tostring(err)
        Debug.error('loop_crashed', tostring(err))
        Debug.writeState('fatal_error.txt', tostring(err))
    else
        Debug.info('loop_stop', 'encerrado normalmente')
    end
end)
