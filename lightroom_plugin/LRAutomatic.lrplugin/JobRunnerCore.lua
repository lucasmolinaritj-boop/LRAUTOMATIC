local LrApplication = import 'LrApplication'
local LrFileUtils = import 'LrFileUtils'
local LrLogger = import 'LrLogger'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'
local Json = require 'Json'

local Runner = {}
local logger = LrLogger('LRAutomatic')
logger:enable('logfile')

local IMPORT_DEFAULT_DELAYS = { 2, 5, 15 }
local IMPORT_CLOUD_DELAYS = { 5, 15, 30, 60 }
local SMART_PREVIEW_DELAYS = { 5, 15, 30 }
local STANDARD_PREVIEW_DELAYS = { 2, 5 }
local CLAIM_STALE_SECONDS = 20
local JOB_STALE_SECONDS = 45
local PREVIEW_ATTEMPT_TIMEOUT_SECONDS = 90
local DEFAULT_EXTENSIONS = { cr2=true, cr3=true, dng=true }

local taskClock = type(LrTasks.currentTime)=='function' and LrTasks.currentTime() or os.clock()
math.randomseed(os.time() + math.floor((taskClock or 0) * 1000))
local INSTANCE_ID = string.format('%010d-%08x', os.time(), math.random(0, 0x7fffffff))
local processing = false
local activeJobPath = nil
local activeJob = nil
local activePreviewHandle = nil
local leaderSince = nil
local readJson
local writeJsonAtomic

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
local function inventoriesDir() return LrPathUtils.child(dataDir(), 'inventories') end
local function claimPath(id) return LrPathUtils.child(claimsDir(), 'claim_' .. id .. '.json') end
local function previewRetryPath() return LrPathUtils.child(stateDir(), 'preview_retry.json') end
local previewRetry = { smart = {}, standard = {} }
local function loadPreviewRetry()
    local value = readJson(previewRetryPath())
    if type(value) == 'table' then
        value.smart = type(value.smart) == 'table' and value.smart or {}
        value.standard = type(value.standard) == 'table' and value.standard or {}
        previewRetry = value
    else previewRetry = { smart = {}, standard = {} } end
end
local function savePreviewRetry()
    LrFileUtils.createAllDirectories(stateDir())
    writeJsonAtomic(previewRetryPath(), previewRetry)
end
local function photoPath(photo)
    if not photo then return nil end
    local ok, path = pcall(function() return photo:getRawMetadata('path') end)
    return ok and path or nil
end

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

readJson = function(path)
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

