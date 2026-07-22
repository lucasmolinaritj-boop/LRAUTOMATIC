-- Evita duas instâncias do loop da versão anterior.
_G.LRAutomaticShutdown = true
_G.LRAutomaticGeneration = (_G.LRAutomaticGeneration or 0) + 1
local myGeneration = _G.LRAutomaticGeneration
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticVersion = '4.9.5-catalog-cache-fast-standard-preview-lr104'
_G.LRAutomaticLastError = nil

LrTasks.startAsyncTask(function()
    LrTasks.sleep(3)
    if myGeneration ~= _G.LRAutomaticGeneration then return end

    local okRequire, Runner = pcall(require, 'JobRunner55')
    if not okRequire then
        _G.LRAutomaticLastError = tostring(Runner)
        return
    end

    _G.LRAutomaticShutdown = false
    _G.LRAutomaticLoopRunning = true
    local okRun, runError = pcall(function()
        Runner.start()
    end)
    if not okRun then
        _G.LRAutomaticLastError = tostring(runError)
    end
    if myGeneration == _G.LRAutomaticGeneration then
        _G.LRAutomaticLoopRunning = false
    end
end)
