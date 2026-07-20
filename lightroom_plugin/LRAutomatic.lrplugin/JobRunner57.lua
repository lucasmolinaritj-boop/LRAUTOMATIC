-- Supervisor resiliente para o runner do Lightroom Classic 10.4.
-- O trabalho que pode ceder ao scheduler roda em uma tarefa filha. Assim, uma
-- exceção fatal da SDK mata somente aquela tarefa, nunca o supervisor principal.
local LrPathUtils = import 'LrPathUtils'

local originalOpen = io.open
local targetPath = LrPathUtils.child(_PLUGIN.path, 'JobRunner51.lua')

local injection = [=[
-- RUNNER FAILSAFE: tarefa filha isolada + watchdog de heartbeat.
source = replaceOnce(source,
[[local leaderSince = nil]],
[[local leaderSince = nil
local workerRunning = false
local workerStartedEpoch = 0
local workerJobId = nil
local WORKER_STALE_SECONDS = 240]],
'variáveis do supervisor resiliente')

-- Se uma chamada SDK bloqueada voltar depois de o watchdog já ter encerrado o
-- job, a tarefa antiga perde a posse antes de contabilizar ou gravar resultado.
source = replaceOnce(source,
[[        local photo,result,err=importOneAttempt(catalog,path)
        if result=='imported' or result=='skipped' then return photo,result,nil end]],
[[        local photo,result,err=importOneAttempt(catalog,path)
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then
            return nil,'cancelled','execução antiga perdeu a posse do job'
        end
        if result=='imported' or result=='skipped' then return photo,result,nil end]],
'validar posse após retorno da SDK')

source = replaceOnce(source,
[[function Runner.runLoop(shouldStop)
    LrFileUtils.createAllDirectories(jobsDir()); LrFileUtils.createAllDirectories(claimsDir())
    plainLog('Plugin V4.8 iniciado; líder global por lease; um job; previews seriais; recuperação automática')
    updateClaim(nil)
    while not shouldStop() do
        if processing and activeJobPath and activeJob and diskCancelled(activeJobPath) then finishCancelled(activeJobPath,activeJob,'Cancelamento detectado pelo loop.') end
        updateClaim(activeJob and activeJob.job_id or nil)
        writeState('heartbeat.txt',timestamp()..'\ninstance='..INSTANCE_ID..'\nprocessing='..tostring(processing)..'\njobs='..jobsDir())
        Runner.processQueuedOnce()
        LrTasks.sleep(1)
    end
    cancelActivePreview()
    if LrFileUtils.exists(claimPath(INSTANCE_ID)) then LrFileUtils.delete(claimPath(INSTANCE_ID)) end
    plainLog('Plugin V4.8 loop encerrado')
end]],
[[local function failActiveWorker(reason)
    local jobPath = activeJobPath
    local job = activeJob
    local detail = tostring(reason or 'falha fatal desconhecida no worker')
    _G.LRAutomaticLastError = detail
    plainLog('WORKER_FATAL_RECOVERY job='..tostring(job and job.job_id)..' error='..detail)

    cancelActivePreview()
    if jobPath and job then
        refreshTotals(job)
        job.error = detail
        job.current_stage = 'failed_guard'
        job.finished_at = timestamp()
        job.status = ((job.total_imported or 0) > 0) and 'partial' or 'failed'
        appendJobEvent(
            job,
            'fatal_guard',
            'Falha isolada automaticamente',
            'Uma exceção ou travamento da SDK foi contido. O motor continuou ativo. Detalhe: '..detail,
            'error'
        )
        writeJsonAtomic(jobPath, job)
    end
    clearActive()
    workerRunning = false
    workerStartedEpoch = 0
    workerJobId = nil
end

local function workerHeartbeatAge()
    if not activeJob then return nil end
    local heartbeat = tonumber(activeJob.runner_heartbeat_epoch or 0)
    if heartbeat <= 0 then return os.time() - workerStartedEpoch end
    return os.time() - heartbeat
end

function Runner.runLoop(shouldStop)
    LrFileUtils.createAllDirectories(jobsDir()); LrFileUtils.createAllDirectories(claimsDir())
    plainLog('Plugin V4.9.7 iniciado; worker isolado; watchdog fatal; loop supervisor permanente')
    updateClaim(nil)

    while not shouldStop() do
        if processing and activeJobPath and activeJob and diskCancelled(activeJobPath) then
            finishCancelled(activeJobPath,activeJob,'Cancelamento detectado pelo supervisor.')
            workerRunning = false
            workerStartedEpoch = 0
            workerJobId = nil
        end

        if workerRunning then
            local age = workerHeartbeatAge()
            if activeJob and age and age > WORKER_STALE_SECONDS then
                failActiveWorker('worker sem heartbeat por '..tostring(age)..' segundos; possível popup, bloqueio ou exceção fatal da SDK')
            elseif not activeJob and workerStartedEpoch > 0 and (os.time() - workerStartedEpoch) > 30 then
                failActiveWorker('worker terminou de forma anormal antes de assumir um job')
            end
        else
            workerRunning = true
            workerStartedEpoch = os.time()
            workerJobId = activeJob and activeJob.job_id or nil
            LrTasks.startAsyncTask(function()
                -- Não usar pcall/xpcall aqui: várias APIs do Lightroom cedem ao
                -- scheduler e não podem executar dentro de uma chamada C protegida.
                Runner.processQueuedOnce()
                workerRunning = false
                workerStartedEpoch = 0
                workerJobId = nil
            end)
        end

        updateClaim(activeJob and activeJob.job_id or nil)
        writeState(
            'heartbeat.txt',
            timestamp()..'\ninstance='..INSTANCE_ID..
            '\nprocessing='..tostring(processing)..
            '\nworker_running='..tostring(workerRunning)..
            '\nworker_job='..tostring(workerJobId)..
            '\njobs='..jobsDir()
        )
        LrTasks.sleep(1)
    end

    cancelActivePreview()
    if activeJobPath and activeJob and processing then
        failActiveWorker('plugin encerrado durante uma tarefa ativa')
    end
    if LrFileUtils.exists(claimPath(INSTANCE_ID)) then LrFileUtils.delete(claimPath(INSTANCE_ID)) end
    plainLog('Plugin V4.9.7 loop supervisor encerrado')
end]],
'supervisor permanente com watchdog')

]=]

io.open = function(path, mode)
    if path == targetPath and (mode == 'rb' or mode == 'r') then
        local realFile, openError = originalOpen(path, mode)
        if not realFile then return nil, openError end
        local content = realFile:read('*a') or ''
        realFile:close()
        content = content:gsub('\r\n','\n'):gsub('\r','\n')
        local marker = "_G.import = function(moduleName)"
        local first = string.find(content, marker, 1, true)
        if not first then error('JobRunner57: marcador de injeção não encontrado') end
        content = string.sub(content,1,first-1) .. injection .. '\n' .. string.sub(content,first)
        local consumed=false
        return {
            read=function()
                if consumed then return nil end
                consumed=true
                return content
            end,
            close=function() return true end,
        }
    end
    return originalOpen(path,mode)
end

local ok,runnerOrError=pcall(require,'JobRunner56')
io.open=originalOpen
if not ok then error(runnerOrError) end
return runnerOrError