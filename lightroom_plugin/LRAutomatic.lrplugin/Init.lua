local LrTasks = import 'LrTasks'

_G.LRAutomaticShutdown = false
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticVersion = '3.1-task-safe-lr104'
_G.LRAutomaticLastError = nil

-- The Lightroom Lua 5.1 runtime cannot yield across pcall/C boundaries.
-- Keep the long-running loop directly inside the asynchronous task.
LrTasks.startAsyncTask(function()
    local okRequire, Runner = pcall(require, 'JobRunner')
    if not okRequire then
        _G.LRAutomaticLastError = tostring(Runner)
        return
    end

    _G.LRAutomaticLoopRunning = true
    _G.LRAutomaticLastError = nil

    Runner.runLoop(function()
        return _G.LRAutomaticShutdown == true
    end)

    _G.LRAutomaticLoopRunning = false
end)
