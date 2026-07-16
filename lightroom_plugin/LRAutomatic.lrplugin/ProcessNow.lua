local LrDialogs = import 'LrDialogs'
local LrTasks = import 'LrTasks'

LrTasks.startAsyncTask(function()
    local okRequire, Runner = pcall(require, 'JobRunner')
    if not okRequire then
        LrDialogs.message('LRAutomatic', 'Falha ao carregar JobRunner:\n' .. tostring(Runner), 'critical')
        return
    end

    local ok, result = pcall(Runner.processQueuedOnce)
    if not ok then
        LrDialogs.message('LRAutomatic', 'Erro ao processar a fila:\n' .. tostring(result), 'critical')
        return
    end

    if result == 0 then
        LrDialogs.message('LRAutomatic', 'Nenhuma tarefa queued foi encontrada em:\n' .. Runner.getJobsDir(), 'info')
    else
        LrDialogs.message('LRAutomatic', tostring(result) .. ' tarefa(s) processada(s).', 'info')
    end
end)
