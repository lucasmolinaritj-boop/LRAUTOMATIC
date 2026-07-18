local LrApplication = import 'LrApplication'
local LrFileUtils = import 'LrFileUtils'
local LrLogger = import 'LrLogger'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'
local Json = require 'Json'

local Runner = {}
local logger = LrLogger('LRAutomatic')
logger:enable('logfile')

local MAX_ATTEMPTS = 10
local RETRY_DELAY_SECONDS = 60
local CLAIM_STALE_SECONDS = 20
local JOB_STALE_SECONDS = 45
local PREVIEW_ATTEMPT_TIMEOUT_SECONDS = 180
local DEFAULT_EXTENSIONS = { cr2=true, cr3=true, dng=true }

math.randomseed(os.time() + math.floor((LrTasks.currentTime() or 0) * 1000))
local INSTANCE_ID = string.format('%010d-%08x', os.time(), math.random(0, 0x7fffffff))
local processing = false
local activeJobPath = nil
local activeJob = nil
local activePreviewHandle = nil
local leaderSince = nil

local function homePath()
    local home = LrPathUtils.getStandardFilePath('home')
    if home and home ~= '' then return home end
    return 'C:\\Users\\Public'
end

local function dataDir()
    return LrPathUtils.child(LrPathUtils.child(LrPathUtils.child(homePath(), 'AppData'), 'Local'), 'LRAutomatic')
end

local function jobsDir() return LrPathUtils.child(dataDir(), 'jobs') end
local function logsDir() return LrPathUtils.child(dataDir(), 'logs') end
local function stateDir() return LrPathUtils.child(dataDir(), 'plugin_state') end
local function claimsDir() return LrPathUtils.child(stateDir(), 'runner_claims') end
local function claimPath(id) return LrPathUtils.child(claimsDir(), 'claim_' .. id .. '.json') end

local function timestamp()
    return os.date('!%Y-%m-%dT%H:%M:%SZ')
end

local function appendText(path, content)
    local file = io.open(path, 'ab')
    if not file then return false end
    file:write(content or '')
    file:close()
    return true
end

local function plainLog(message)
    LrFileUtils.createAllDirectories(dataDir())
    LrFileUtils.createAllDirectories(logsDir())
    local line = timestamp() .. ' instance=' .. INSTANCE_ID .. ' ' .. tostring(message) .. '\n'
    appendText(LrPathUtils.child(dataDir(), 'runner-trace.log'), line)
    appendText(LrPathUtils.child(logsDir(), 'plugin.log'), line)
    pcall(function() logger:info(tostring(message)) end)
end

local function stripBom(content)
    if content and string.byte(content,1)==239 and string.byte(content,2)==187 and string.byte(content,3)==191 then
        return string.sub(content,4)
    end
    return content
end

local function readJson(path)
    local file = io.open(path, 'rb')
    if not file then return nil, 'arquivo não pôde ser lido' end
    local content = file:read('*a')
    file:close()
    local ok, value = pcall(Json.decode, stripBom(content or ''))
    if not ok or type(value) ~= 'table' then return nil, tostring(value) end
    return value, nil
end

local function encodeJson(value)
    local ok, encoded = pcall(Json.encode, value)
    if not ok then return nil end
    return encoded
end

local function writeJsonAtomic(path, value)
    local encoded = encodeJson(value)
    if not encoded then return false end
    local temp = path .. '.tmp.' .. INSTANCE_ID .. '.' .. tostring(math.random(100000,999999))
    local file = io.open(temp, 'wb')
    if not file then return false end
    file:write(encoded)
    file:close()
    for attempt=1,5 do
        if LrFileUtils.exists(path) then LrFileUtils.delete(path) end
        if LrFileUtils.move(temp, path) == true then return true end
        LrTasks.sleep(0.1 * attempt)
    end
    if LrFileUtils.exists(temp) then LrFileUtils.delete(temp) end
    return false
end

local function writeState(name, text)
    LrFileUtils.createAllDirectories(stateDir())
    local file = io.open(LrPathUtils.child(stateDir(), name), 'wb')
    if file then file:write(tostring(text or '')); file:close() end
end

local function appendJobEvent(job, stage, title, detail, level)
    job.events = job.events or {}
    table.insert(job.events, {at=timestamp(), stage=stage, title=title, detail=tostring(detail or ''), level=level or 'info'})
