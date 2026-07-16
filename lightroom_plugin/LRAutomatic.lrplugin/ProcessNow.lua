local LrDialogs = import 'LrDialogs'
local LrTasks = import 'LrTasks'

LrTasks.startAsyncTask(function()
    local okRequire, Runner = pcall(require, 'JobRunner')
    if not okRequire then
        _G.LRAutomaticLastError = tostring(Runner)
        LrDialogs.message('LRAutomatic', 'Falha ao carregar JobRunner:\n' .. tostring(Runner), 'critical')
        return
    end

    -- Do not wrap this in pcall: withWriteAccessDo may yield while waiting for
    -- catalog access, and Lua 5.1 cannot yield across pcall/C boundaries.
    local result = Runner.processQueuedOnce()
    _G.LRAutomaticLastError = nil

    if result == 0 then
        LrDialogs.message('LRAutomatic', 'Nenhuma tarefa queued foi encontrada em:\n' .. tostring(Runner.getJobsDir()), 'info')
    else
        LrDialogs.message('LRAutomatic', tostring(result) .. ' tarefa(s) processada(s).', 'info')
    end
end)
