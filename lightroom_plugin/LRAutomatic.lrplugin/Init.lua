local LrTasks = import 'LrTasks'

-- Encerra loops deixados por recargas anteriores antes de iniciar uma nova geração.
_G.LRAutomaticShutdown = true
_G.LRAutomaticGeneration = (_G.LRAutomaticGeneration or 0) + 1
local myGeneration = _G.LRAutomaticGeneration
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticVersion = '4.9.9-catalog-cache-fast-standard-preview-pause-watchdog-lr104'
_G.LRAutomaticLastError = nil

LrTasks.startAsyncTask(function()
    LrTasks.sleep(3)
    if myGeneration ~= _G.LRAutomaticGeneration then return end

    -- A cadeia ativa é 58 -> 57 -> 56 -> 55, preservando pausa, watchdog,
    -- reparação de Smart Preview e as otimizações de catálogo/previews.
    local okRequire, Runner = pcall(require, 'JobRunner58')
    if not okRequire then
        _G.LRAutomaticLastError = tostring(Runner)
        return
    end

    _G.LRAutomaticShutdown = false
    _G.LRAutomaticLoopRunning = true
    _G.LRAutomaticLastError = nil

    Runner.runLoop(function()
        return _G.LRAutomaticShutdown == true or myGeneration ~= _G.LRAutomaticGeneration
    end)

    if myGeneration == _G.LRAutomaticGeneration then
        _G.LRAutomaticLoopRunning = false
    end
end)