end

local function diskCancelled(path)
    local disk = readJson(path)
    return disk and tostring(disk.status) == 'cancelled'
end

local function safeWriteJob(path, job)
    if diskCancelled(path) then
        job.status = 'cancelled'
        return false
    end
    job.runner_instance_id = INSTANCE_ID
    job.runner_heartbeat_epoch = os.time()
    job.runner_heartbeat_at = timestamp()
    if not writeJsonAtomic(path, job) then
        plainLog('JOB_WRITE_FAILED path=' .. tostring(path))
        return false
    end
    return true
end

local function updateClaim(currentJobId)
    LrFileUtils.createAllDirectories(claimsDir())
    local value = {
        instance_id=INSTANCE_ID,
        heartbeat_epoch=os.time(),
        heartbeat_at=timestamp(),
        current_job_id=currentJobId,
        processing=processing,
    }
    writeJsonAtomic(claimPath(INSTANCE_ID), value)
end

local function activeClaims()
    LrFileUtils.createAllDirectories(claimsDir())
    local now = os.time()
    local result = {}
    for path in LrFileUtils.files(claimsDir()) do
        local name = string.lower(LrPathUtils.leafName(path) or '')
        if string.sub(name,1,6)=='claim_' and string.sub(name,-5)=='.json' then
            local claim = readJson(path)
            local age = claim and (now - (tonumber(claim.heartbeat_epoch) or 0)) or CLAIM_STALE_SECONDS + 1
            if claim and age <= CLAIM_STALE_SECONDS then
                table.insert(result, claim)
            elseif age > CLAIM_STALE_SECONDS and LrFileUtils.exists(path) then
                LrFileUtils.delete(path)
            end
        end
    end
    table.sort(result, function(a,b) return tostring(a.instance_id) < tostring(b.instance_id) end)
    return result
end

local function isLeader()
    updateClaim(activeJob and activeJob.job_id or nil)
    local claims = activeClaims()
    local leader = claims[1] and tostring(claims[1].instance_id) or INSTANCE_ID
    local yes = leader == INSTANCE_ID
    if yes then
        leaderSince = leaderSince or os.time()
    else
        leaderSince = nil
    end
    writeState('runner_owner.txt', timestamp() .. '\ninstance=' .. INSTANCE_ID .. '\nleader=' .. leader .. '\nactive=' .. tostring(yes))
    return yes
end

local function stableLeader()
    return isLeader() and leaderSince and (os.time() - leaderSince >= 2)
end

local function cancelActivePreview()
    local handle = activePreviewHandle
    activePreviewHandle = nil
    if handle and handle.cancel then pcall(function() handle:cancel() end) end
end

local function clearActive()
    cancelActivePreview()
    processing=false
    activeJobPath=nil
    activeJob=nil
    updateClaim(nil)
end

local function finishCancelled(jobPath, job, detail)
    cancelActivePreview()
    local disk = readJson(jobPath)
    if disk and tostring(disk.status)=='cancelled' then job=disk end
    job.status='cancelled'
    job.finished_at=job.finished_at or timestamp()
    job.current_source=nil
    job.current_photo=nil
    job.current_stage='cancelled'
    appendJobEvent(job,'cancelled','Tarefa cancelada pelo usuário',detail or 'Processamento interrompido.','warning')
    writeJsonAtomic(jobPath,job)
    plainLog('JOB_CANCELLED id=' .. tostring(job.job_id))
    clearActive()
end

local function isCancelled(jobPath, job)
    if tostring(job.status)=='cancelled' or diskCancelled(jobPath) then
        finishCancelled(jobPath,job,'A operação atual e as tentativas pendentes foram interrompidas.')
        return true
    end
    return false
end

local function stillOwns(jobPath, job)
    if not processing or activeJobPath ~= jobPath or activeJob ~= job then return false end
    if tostring(job.runner_instance_id or INSTANCE_ID) ~= INSTANCE_ID then return false end
    return isLeader()
end

local function sleepInterruptible(jobPath, job, seconds)
    for _=1,seconds do
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return false end
        updateClaim(job.job_id)
        safeWriteJob(jobPath,job)
        LrTasks.sleep(1)
    end
    return true
end

