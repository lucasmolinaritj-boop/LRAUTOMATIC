local LrTasks = import 'LrTasks'

-- Encerra loops deixados por recargas anteriores antes de iniciar uma nova geração.
_G.LRAutomaticShutdown = true
_G.LRAutomaticGeneration = (_G.LRAutomaticGeneration or 0) + 1
local myGeneration = _G.LRAutomaticGeneration
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticVersion = '4.9.3-preview-state-scope-fix-lr104'
_G.LRAutomaticLastError = nil

LrTasks.startAsyncTask(function()
    LrTasks.sleep(3)
    if myGeneration ~= _G.LRAutomaticGeneration then return end

    -- pcall somente no carregamento: chamadas SDK que cedem ao scheduler ficam fora dele.
    local okRequire, Runner = pcall(require, 'JobRunner53')
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