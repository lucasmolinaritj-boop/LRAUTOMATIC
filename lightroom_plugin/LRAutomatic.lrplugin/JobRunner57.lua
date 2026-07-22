-- Loop simples e contínuo para Lightroom Classic 10.4.
-- Remove o worker filho que podia deixar workerRunning preso após a primeira tarefa.
local LrPathUtils = import 'LrPathUtils'

local originalOpen = io.open
local targetPath = LrPathUtils.child(_PLUGIN.path, 'JobRunner51.lua')

local injection = [=[
-- Após cada chamada SDK, confirma que a execução ainda possui o job antes de
-- contabilizar resultado. Mantém a proteção da versão anterior sem supervisor filho.
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
[[function Runner.runLoop(shouldStop)
    LrFileUtils.createAllDirectories(jobsDir())
    LrFileUtils.createAllDirectories(claimsDir())
    plainLog('Plugin V4.9.10 iniciado; loop direto; consumo contínuo da fila')
    updateClaim(nil)

    while not shouldStop() do
        if processing and activeJobPath and activeJob and diskCancelled(activeJobPath) then
            finishCancelled(activeJobPath,activeJob,'Cancelamento detectado pelo loop.')
        end

        updateClaim(activeJob and activeJob.job_id or nil)
        writeState(
            'heartbeat.txt',
            timestamp()..'\ninstance='..INSTANCE_ID..
            '\nprocessing='..tostring(processing)..
            '\njobs='..jobsDir()
        )

        -- Executa diretamente no mesmo loop. processQueuedOnce já garante um único
        -- job por vez e sempre volta a varrer a fila na próxima iteração.
        Runner.processQueuedOnce()
        LrTasks.sleep(1)
    end

    cancelActivePreview()
    if LrFileUtils.exists(claimPath(INSTANCE_ID)) then
        LrFileUtils.delete(claimPath(INSTANCE_ID))
    end
    plainLog('Plugin V4.9.10 loop direto encerrado')
end]],
'loop direto sem worker órfão')

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