local function normalizedExtension(path)
    local ext=string.lower(LrPathUtils.extension(path) or '')
    if string.sub(ext,1,1)=='.' then ext=string.sub(ext,2) end
    return ext
end

local function allowedExtensionTable(request)
    local configured=request and request.allowed_extensions
    if type(configured)~='table' or #configured==0 then return DEFAULT_EXTENSIONS end
    local result={}
    for _,value in ipairs(configured) do
        local ext=string.lower(tostring(value or ''))
        if string.sub(ext,1,1)=='.' then ext=string.sub(ext,2) end
        if ext~='' then result[ext]=true end
    end
    return next(result) and result or DEFAULT_EXTENSIONS
end

local function isJobFile(path)
    local name=string.lower(LrPathUtils.leafName(path) or tostring(path))
    return string.sub(name,1,4)=='job_' and string.sub(name,-5)=='.json'
end

local function collectFiles(folder,recursive,allowed)
    if not folder or folder=='' then return {},'pasta de origem vazia' end
    if not LrFileUtils.exists(folder) then return {},'pasta de origem não existe: '..tostring(folder) end
    local result={}
    local iterator=recursive and LrFileUtils.recursiveFiles(folder) or LrFileUtils.files(folder)
    for path in iterator do
        if LrFileUtils.exists(path) and allowed[normalizedExtension(path)] then table.insert(result,path) end
    end
    table.sort(result)
    return result,nil
end

local function refreshTotals(job)
    job.total_discovered,job.total_imported,job.total_skipped,job.total_failed=0,0,0,0
    for _,p in ipairs(job.progress or {}) do
        job.total_discovered=job.total_discovered+(p.discovered or 0)
        job.total_imported=job.total_imported+(p.imported or 0)
        job.total_skipped=job.total_skipped+(p.skipped or 0)
        job.total_failed=job.total_failed+(p.failed or 0)
    end
end

local function withWrite(catalog,actionName,fn,detail)
    local ran,timedOut=false,false
    plainLog('WRITE_BEGIN action='..actionName..' detail='..tostring(detail))
    local status=catalog:withWriteAccessDo(actionName,function(context) ran=true; fn(context) end,{timeout=15,callback=function() timedOut=true end})
    plainLog('WRITE_END action='..actionName..' status='..tostring(status)..' ran='..tostring(ran)..' timeout='..tostring(timedOut))
    return ran and not timedOut and (status==nil or status=='executed'),tostring(status or 'executed')
end

local function findCollection(catalog,name)
    for _,collection in ipairs(catalog:getChildCollections()) do if collection:getName()==name then return collection end end
    return nil
end

local function ensureCollection(catalog,name)
    if not name or name=='' then return nil,nil end
    local existing=findCollection(catalog,name)
    if existing then return existing,nil end
    local ok,reason=withWrite(catalog,'LRAutomatic: criar coleção',function() catalog:createCollection(name,nil,true) end,name)
    if not ok then return nil,reason end
    return findCollection(catalog,name),nil
end

local function importOneAttempt(catalog,path)
    if not path or path=='' then return nil,'failed','caminho vazio' end
    if not LrFileUtils.exists(path) then return nil,'failed','arquivo não encontrado' end
    local before=catalog:findPhotoByPath(path)
    if before then return before,'skipped',nil end
    local imported=nil
    local ok,reason=withWrite(catalog,'LRAutomatic: importar foto',function() imported=catalog:addPhoto(path) end,path)
    if not ok then return nil,'failed',reason end
    local after=imported or catalog:findPhotoByPath(path)
    if after then return after,'imported',nil end
    return nil,'failed','foto não apareceu no catálogo após addPhoto'
end

local function importOneWithRetry(catalog,path,job,jobPath)
    local lastError=nil
    for attempt=1,MAX_ATTEMPTS do
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return nil,'cancelled','cancelado' end
        job.current_stage='importing'; job.current_photo=path; job.current_photo_attempt=attempt
        job.import_attempts_total=(job.import_attempts_total or 0)+1
        safeWriteJob(jobPath,job)
        local photo,result,err=importOneAttempt(catalog,path)
        if result=='imported' or result=='skipped' then return photo,result,nil end
        lastError=err
        if attempt<MAX_ATTEMPTS then
            appendJobEvent(job,'import_retry','Nova tentativa de importação agendada',path..' — tentativa '..(attempt+1)..' de 10 em 1 minuto.','warning')
            if not sleepInterruptible(jobPath,job,RETRY_DELAY_SECONDS) then return nil,'cancelled','cancelado' end
        end
    end
    return nil,'failed',lastError or 'falha desconhecida após 10 tentativas'
