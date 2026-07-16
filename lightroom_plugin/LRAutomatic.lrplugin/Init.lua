local LrTasks = import 'LrTasks'

_G.LRAutomaticShutdown = false
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticVersion = '0.2.6-lr104-hardened'
_G.LRAutomaticLastError = nil

-- Keep top-level code minimal so menu registration is never blocked.
LrTasks.startAsyncTask(function()
    local okDebug, Debug = pcall(require, 'DebugLog')

    local function logInfo(event, detail)
        if okDebug and Debug then pcall(Debug.info, event, detail) end
    end

    local function logError(event, detail)
        if okDebug and Debug then
            pcall(Debug.error, event, detail)
            pcall(Debug.writeState, 'fatal_error.txt', tostring(detail or ''))
        end
    end

    logInfo('automatic_bootstrap_start', _G.LRAutomaticVersion)

    local okRequire, Runner = pcall(require, 'JobRunner')
    if not okRequire then
        _G.LRAutomaticLastError = tostring(Runner)
        logError('automatic_jobrunner_require_failed', Runner)
        return
    end

    _G.LRAutomaticLoopRunning = true
    _G.LRAutomaticLastError = nil
    logInfo('automatic_loop_start', tostring(Runner.getJobsDir()))

    local okLoop, loopError = pcall(function()
        Runner.runLoop(function()
            return _G.LRAutomaticShutdown == true
        end)
    end)

    _G.LRAutomaticLoopRunning = false
    if not okLoop then
        _G.LRAutomaticLastError = tostring(loopError)
        logError('automatic_loop_failed', loopError)
    else
        logInfo('automatic_loop_stopped', 'normal')
    end
end)
