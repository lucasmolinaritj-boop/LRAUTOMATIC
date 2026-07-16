local LrDialogs = import 'LrDialogs'
local LrTasks = import 'LrTasks'

LrTasks.startAsyncTask(function()
    local okDebug, Debug = pcall(require, 'DebugLog')

    local function logInfo(event, detail)
        if okDebug and Debug then pcall(Debug.info, event, detail) end
    end

    local function logError(event, detail)
        if okDebug and Debug then
            pcall(Debug.error, event, detail)
            pcall(Debug.writeState, 'manual_process_error.txt', tostring(detail or ''))
        end
    end

    logInfo('manual_process_clicked', 'processamento imediato solicitado')

    local okRequire, Runner = pcall(require, 'JobRunner')
    if not okRequire then
        _G.LRAutomaticLastError = tostring(Runner)
        logError('manual_jobrunner_require_failed', Runner)
        LrDialogs.message('LRAutomatic', 'Falha ao carregar JobRunner:\n' .. tostring(Runner), 'critical')
        return
    end

    -- Lightroom Classic 10.4 does not expose xpcall in every plug-in context.
    -- pcall is supported and keeps the command from crashing the plug-in host.
    local okProcess, result = pcall(Runner.processQueuedOnce)
    if not okProcess then
        _G.LRAutomaticLastError = tostring(result)
        logError('manual_process_failed', result)
        LrDialogs.message('LRAutomatic', 'Erro ao processar a fila:\n' .. tostring(result), 'critical')
        return
    end

    _G.LRAutomaticLastError = nil
    logInfo('manual_process_complete', 'processed=' .. tostring(result))

    if result == 0 then
        LrDialogs.message('LRAutomatic', 'Nenhuma tarefa queued foi encontrada em:\n' .. tostring(Runner.getJobsDir()), 'info')
    else
        LrDialogs.message('LRAutomatic', tostring(result) .. ' tarefa(s) processada(s).', 'info')
    end
end)
