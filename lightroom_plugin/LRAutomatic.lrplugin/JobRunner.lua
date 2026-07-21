local LrTasks = import 'LrTasks'

local BaseRunner = require 'JobRunner46'
local CollectionOrganizer = require 'CollectionOrganizer'
local Runner = {}

function Runner.processQueuedOnce()
    local processed = BaseRunner.processQueuedOnce()
    CollectionOrganizer.processOnce()
    return processed
end

function Runner.runLoop(shouldStop)
    while not shouldStop() do
        BaseRunner.processQueuedOnce()
        CollectionOrganizer.processOnce()
        LrTasks.sleep(1)
    end
end

function Runner.getJobsDir()
    return BaseRunner.getJobsDir()
end

return Runner
