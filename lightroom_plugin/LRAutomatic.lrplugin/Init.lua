local LrTasks = import 'LrTasks'
local Runner = require 'JobRunner'

_G.LRAutomaticShutdown = false

LrTasks.startAsyncTask(function()
    Runner.runLoop(function()
        return _G.LRAutomaticShutdown == true
    end)
end)
