local LrDialogs = import 'LrDialogs'
local LrTasks = import 'LrTasks'
local Debug = require 'DebugLog'

Debug.info('manual_process_clicked', 'usuário solicitou processamento imediato')

LrTasks.startAsyncTask(function()
    local okRequire, Runner = pcall(require, 'JobRunner')
    if not okRequire then
        _G.LRAutomaticLastError = tostring(Runner)
        Debug.error('manual_jobrunner_require_failed', tostring(Runner))
        LrDialogs.message('LRAutomatic V2', 'Falha ao carregar JobRunner:\n' .. tostring(Runner), 'critical')
        return
    end

    local ok, result = xpcall(Runner.processQueuedOnce, function(message)
        return debug.traceback(tostring(message), 2)
    end)
    if not ok then
        _G.LRAutomaticLastError = tostring(result)
        Debug.error('manual_process_failed', tostring(result))
        Debug.writeState('manual_process_error.txt', tostring(result))
        LrDialogs.message('LRAutomatic V2', 'Erro ao processar a fila:\n' .. tostring(result), 'critical')
        return
    end

    Debug.info('manual_process_complete', 'processed=' .. tostring(result))
    if result == 0 then
        LrDialogs.message('LRAutomatic V2', 'Nenhuma tarefa queued foi encontrada em:\n' .. Runner.getJobsDir(), 'info')
    else
        LrDialogs.message('LRAutomatic V2', tostring(result) .. ' tarefa(s) processada(s).', 'info')
    end
end)
