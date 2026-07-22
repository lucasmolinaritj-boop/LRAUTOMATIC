local LrTasks = import 'LrTasks'

-- Encerra loops deixados por recargas anteriores antes de iniciar uma nova geração.
_G.LRAutomaticShutdown = true
_G.LRAutomaticGeneration = (_G.LRAutomaticGeneration or 0) + 1
local myGeneration = _G.LRAutomaticGeneration
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticVersion = '4.10.0-unified-entrypoint-lr104'
_G.LRAutomaticLastError = nil

LrTasks.startAsyncTask(function()
    LrTasks.sleep(3)
    if myGeneration ~= _G.LRAutomaticGeneration then return end

    local okRequire, Runner = pcall(require, 'JobRunner')
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