end

local function findPresetByNameOrUuid(name,uuid)
    local function searchFolder(folder)
        for _,preset in ipairs(folder:getDevelopPresets()) do
            if (uuid and preset:getUuid()==uuid) or (name and preset:getName()==name) then return preset end
        end
        if folder.getChildren then for _,child in ipairs(folder:getChildren()) do local found=searchFolder(child); if found then return found end end end
    end
    for _,folder in ipairs(LrApplication.developPresetFolders()) do local found=searchFolder(folder); if found then return found end end
end

local function applyPreset(catalog,photos,job,jobPath)
    if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return false end
    local request=job.request or {}; local name,uuid=request.develop_preset_name,request.develop_preset_uuid
    if not name and not uuid then job.preset_status='not_requested'; return true end
    if #photos==0 then job.preset_status='completed_no_photos'; return true end
    local preset=findPresetByNameOrUuid(name,uuid)
    if not preset then job.preset_status='failed'; job.error='Preset não encontrado: '..tostring(name or uuid); return false end
    job.current_stage='preset'; safeWriteJob(jobPath,job)
    local applied=0
    local ok,reason=withWrite(catalog,'LRAutomatic: aplicar preset',function() for _,photo in ipairs(photos) do photo:applyDevelopPreset(preset); applied=applied+1 end end,preset:getName())
    if not ok then job.preset_status='failed'; job.error='Falha ao aplicar preset: '..tostring(reason); return false end
    job.preset_status='completed'; job.preset_name_applied=preset:getName(); job.preset_applied_count=applied
    return true
end

local function buildSmartPreviewsWithRetry(catalog,photos,job,jobPath)
    if not ((job.request or {}).build_smart_previews==true) then job.smart_previews_status='not_requested'; return true end
    if #photos==0 then job.smart_previews_status='completed_no_photos'; return true end
    local pending=photos; local createdTotal,existedTotal=0,0
    job.current_stage='smart_preview'; job.smart_previews_status='running'
    for attempt=1,MAX_ATTEMPTS do
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return false end
        job.smart_previews_attempts=attempt; job.smart_previews_pending=#pending; safeWriteJob(jobPath,job)
        local result=catalog:buildSmartPreviews(pending)
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return false end
        local created=result and result.created or {}; local existed=result and result.existed or {}; local failed=result and result.failed or pending
        createdTotal=createdTotal+#created; existedTotal=existedTotal+#existed; pending=failed
        job.smart_previews_created=createdTotal; job.smart_previews_existed=existedTotal; job.smart_previews_failed=#pending; job.smart_previews_pending=#pending
        safeWriteJob(jobPath,job)
        if #pending==0 then job.smart_previews_status='completed'; return true end
        if attempt<MAX_ATTEMPTS and not sleepInterruptible(jobPath,job,RETRY_DELAY_SECONDS) then return false end
    end
    job.smart_previews_status='failed_after_retries'; job.smart_previews_failed=#pending
    return false
end

