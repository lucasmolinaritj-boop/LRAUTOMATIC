local LrTasks = import 'LrTasks'

-- Kill every loop left alive by a previous plug-in reload before starting a new one.
_G.LRAutomaticShutdown = true
_G.LRAutomaticGeneration = (_G.LRAutomaticGeneration or 0) + 1
local myGeneration = _G.LRAutomaticGeneration
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticVersion = '4.3-safe-missing-file-recovery-lr104'
_G.LRAutomaticLastError = nil

LrTasks.startAsyncTask(function()
    -- Give older loops enough time to observe Shutdown=true and exit.
    LrTasks.sleep(3)
    if myGeneration ~= _G.LRAutomaticGeneration then return end

    local okRequire, Runner = pcall(require, 'SafeRunner')
    if not okRequire then
        _G.LRAutomaticLastError = tostring(Runner)
        return
    end

    _G.LRAutomaticShutdown = false
    _G.LRAutomaticLoopRunning = true
    _G.LRAutomaticLastError = nil

    local okRun, runError = pcall(function()
        Runner.runLoop(function()
            return _G.LRAutomaticShutdown == true or myGeneration ~= _G.LRAutomaticGeneration
        end)
    end)

    if not okRun then
        _G.LRAutomaticLastError = tostring(runError)
    end

    if myGeneration == _G.LRAutomaticGeneration then
        _G.LRAutomaticLoopRunning = false
    end
end)
