local LrLogger = import 'LrLogger'
local LrTasks = import 'LrTasks'

local logger = LrLogger('LRAutomatic')
logger:enable('logfile')

_G.LRAutomaticShutdown = false
_G.LRAutomaticLoopRunning = false

logger:info('LRAutomatic 0.2.0 carregado no Lightroom Classic 10.4')

LrTasks.startAsyncTask(function()
    local okRequire, Runner = pcall(require, 'JobRunner')
    if not okRequire then
        logger:error('Falha ao carregar JobRunner: ' .. tostring(Runner))
        return
    end

    _G.LRAutomaticLoopRunning = true
    logger:info('Loop automático iniciado')

    local okLoop, err = pcall(function()
        Runner.runLoop(function()
            return _G.LRAutomaticShutdown == true
        end)
    end)

    _G.LRAutomaticLoopRunning = false
    if not okLoop then
        logger:error('Loop automático encerrado por erro: ' .. tostring(err))
    else
        logger:info('Loop automático encerrado normalmente')
    end
end)