writeJsonAtomic = function(path, value)
    local encoded = encodeJson(value)
    if not encoded then return false end

    local parent = LrPathUtils.parent(path)
    if parent and parent ~= '' then LrFileUtils.createAllDirectories(parent) end

    local nonce = tostring(os.time()) .. '.' .. tostring(math.random(100000,999999))
    local temp = path .. '.tmp.' .. INSTANCE_ID .. '.' .. nonce
    local backup = path .. '.bak.' .. INSTANCE_ID

    local tempFile = io.open(temp, 'wb')
    if not tempFile then return false end
    local writeOk = tempFile:write(encoded)
    tempFile:flush()
    tempFile:close()
    if not writeOk then
        if LrFileUtils.exists(temp) then LrFileUtils.delete(temp) end
        return false
    end

    local verifyFile = io.open(temp, 'rb')
    local verified = false
    if verifyFile then
        verified = verifyFile:read('*a') == encoded
        verifyFile:close()
    end
    if not verified then
        if LrFileUtils.exists(temp) then LrFileUtils.delete(temp) end
        return false
    end

    local function verifyDestination()
        local check = io.open(path, 'rb')
        if not check then return false end
        local same = check:read('*a') == encoded
        check:close()
        return same
    end

    for attempt=1,20 do
        if not LrFileUtils.exists(path) then
            if LrFileUtils.move(temp, path) == true and verifyDestination() then
                if LrFileUtils.exists(backup) then LrFileUtils.delete(backup) end
                return true
            end
        else
            if LrFileUtils.exists(backup) then LrFileUtils.delete(backup) end
            local backedUp = LrFileUtils.move(path, backup) == true
            if backedUp then
                if LrFileUtils.move(temp, path) == true and verifyDestination() then
                    if LrFileUtils.exists(backup) then LrFileUtils.delete(backup) end
                    return true
                end
                if LrFileUtils.exists(path) then LrFileUtils.delete(path) end
                LrFileUtils.move(backup, path)
            end
        end

        -- Fallback para bloqueios de rename causados por antivírus/indexadores.
        -- Só é aceito após releitura byte a byte do destino.
        if attempt % 4 == 0 then
            local direct = io.open(path, 'wb')
            if direct then
                local directOk = direct:write(encoded)
                direct:flush()
                direct:close()
                if directOk and verifyDestination() then
                    if LrFileUtils.exists(temp) then LrFileUtils.delete(temp) end
                    if LrFileUtils.exists(backup) then LrFileUtils.delete(backup) end
                    return true
                end
            end
        end

        LrTasks.sleep(math.min(0.15 * attempt, 1.0))
    end

    -- Mantém o temporário íntegro para diagnóstico/recuperação; nunca destrói
    -- silenciosamente a única cópia nova quando todas as trocas falham.
    plainLog('JSON_ATOMIC_EXHAUSTED path=' .. tostring(path) .. ' temp=' .. tostring(temp))
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
    -- Inventários extensos ficam em manifestos separados; jobs antigos são
    -- enxugados automaticamente na primeira persistência pelo núcleo novo.
    for _, progress in ipairs(job.progress or {}) do
        if type(progress)=='table' then progress.discovered_files=nil end
    end
    job.updated_at=timestamp()
    job.runner_instance_id = INSTANCE_ID
    job.runner_heartbeat_epoch = os.time()
    job.runner_heartbeat_at = timestamp()
    for attempt=1,3 do
        if writeJsonAtomic(path, job) then return true end
        if diskCancelled(path) then
            job.status = 'cancelled'
            return false
        end
        if attempt < 3 then LrTasks.sleep(0.5 * attempt) end
    end
    plainLog('JOB_WRITE_FAILED_FINAL path=' .. tostring(path))
    return false
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
    for elapsed=1,seconds do
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return false end
        if elapsed % 5 == 0 or elapsed == seconds then
            updateClaim(job.job_id)
            safeWriteJob(jobPath,job)
        end
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

local function allowedSignature(allowed)
    local values={}
    for extension, enabled in pairs(allowed or {}) do
        if enabled then table.insert(values,tostring(extension)) end
    end
    table.sort(values)
    return table.concat(values,',')
end

local function inventoryPath(job,sourceIndex)
    local jobId=tostring(job.job_id or 'unknown'):gsub('[^%w%-_]','_')
    return LrPathUtils.child(inventoriesDir(),jobId..'_source_'..tostring(sourceIndex)..'.json')
end

local function loadOrCreateInventory(job,source,sourceIndex,recursive,allowed)
    LrFileUtils.createAllDirectories(inventoriesDir())
    local path=inventoryPath(job,sourceIndex)
    local signature=allowedSignature(allowed)
    local manifest=readJson(path)
    if type(manifest)=='table'
        and manifest.version==1
        and tostring(manifest.job_id)==tostring(job.job_id)
        and tostring(manifest.source_path)==tostring(source.path)
        and manifest.recursive==(recursive==true)
        and tostring(manifest.allowed_signature)==signature
        and type(manifest.files)=='table'
    then
        manifest.imported_paths=type(manifest.imported_paths)=='table' and manifest.imported_paths or {}
        return manifest.files,nil,path,true,manifest
    end

    local files,collectError=collectFiles(source.path,recursive,allowed)
    if collectError then return {},collectError,path,false,nil end
    manifest={
        version=1,
        job_id=job.job_id,
        source_index=sourceIndex,
        source_path=source.path,
        recursive=recursive==true,
        allowed_signature=signature,
        created_at=timestamp(),
        count=#files,
        files=files,
        imported_paths={},
    }
    local written=writeJsonAtomic(path,manifest)
    if not written then
        plainLog('INVENTORY_MANIFEST_WRITE_FAILED path='..tostring(path))
        path=nil
    end
    return files,nil,path,false,manifest
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
    if not catalog or type(catalog.withWriteAccessDo)~='function' then
        return false,'API withWriteAccessDo indisponível'
    end
    -- Não envolver este gate nem sua callback em pcall/xpcall. Operações do
    -- catálogo podem fazer yield no Lua 5.1 do Lightroom Classic 10.4.
    local status=catalog:withWriteAccessDo(actionName,function(context)
        ran=true
        fn(context)
    end,{timeout=15,callback=function() timedOut=true end})
    plainLog('WRITE_END action='..actionName..' status='..tostring(status)..' ran='..tostring(ran)..' timeout='..tostring(timedOut))
    return ran and not timedOut and (status==nil or status=='executed'),tostring(status or 'executed')