local function standardPreviewsSerial(photos,jobPath,job)
    local request=job.request or {}
    if request.build_standard_previews~=true then job.standard_previews_status='not_requested'; return true end
    if #photos==0 then job.standard_previews_status='completed_no_photos'; return true end
    local size=math.max(256,math.min(16384,tonumber(request.standard_preview_size) or 2048))
    job.current_stage='standard_preview'; job.standard_previews_status='running'; job.standard_previews_created=0; job.standard_previews_failed=0; job.standard_previews_attempts_total=0
    for index,photo in ipairs(photos) do
        local success=false; local lastError=nil
        for attempt=1,MAX_ATTEMPTS do
            if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return false end
            job.current_photo='preview_'..tostring(index); job.current_photo_attempt=attempt; job.standard_previews_pending=#photos-index+1
            job.standard_previews_attempts_total=job.standard_previews_attempts_total+1; safeWriteJob(jobPath,job)
            local done=false; local gotData=false; local callbackError=nil
            local expectedJobId=tostring(job.job_id); local expectedInstance=INSTANCE_ID
            activePreviewHandle=photo:requestJpegThumbnail(size,size,function(data,errorMessage)
                if not processing or not activeJob or tostring(activeJob.job_id)~=expectedJobId or expectedInstance~=INSTANCE_ID then return end
                gotData=data~=nil; callbackError=errorMessage; done=true
            end)
            local waited=0
            while not done and waited<PREVIEW_ATTEMPT_TIMEOUT_SECONDS do
                if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then cancelActivePreview(); return false end
                updateClaim(job.job_id); LrTasks.sleep(1); waited=waited+1
            end
            cancelActivePreview()
            if done and gotData then success=true; break end
            lastError=callbackError or (done and 'preview sem dados' or 'timeout da tentativa')
            if attempt<MAX_ATTEMPTS and not sleepInterruptible(jobPath,job,RETRY_DELAY_SECONDS) then return false end
        end
        if success then job.standard_previews_created=job.standard_previews_created+1 else job.standard_previews_failed=job.standard_previews_failed+1; plainLog('STANDARD_PREVIEW_GAVE_UP photo='..index..' error='..tostring(lastError)) end
        job.standard_previews_pending=#photos-index; safeWriteJob(jobPath,job)
    end
    job.standard_previews_status=job.standard_previews_failed>0 and 'failed_after_retries' or 'completed'
    return job.standard_previews_failed==0
end

local function finishJob(jobPath,job,failed)
    if isCancelled(jobPath,job) then return end
    refreshTotals(job); job.current_source=nil; job.current_photo=nil; job.current_photo_attempt=nil; job.current_stage='finished'; job.finished_at=timestamp()
    job.status=failed and ((job.total_imported or 0)>0 and 'partial' or 'failed') or 'completed'
    safeWriteJob(jobPath,job)
    plainLog('JOB_END id='..tostring(job.job_id)..' status='..tostring(job.status)..' imported='..tostring(job.total_imported))
    clearActive()
end

local function processSource(catalog,job,source,progress,jobPath,importedPhotos,allowed)
    source=source or {}; progress.status='running'; progress.imported=progress.imported or 0; progress.skipped=progress.skipped or 0; progress.failed=progress.failed or 0
    job.current_source=source.path
    local recursive=source.recursive; if recursive==nil then recursive=(job.request or {}).recursive==true end
    local files,collectError=collectFiles(source.path,recursive,allowed)
    if collectError then progress.discovered=0; progress.status='failed'; progress.error=collectError; safeWriteJob(jobPath,job); return true end
    progress.discovered=#files; safeWriteJob(jobPath,job)
    local photosForCollection={}
    for _,path in ipairs(files) do
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return true end
        local photo,result,err=importOneWithRetry(catalog,path,job,jobPath)
        if result=='cancelled' then return true elseif result=='imported' then progress.imported=progress.imported+1; table.insert(photosForCollection,photo); table.insert(importedPhotos,photo) elseif result=='skipped' then progress.skipped=progress.skipped+1; table.insert(photosForCollection,photo); table.insert(importedPhotos,photo) else progress.failed=progress.failed+1; progress.error=tostring(err)..': '..tostring(path) end
        refreshTotals(job); safeWriteJob(jobPath,job); LrTasks.yield()
    end
    local collectionName=source.collection; if not collectionName or collectionName=='' then collectionName=LrPathUtils.leafName(source.path or '') end
    if (job.request or {}).create_collections~=false and #photosForCollection>0 then
        local collection,collectionErr=ensureCollection(catalog,collectionName)
        if collection then local ok,reason=withWrite(catalog,'LRAutomatic: adicionar à coleção',function() collection:addPhotos(photosForCollection) end,collectionName); if not ok then progress.error='Coleção falhou: '..tostring(reason) end else progress.error='Coleção não criada: '..tostring(collectionErr) end
    end
    progress.status=(progress.failed>0) and 'partial' or 'completed'; refreshTotals(job); safeWriteJob(jobPath,job)
    return progress.status~='completed'
end

