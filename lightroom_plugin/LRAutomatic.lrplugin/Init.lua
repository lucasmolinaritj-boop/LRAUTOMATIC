local LrApplication = import 'LrApplication'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'

_G.LRAutomaticShutdown = false
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticVersion = '0.2.2-lr104-pathfix'
_G.LRAutomaticLastError = nil

-- Lightroom 10.4 may expose os without getenv. Older modules previously used
-- os.getenv('LOCALAPPDATA'), so provide a deterministic compatibility shim.
if not os.getenv then
    os.getenv = function(name)
        if name == 'LOCALAPPDATA' then
            local home = LrPathUtils.getStandardFilePath('home')
            if home and home ~= '' then
                return LrPathUtils.child(
                    LrPathUtils.child(
                        LrPathUtils.child(home, 'AppData'),
                        'Local'
                    ),
                    ''
                )
            end
        end
        return nil
    end
end

local okDebug, Debug = pcall(require, 'DebugLog')
if not okDebug then
    -- Lightroom itself will surface this loader error in Plugin Manager.
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
Debug.info('data_dir', tostring(Debug.dataDir()))
Debug.info('active_catalog', catalogPath())
Debug.writeState('bootstrap.txt', 'OK\nversion=' .. _G.LRAutomaticVersion .. '\nplugin_path=' .. tostring(_PLUGIN and _PLUGIN.path or '') .. '\ndata_dir=' .. tostring(Debug.dataDir()) .. '\ncatalog=' .. catalogPath())

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