end

local catalogPhotoIndex=nil
local catalogPhotoIndexPath=nil

local function normalizeCatalogPath(path)
    if not path then return nil end
    return string.lower((tostring(path):gsub('/','\\')))
end

local function ensureCatalogPhotoIndex(catalog)
    local path=catalog:getPath()
    if catalogPhotoIndex and catalogPhotoIndexPath==path then return catalogPhotoIndex end
    local index={}
    local photos=catalog:getAllPhotos()
    for _,photo in ipairs(photos or {}) do
        local key=normalizeCatalogPath(photo:getRawMetadata('path'))
        if key then index[key]=photo end
    end
    catalogPhotoIndex=index
    catalogPhotoIndexPath=path
    plainLog('CATALOG_INDEX_READY count='..tostring(#(photos or {})))
    return index
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

local function inspectRawFile(path)
    if not path or path=='' then return false,'caminho vazio',0 end
    if not LrFileUtils.exists(path) then return false,'arquivo não encontrado',0 end

    local file,openError=io.open(path,'rb')
    if not file then return false,'arquivo não pôde ser aberto para leitura: '..tostring(openError),0 end

    local sizeBefore,seekError=file:seek('end')
    if not sizeBefore then file:close(); return false,'não foi possível obter o tamanho: '..tostring(seekError),0 end
    if sizeBefore<=0 then file:close(); return false,'arquivo com 0 bytes ou ainda não materializado',0 end

    local sampleSize=math.min(65536,sizeBefore)
    local startOk=file:seek('set',0)
    local startData=startOk and file:read(sampleSize) or nil
    local tailOffset=math.max(0,sizeBefore-sampleSize)
    local tailOk=file:seek('set',tailOffset)
    local tailData=tailOk and file:read(sampleSize) or nil
    local sizeAfter=file:seek('end')
    file:close()

    if not startData or #startData==0 then return false,'início do arquivo ilegível ou indisponível',sizeBefore end
    if not tailData or #tailData==0 then return false,'fim do arquivo ilegível ou download incompleto',sizeBefore end
    if not sizeAfter or sizeAfter~=sizeBefore then return false,'tamanho do arquivo mudou durante a leitura',tonumber(sizeAfter) or sizeBefore end
    return true,nil,sizeBefore
end

local function importOneAttempt(catalog,path)
    if not path or path=='' then return nil,'failed','caminho vazio' end
    local index=ensureCatalogPhotoIndex(catalog)
    local normalizedPath=normalizeCatalogPath(path)
    local before=normalizedPath and index[normalizedPath] or nil
    if before then return before,'skipped',nil end

    local ready,readError,fileSize=inspectRawFile(path)
    if not ready then
        plainLog('IMPORT_PREFLIGHT_FAILED path='..tostring(path)..' size='..tostring(fileSize or 0)..' error='..tostring(readError))
        return nil,'failed','pré-validação recusou o RAW: '..tostring(readError)
    end
    plainLog('IMPORT_PREFLIGHT_OK path='..tostring(path)..' size='..tostring(fileSize))

    local imported=nil
    local ok,reason=withWrite(catalog,'LRAutomatic: importar foto',function()
        imported=catalog:addPhoto(path)
    end,path)

    if not ok then return nil,'failed','acesso de escrita recusado: '..tostring(reason) end

    local after=imported or catalog:findPhotoByPath(path)
    if after then
        if normalizedPath then index[normalizedPath]=after end
        return after,'imported',nil
    end
    return nil,'failed','foto não apareceu no catálogo após addPhoto'
end

local badFilesInJob={}

local function recordBadFile(job,path,reason,attempts,category)
    local key=tostring(path or '')
    if key=='' or badFilesInJob[key] then return end
    badFilesInJob[key]=tostring(reason or 'erro desconhecido')
    job.bad_files=type(job.bad_files)=='table' and job.bad_files or {}
    table.insert(job.bad_files,{
        path=key,
        reason=tostring(reason or 'erro desconhecido'),
        error=tostring(reason or 'erro desconhecido'),
        attempts=attempts or 1,
        category=category or 'permanent',
        at=timestamp(),
    })
    job.bad_files_count=#job.bad_files
    job.completed_with_file_errors=true
    appendJobEvent(job,'import_file_failed','RAW isolado',key..' — '..tostring(reason),'error')
    plainLog('IMPORT_FILE_ISOLATED path='..key..' category='..tostring(category)..' error='..tostring(reason))
end

local function classifyImportError(reason)
    local lowered=string.lower(tostring(reason or ''))
    if string.find(lowered,'yieldtoscheduler',1,true)
        or string.find(lowered,'attempt to call',1,true)
        or string.find(lowered,'nil value',1,true)
        or string.find(lowered,'unsupported',1,true)
        or string.find(lowered,'inválid',1,true)
        or string.find(lowered,'invalid',1,true)
        or string.find(lowered,'corrupt',1,true)
        or string.find(lowered,'não apareceu',1,true)
    then
        return 'permanent',{}
    end
    if string.find(lowered,'0 bytes',1,true)
        or string.find(lowered,'materializ',1,true)
        or string.find(lowered,'download',1,true)
        or string.find(lowered,'indisponível',1,true)
        or string.find(lowered,'não pôde ser aberto',1,true)
        or string.find(lowered,'tamanho do arquivo mudou',1,true)
    then
        return 'cloud',IMPORT_CLOUD_DELAYS
    end
    if string.find(lowered,'arquivo não encontrado',1,true)
        or string.find(lowered,'caminho vazio',1,true)
        or string.find(lowered,'ilegível',1,true)
    then
        return 'permanent',{}
    end
    return 'transient',IMPORT_DEFAULT_DELAYS
end

local function importOneWithRetry(catalog,path,job,jobPath)
    if badFilesInJob[path] then return nil,'bad_file',badFilesInJob[path] end
    local lastError=nil
    local retryDelays=IMPORT_DEFAULT_DELAYS
    local category='transient'
    local maxAttempts=#retryDelays+1
    local attempt=1
    while attempt<=maxAttempts do
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return nil,'cancelled','cancelado' end
        job.current_stage='importing'; job.current_photo=path; job.current_photo_attempt=attempt
        job.import_attempts_total=(job.import_attempts_total or 0)+1
        safeWriteJob(jobPath,job)
        local photo,result,err=importOneAttempt(catalog,path)
        if result=='imported' or result=='skipped' then return photo,result,nil end
        lastError=err
        category,retryDelays=classifyImportError(err)
        maxAttempts=#retryDelays+1
        local delay=retryDelays[attempt]
        if delay then
            appendJobEvent(
                job,
                'import_retry',
                'Nova tentativa de importação',
                path..' — categoria '..category..', tentativa '..(attempt+1)..' de '..maxAttempts..' em '..delay..'s.',
                'warning'
            )
            if not sleepInterruptible(jobPath,job,delay) then return nil,'cancelled','cancelado' end
        end
        attempt=attempt+1
    end
    local finalError=lastError or 'falha desconhecida'
    recordBadFile(job,path,finalError,attempt-1,category)
    safeWriteJob(jobPath,job)
    return nil,'bad_file',finalError
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
    local pending=photos; local inputPhotos=photos; local createdTotal,existedTotal=0,0
    job.current_stage='smart_preview'; job.smart_previews_status='running'
    for attempt=1,#SMART_PREVIEW_DELAYS+1 do
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return false end
        job.smart_previews_attempts=attempt; job.smart_previews_pending=#pending; safeWriteJob(jobPath,job)
        local result=catalog:buildSmartPreviews(pending)
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return false end
        local created=result and result.created or {}; local existed=result and result.existed or {}; local failed=result and result.failed or pending
        createdTotal=createdTotal+#created; existedTotal=existedTotal+#existed; pending=failed
        job.smart_previews_created=createdTotal; job.smart_previews_existed=existedTotal; job.smart_previews_failed=#pending; job.smart_previews_pending=#pending
        safeWriteJob(jobPath,job)
        if #pending==0 then for _,p in ipairs(inputPhotos) do local path=photoPath(p); if path then previewRetry.smart[path]=nil end end; savePreviewRetry(); job.smart_previews_status='completed'; return true end
        local delay=SMART_PREVIEW_DELAYS[attempt]
        if delay and not sleepInterruptible(jobPath,job,delay) then return false end
    end
    job.smart_previews_status='failed_after_retries'; job.smart_previews_failed=#pending; for _,p in ipairs(pending) do local path=photoPath(p); if path then previewRetry.smart[path]=true end end; savePreviewRetry()
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
        for attempt=1,#STANDARD_PREVIEW_DELAYS+1 do
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
            local delay=STANDARD_PREVIEW_DELAYS[attempt]
            if delay and not sleepInterruptible(jobPath,job,delay) then return false end
        end
        local currentPath=photoPath(photo); if success then job.standard_previews_created=job.standard_previews_created+1; if currentPath then previewRetry.standard[currentPath]=nil end else job.standard_previews_failed=job.standard_previews_failed+1; if currentPath then previewRetry.standard[currentPath]=true end; plainLog('STANDARD_PREVIEW_GAVE_UP photo='..index..' error='..tostring(lastError)) end; savePreviewRetry()
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

local function processSource(catalog,job,source,sourceIndex,progress,jobPath,importedPhotos,smartPhotos,standardPhotos,allowed)
    source=source or {}; progress.status='running'; progress.imported=progress.imported or 0; progress.skipped=progress.skipped or 0; progress.failed=progress.failed or 0
    job.current_source=source.path
    local recursive=source.recursive; if recursive==nil then recursive=(job.request or {}).recursive==true end
    job.current_stage='counting'
    local files,collectError,manifestPath,reusedInventory,manifest=loadOrCreateInventory(job,source,sourceIndex,recursive,allowed)
    if collectError then
        progress.discovered=0
        progress.scan_completed=false
        progress.status='failed'
        progress.error=collectError
        safeWriteJob(jobPath,job)
        return true
    end
    progress.discovered=#files
    progress.scan_completed=true
    progress.scan_completed_at=progress.scan_completed_at or timestamp()
    progress.inventory_manifest=manifestPath
    progress.inventory_reused=reusedInventory==true
    if reusedInventory then job.inventory_reused_count=(job.inventory_reused_count or 0)+1 end
    progress.error=nil
    refreshTotals(job)
    job.current_stage='counted'
    safeWriteJob(jobPath,job)
    job.current_stage='importing'
    local photosForCollection={}
    local progressSinceWrite=0
    local lastProgressWrite=os.time()
    local manifestDirty=false
    local importedPathSet={}
    for _,importedPath in ipairs((manifest and manifest.imported_paths) or {}) do
        importedPathSet[tostring(importedPath)]=true
        local photo=ensureCatalogPhotoIndex(catalog)[normalizeCatalogPath(importedPath)]
        if photo then
            table.insert(importedPhotos,photo)
            table.insert(smartPhotos,photo)
            table.insert(standardPhotos,photo)
        end
    end
    local nextIndex=math.max(1,tonumber(progress.next_index) or 1)
    for fileIndex=nextIndex,#files do
        local path=files[fileIndex]
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return true end
        local photo,result,err=importOneWithRetry(catalog,path,job,jobPath)
        if result=='cancelled' then
            return true
        elseif result=='imported' then
            progress.imported=progress.imported+1
            table.insert(photosForCollection,photo)
            table.insert(importedPhotos,photo)
            table.insert(smartPhotos,photo)
            table.insert(standardPhotos,photo)
            if manifest and not importedPathSet[path] then
                importedPathSet[path]=true
                table.insert(manifest.imported_paths,path)
                manifestDirty=true
            end
        elseif result=='skipped' then
            progress.skipped=progress.skipped+1
            table.insert(photosForCollection,photo)
            if previewRetry.smart[path] then table.insert(smartPhotos,photo) end
            if previewRetry.standard[path] then table.insert(standardPhotos,photo) end
        elseif result=='bad_file' then
            progress.failed=progress.failed+1
            progress.error='RAW isolado: '..tostring(path)..' — '..tostring(err)
        else
            progress.failed=progress.failed+1
            progress.error=tostring(err)..': '..tostring(path)
        end
        progress.next_index=fileIndex+1
        refreshTotals(job)
        progressSinceWrite=progressSinceWrite+1
        local now=os.time()
        if progressSinceWrite>=10 or now-lastProgressWrite>=2 then
            if manifestDirty and manifestPath then
                writeJsonAtomic(manifestPath,manifest)
                manifestDirty=false
            end
            safeWriteJob(jobPath,job)
            progressSinceWrite=0
            lastProgressWrite=now
        end
        LrTasks.yield()
    end
    if manifestDirty and manifestPath then writeJsonAtomic(manifestPath,manifest) end
    if progressSinceWrite>0 then safeWriteJob(jobPath,job) end
    photosForCollection={}
    local finalIndex=ensureCatalogPhotoIndex(catalog)
    for _,path in ipairs(files) do
        local photo=finalIndex[normalizeCatalogPath(path)]
        if photo then table.insert(photosForCollection,photo) end
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
    badFilesInJob={}
    job.request=type(job.request)=='table' and job.request or {}; job.progress=type(job.progress)=='table' and job.progress or {}; loadPreviewRetry()
    local catalog=LrApplication.activeCatalog()
    if not catalog then job.status='failed'; job.error='nenhum catálogo ativo'; job.finished_at=timestamp(); safeWriteJob(jobPath,job); clearActive(); return false end
    job.active_catalog_path=catalog:getPath(); job.status='running'; job.error=nil; job.started_at=job.started_at or timestamp(); job.current_stage='starting'; safeWriteJob(jobPath,job)
    local importedPhotos={}; local smartPhotos={}; local standardPhotos={}; local failed=false; local sources=type(job.request.sources)=='table' and job.request.sources or {}; local allowed=allowedExtensionTable(job.request)
    for index,source in ipairs(sources) do
        local progress=job.progress[index]; if type(progress)~='table' then progress={status='queued',discovered=0,imported=0,skipped=0,failed=0}; job.progress[index]=progress end
        if processSource(catalog,job,source,index,progress,jobPath,importedPhotos,smartPhotos,standardPhotos,allowed) then failed=true end
        if tostring(job.status)=='cancelled' or not processing then return false end
    end
    job.preset_candidate_count=#importedPhotos
    job.preset_skipped_existing_count=job.total_skipped or 0
    local presetOk=applyPreset(catalog,importedPhotos,job,jobPath); safeWriteJob(jobPath,job)
    if tostring(job.status)=='cancelled' or not processing then return false end
    local smartOk=buildSmartPreviewsWithRetry(catalog,smartPhotos,job,jobPath); safeWriteJob(jobPath,job)
    if tostring(job.status)=='cancelled' or not processing then return false end
    local standardOk=standardPreviewsSerial(standardPhotos,jobPath,job)
    if tostring(job.status)=='cancelled' or not processing then return false end
    finishJob(jobPath,job,failed or not presetOk or not smartOk or not standardOk)
    return true
end

local function recoverOrBlockRunningJobs()
    local now=os.time(); local foundActive=false
    local liveByJob={}
    for _,claim in ipairs(activeClaims()) do
        if claim.processing==true and claim.current_job_id then
            liveByJob[tostring(claim.current_job_id)]=tostring(claim.instance_id or '')
        end
    end
    for path in LrFileUtils.files(jobsDir()) do
        if isJobFile(path) then
            local job=readJson(path)
            if job and tostring(job.status)=='running' then
                local jobId=tostring(job.job_id or '')
                local owner=tostring(job.runner_instance_id or '')
                local liveOwner=liveByJob[jobId]
                local age=now-(tonumber(job.runner_heartbeat_epoch) or 0)
                if liveOwner and liveOwner==owner and age<=JOB_STALE_SECONDS then
                    foundActive=true
                    plainLog('RUNNING_JOB_BLOCK id='..jobId..' owner='..owner..' age='..age..' claim=live')
                else
                    job.status='queued'; job.recovered_at=timestamp(); job.recovery_count=(job.recovery_count or 0)+1; job.current_stage='recovered_orphan'; job.runner_instance_id=nil
                    appendJobEvent(job,'recovered','Tarefa órfã devolvida à fila','Nenhum runner ativo possuía este job.','warning')
                    writeJsonAtomic(path,job)
                    plainLog('RUNNING_JOB_RECOVERED_ORPHAN id='..jobId..' owner='..owner..' age='..age)
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
    plainLog('Plugin Core 5.0 iniciado; runner canônico; retries classificados; inventário externo')
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
    plainLog('Plugin Core 5.0 loop encerrado')
end

function Runner.getJobsDir() return jobsDir() end
Runner.engine_name='JobRunnerCore'
Runner.engine_version='5.0.0-canonical-yield-safe'
return Runner