local function processJob(jobPath,job)
    if type(job)~='table' or tostring(job.status)~='queued' then clearActive(); return false end
    activeJobPath=jobPath; activeJob=job; processing=true
    job.request=type(job.request)=='table' and job.request or {}; job.progress=type(job.progress)=='table' and job.progress or {}
    local catalog=LrApplication.activeCatalog()
    if not catalog then job.status='failed'; job.error='nenhum catálogo ativo'; job.finished_at=timestamp(); safeWriteJob(jobPath,job); clearActive(); return false end
    job.active_catalog_path=catalog:getPath(); job.status='running'; job.error=nil; job.started_at=job.started_at or timestamp(); job.current_stage='starting'; safeWriteJob(jobPath,job)
    local importedPhotos={}; local failed=false; local sources=type(job.request.sources)=='table' and job.request.sources or {}; local allowed=allowedExtensionTable(job.request)
    for index,source in ipairs(sources) do
        local progress=job.progress[index]; if type(progress)~='table' then progress={status='queued',discovered=0,imported=0,skipped=0,failed=0}; job.progress[index]=progress end
        if processSource(catalog,job,source,progress,jobPath,importedPhotos,allowed) then failed=true end
        if tostring(job.status)=='cancelled' or not processing then return false end
    end
    local presetOk=applyPreset(catalog,importedPhotos,job,jobPath); safeWriteJob(jobPath,job)
    if tostring(job.status)=='cancelled' or not processing then return false end
    local smartOk=buildSmartPreviewsWithRetry(catalog,importedPhotos,job,jobPath); safeWriteJob(jobPath,job)
    if tostring(job.status)=='cancelled' or not processing then return false end
    local standardOk=standardPreviewsSerial(importedPhotos,jobPath,job)
    if tostring(job.status)=='cancelled' or not processing then return false end
    finishJob(jobPath,job,failed or not presetOk or not smartOk or not standardOk)
    return true
end

local function recoverOrBlockRunningJobs()
    local now=os.time(); local foundActive=false
    for path in LrFileUtils.files(jobsDir()) do
        if isJobFile(path) then
            local job=readJson(path)
            if job and tostring(job.status)=='running' then
                local age=now-(tonumber(job.runner_heartbeat_epoch) or 0)
                if age<=JOB_STALE_SECONDS then
                    foundActive=true
                    plainLog('RUNNING_JOB_BLOCK id='..tostring(job.job_id)..' owner='..tostring(job.runner_instance_id)..' age='..age)
                else
                    job.status='queued'; job.recovered_at=timestamp(); job.recovery_count=(job.recovery_count or 0)+1; job.current_stage='recovered'
                    appendJobEvent(job,'recovered','Tarefa recuperada após interrupção','O runner anterior ficou sem heartbeat por '..age..' segundos.','warning')
                    writeJsonAtomic(path,job)
                    plainLog('RUNNING_JOB_RECOVERED id='..tostring(job.job_id)..' age='..age)
                end
            end
        end
    end
    return foundActive
end

function Runner.processQueuedOnce()
    if processing then if activeJobPath and activeJob and diskCancelled(activeJobPath) then finishCancelled(activeJobPath,activeJob,'Cancelamento detectado pelo loop.') end; return 0 end
    if not stableLeader() then return 0 end
    LrFileUtils.createAllDirectories(jobsDir())
    if recoverOrBlockRunningJobs() then return 0 end
    local queued={}; local inspected=0
    for path in LrFileUtils.files(jobsDir()) do
        if isJobFile(path) then
            inspected=inspected+1; local job,err=readJson(path)
            if not job then plainLog('JSON_INVALID path='..tostring(path)..' error='..tostring(err)) elseif tostring(job.status)=='queued' then table.insert(queued,{path=path,job=job}) end
        end
    end
    table.sort(queued,function(a,b) local ac=tostring(a.job.created_at or ''); local bc=tostring(b.job.created_at or ''); if ac==bc then return tostring(a.path)<tostring(b.path) end; return ac<bc end)
    if #queued>0 then writeState('last_scan.txt',timestamp()..'\ninspected='..inspected..'\nprocessed=1\nqueued='..#queued); processJob(queued[1].path,queued[1].job); return 1 end
    writeState('last_scan.txt',timestamp()..'\ninspected='..inspected..'\nprocessed=0'); return 0
end

function Runner.runLoop(shouldStop)
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
end

function Runner.getJobsDir() return jobsDir() end
return Runner
