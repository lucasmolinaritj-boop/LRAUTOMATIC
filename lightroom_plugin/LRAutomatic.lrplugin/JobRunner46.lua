local LrApplication = import 'LrApplication'
local LrFileUtils = import 'LrFileUtils'
local LrLogger = import 'LrLogger'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'
local Json = require 'Json'

local Runner = {}
local logger = LrLogger('LRAutomatic')
logger:enable('logfile')

local processing = false
local previewBatches = {}
local DEFAULT_EXTENSIONS = { cr2=true, cr3=true, dng=true }
local PREVIEW_MAX_ATTEMPTS = 10
local PREVIEW_RETRY_DELAY_SECONDS = 2
local STANDARD_PREVIEW_TOTAL_TIMEOUT_SECONDS = 900

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

local function timestamp()
    local ok, value = pcall(os.date, '!%Y-%m-%dT%H:%M:%SZ')
    return (ok and value) or 'time-unavailable'
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
    local line = timestamp() .. ' ' .. tostring(message) .. '\n'
    appendText(LrPathUtils.child(dataDir(), 'runner-trace.log'), line)
    appendText(LrPathUtils.child(logsDir(), 'plugin.log'), line)
    pcall(function() logger:info(tostring(message)) end)
end

local function writeState(name, text)
    LrFileUtils.createAllDirectories(stateDir())
    local file = io.open(LrPathUtils.child(stateDir(), name), 'wb')
    if file then file:write(tostring(text or '')); file:close() end
end

local function stripBom(content)
    if string.byte(content, 1) == 239 and string.byte(content, 2) == 187 and string.byte(content, 3) == 191 then
        return string.sub(content, 4)
    end
    return content
end

local function readJson(path)
    local file = io.open(path, 'rb')
    if not file then return nil, 'arquivo não pôde ser lido' end
    local content = file:read('*a')
    file:close()
    local ok, decoded = pcall(Json.decode, stripBom(content))
    if not ok or type(decoded) ~= 'table' then return nil, tostring(decoded) end
    return decoded, nil
end

local function writeJson(path, value)
    local ok, encoded = pcall(Json.encode, value)
    if not ok then return false end
    local temp = path .. '.tmp'
    local file = io.open(temp, 'wb')
    if not file then return false end
    file:write(encoded)
    file:close()
    if LrFileUtils.exists(path) then LrFileUtils.delete(path) end
    return LrFileUtils.move(temp, path) == true
end

local function safeWriteJob(path, job)
    if not writeJson(path, job) then plainLog('JOB_WRITE_FAILED path=' .. tostring(path)) end
end

local function appendJobEvent(job, stage, title, detail, level)
    job.events = job.events or {}
    table.insert(job.events, {
        at=timestamp(), stage=stage, title=title,
        detail=tostring(detail or ''), level=level or 'info'
    })
end

local function normalizedExtension(path)
    local ext = string.lower(LrPathUtils.extension(path) or '')
    if string.sub(ext, 1, 1) == '.' then ext = string.sub(ext, 2) end
    return ext
end

local function allowedExtensionTable(request)
    local configured = request and request.allowed_extensions
    if type(configured) ~= 'table' or #configured == 0 then return DEFAULT_EXTENSIONS end
    local result = {}
    for _, value in ipairs(configured) do
        local ext = string.lower(tostring(value or ''))
        if string.sub(ext, 1, 1) == '.' then ext = string.sub(ext, 2) end
        if ext ~= '' then result[ext] = true end
    end
    return next(result) and result or DEFAULT_EXTENSIONS
end

local function isJobFile(path)
    local name = string.lower(LrPathUtils.leafName(path) or tostring(path))
    return string.sub(name, 1, 4) == 'job_' and string.sub(name, -5) == '.json'
end

local function collectFiles(folder, recursive, allowed)
    if not folder or folder == '' then return {}, 'pasta de origem vazia' end
    if not LrFileUtils.exists(folder) then return {}, 'pasta de origem não existe: ' .. tostring(folder) end
    local result = {}
    local iterator = recursive and LrFileUtils.recursiveFiles(folder) or LrFileUtils.files(folder)
    for path in iterator do
        if LrFileUtils.exists(path) and allowed[normalizedExtension(path)] then table.insert(result, path) end
    end
    table.sort(result)
    return result, nil
end

local function refreshTotals(job)
    job.total_discovered, job.total_imported, job.total_skipped, job.total_failed = 0, 0, 0, 0
    for _, progress in ipairs(job.progress or {}) do
        job.total_discovered = job.total_discovered + (progress.discovered or 0)
        job.total_imported = job.total_imported + (progress.imported or 0)
        job.total_skipped = job.total_skipped + (progress.skipped or 0)
        job.total_failed = job.total_failed + (progress.failed or 0)
    end
end

-- Nenhuma operação SDK que possa fazer yield fica dentro de pcall.
local function withWrite(catalog, actionName, fn, detail)
    local ran, timedOut = false, false
    plainLog('WRITE_BEGIN action=' .. actionName .. ' detail=' .. tostring(detail))
    local status = catalog:withWriteAccessDo(actionName, function(context)
        ran = true
        fn(context)
    end, {
        timeout = 15,
        callback = function() timedOut = true end,
    })
    plainLog('WRITE_END action=' .. actionName .. ' status=' .. tostring(status) .. ' ran=' .. tostring(ran) .. ' timeout=' .. tostring(timedOut))
    return ran and not timedOut and (status == nil or status == 'executed'), tostring(status or 'executed')
end

local function findCollection(catalog, name)
    for _, collection in ipairs(catalog:getChildCollections()) do
        if collection:getName() == name then return collection end
    end
    return nil
end

local function ensureCollection(catalog, name)
    if not name or name == '' then return nil, nil end
    local existing = findCollection(catalog, name)
    if existing then return existing, nil end
    local ok, reason = withWrite(catalog, 'LRAutomatic: criar coleção', function()
        catalog:createCollection(name, nil, true)
    end, name)
    if not ok then return nil, reason end
    return findCollection(catalog, name), nil
end

local function importOne(catalog, photoPath)
    if not photoPath or photoPath == '' then return nil, 'failed', 'caminho vazio' end
    if not LrFileUtils.exists(photoPath) then return nil, 'missing', 'arquivo não encontrado' end
    local before = catalog:findPhotoByPath(photoPath)
    if before then return before, 'skipped', nil end
    local importedPhoto = nil
    local ok, reason = withWrite(catalog, 'LRAutomatic: importar foto', function()
        importedPhoto = catalog:addPhoto(photoPath)
    end, photoPath)
    if not ok then return nil, 'failed', reason end
    local after = importedPhoto or catalog:findPhotoByPath(photoPath)
    if after then return after, 'imported', nil end
    return nil, 'failed', 'foto não apareceu no catálogo após addPhoto'
end

local function findPresetByNameOrUuid(name, uuid)
    local function searchFolder(folder)
        for _, preset in ipairs(folder:getDevelopPresets()) do
            if (uuid and preset:getUuid() == uuid) or (name and preset:getName() == name) then return preset end
        end
        if folder.getChildren then
            for _, child in ipairs(folder:getChildren()) do
                local found = searchFolder(child)
                if found then return found end
            end
        end
    end
    for _, folder in ipairs(LrApplication.developPresetFolders()) do
        local found = searchFolder(folder)
        if found then return found end
    end
end

local function applyPreset(catalog, photos, job)
    local request = job.request or {}
    local name, uuid = request.develop_preset_name, request.develop_preset_uuid
    if not name and not uuid then job.preset_status='not_requested'; return true end
    if #photos == 0 then job.preset_status='completed_no_photos'; return true end
    local preset = findPresetByNameOrUuid(name, uuid)
    if not preset then job.preset_status='failed'; job.error='Preset não encontrado: ' .. tostring(name or uuid); return false end
    local applied = 0
    local ok, reason = withWrite(catalog, 'LRAutomatic: aplicar preset', function()
        for _, photo in ipairs(photos) do photo:applyDevelopPreset(preset); applied = applied + 1 end
    end, preset:getName())
    if not ok then job.preset_status='failed'; job.error='Falha ao aplicar preset: ' .. tostring(reason); return false end
    job.preset_status='completed'; job.preset_name_applied=preset:getName(); job.preset_applied_count=applied
    return true
end

local function buildSmartPreviewsWithRetry(catalog, photos, job, jobPath)
    if not ((job.request or {}).build_smart_previews == true) then job.smart_previews_status='not_requested'; return true end
    if #photos == 0 then job.smart_previews_status='completed_no_photos'; return true end

    local pending = photos
    local totalCreated, totalExisted = 0, 0
    job.smart_previews_attempts = 0
    job.smart_previews_status = 'running'

    for attempt = 1, PREVIEW_MAX_ATTEMPTS do
        job.smart_previews_attempts = attempt
        job.smart_previews_pending = #pending
        safeWriteJob(jobPath, job)
        plainLog('SMART_PREVIEW_ATTEMPT attempt=' .. attempt .. ' pending=' .. #pending)

        local result = catalog:buildSmartPreviews(pending)
        local created = result and result.created or {}
        local existed = result and result.existed or {}
        local failed = result and result.failed or {}
        totalCreated = totalCreated + #created
        totalExisted = totalExisted + #existed
        pending = failed

        job.smart_previews_created = totalCreated
        job.smart_previews_existed = totalExisted
        job.smart_previews_failed = #pending
        job.smart_previews_pending = #pending
        safeWriteJob(jobPath, job)

        if #pending == 0 then
            job.smart_previews_status = 'completed'
            appendJobEvent(job, 'smart_preview', 'Smart Previews concluídas', 'Concluídas em ' .. attempt .. ' tentativa(s).', 'info')
            return true
        end
        if attempt < PREVIEW_MAX_ATTEMPTS then LrTasks.sleep(PREVIEW_RETRY_DELAY_SECONDS) end
    end

    job.smart_previews_status = 'failed_after_retries'
    job.smart_previews_failed = #pending
    appendJobEvent(job, 'smart_preview_failed', 'Smart Previews falharam após 10 tentativas', #pending .. ' foto(s) ainda sem Smart Preview.', 'error')
    plainLog('SMART_PREVIEW_GAVE_UP attempts=' .. PREVIEW_MAX_ATTEMPTS .. ' remaining=' .. #pending)
    return false
end

local function finishJob(jobPath, job, failed)
    refreshTotals(job)
    job.current_source=nil
    job.finished_at=timestamp()
    job.status = failed and ((job.total_imported or 0) > 0 and 'partial' or 'failed') or 'completed'
    safeWriteJob(jobPath, job)
    previewBatches[job.job_id]=nil
    processing=false
    plainLog('JOB_END id=' .. tostring(job.job_id) .. ' status=' .. tostring(job.status) .. ' imported=' .. tostring(job.total_imported))
end

local function startStandardPreviewsWithRetry(photos, jobPath, job, baseFailed)
    local request = job.request or {}
    if request.build_standard_previews ~= true then job.standard_previews_status='not_requested'; finishJob(jobPath, job, baseFailed); return end
    if #photos == 0 then job.standard_previews_status='completed_no_photos'; finishJob(jobPath, job, baseFailed); return end

    local size = math.max(256, math.min(16384, tonumber(request.standard_preview_size) or 2048))
    job.standard_previews_status='running'
    job.standard_previews_created=0
    job.standard_previews_failed=0
    job.standard_previews_attempts_total=0
    safeWriteJob(jobPath, job)

    local batch = { remaining=#photos, finished=false, handles={}, attempts={} }
    previewBatches[job.job_id]=batch

    local function finishOne(photoKey, success, errorMessage)
        local active = previewBatches[job.job_id]
        if not active or active.finished then return end
        if success then
            job.standard_previews_created = (job.standard_previews_created or 0) + 1
        else
            job.standard_previews_failed = (job.standard_previews_failed or 0) + 1
            plainLog('STANDARD_PREVIEW_GAVE_UP photo=' .. tostring(photoKey) .. ' error=' .. tostring(errorMessage))
        end
        active.remaining = active.remaining - 1
        job.standard_previews_pending = active.remaining
        safeWriteJob(jobPath, job)
        if active.remaining <= 0 then
            active.finished = true
            job.standard_previews_status = (job.standard_previews_failed or 0) > 0 and 'failed_after_retries' or 'completed'
            if (job.standard_previews_failed or 0) > 0 then
                appendJobEvent(job, 'standard_preview_failed', 'Visualizações padrão falharam após 10 tentativas', tostring(job.standard_previews_failed) .. ' foto(s) ainda sem visualização padrão.', 'error')
            else
                appendJobEvent(job, 'standard_preview', 'Visualizações padrão concluídas', 'Todas as fotos concluídas, com até 10 tentativas por foto.', 'info')
            end
            finishJob(jobPath, job, baseFailed or (job.standard_previews_failed or 0) > 0)
        end
    end

    local requestAttempt
    requestAttempt = function(photo, photoKey)
        local active = previewBatches[job.job_id]
        if not active or active.finished then return end
        local attempt = (active.attempts[photoKey] or 0) + 1
        active.attempts[photoKey] = attempt
        job.standard_previews_attempts_total = (job.standard_previews_attempts_total or 0) + 1
        job.standard_previews_pending = active.remaining
        safeWriteJob(jobPath, job)
        plainLog('STANDARD_PREVIEW_ATTEMPT photo=' .. tostring(photoKey) .. ' attempt=' .. attempt)

        local handle = photo:requestJpegThumbnail(size, size, function(data, errorMessage)
            local current = previewBatches[job.job_id]
            if not current or current.finished then return end
            if data ~= nil then
                finishOne(photoKey, true, nil)
            elseif attempt < PREVIEW_MAX_ATTEMPTS then
                LrTasks.startAsyncTask(function()
                    LrTasks.sleep(PREVIEW_RETRY_DELAY_SECONDS)
                    requestAttempt(photo, photoKey)
                end)
            else
                finishOne(photoKey, false, errorMessage)
            end
        end)
        table.insert(active.handles, handle)
    end

    for index, photo in ipairs(photos) do
        requestAttempt(photo, tostring(index))
    end

    LrTasks.startAsyncTask(function()
        LrTasks.sleep(STANDARD_PREVIEW_TOTAL_TIMEOUT_SECONDS)
        local active = previewBatches[job.job_id]
        if not active or active.finished then return end
        active.finished = true
        local remaining = math.max(0, active.remaining or 0)
        job.standard_previews_failed = (job.standard_previews_failed or 0) + remaining
        job.standard_previews_pending = 0
        job.standard_previews_status = 'timeout_after_retries'
        job.error = job.error or 'Tempo máximo das tentativas de visualização padrão excedido.'
        appendJobEvent(job, 'standard_preview_timeout', 'Visualizações padrão excederam o tempo máximo', remaining .. ' foto(s) ainda pendentes.', 'error')
        finishJob(jobPath, job, true)
    end)
end

local function processSource(catalog, job, source, progress, jobPath, importedPhotos, allowed)
    source=source or {}
    progress.status='running'
    progress.imported=progress.imported or 0
    progress.skipped=progress.skipped or 0
    progress.failed=progress.failed or 0
    job.current_source=source.path
    local recursive=source.recursive
    if recursive == nil then recursive=(job.request or {}).recursive == true end
    local files, collectError=collectFiles(source.path,recursive,allowed)
    if collectError then
        progress.discovered=0; progress.status='failed'; progress.error=collectError
        appendJobEvent(job,'source_missing','Pasta ignorada sem travar o Lightroom',collectError,'error')
        safeWriteJob(jobPath,job)
        return true
    end
    progress.discovered=#files
    safeWriteJob(jobPath,job)
    plainLog('SOURCE_DISCOVERED path=' .. tostring(source.path) .. ' count=' .. tostring(#files))
    local photosForCollection={}
    for _, photoPath in ipairs(files) do
        local photo,result,err=importOne(catalog,photoPath)
        if result=='imported' then
            progress.imported=progress.imported+1
            table.insert(photosForCollection,photo)
            table.insert(importedPhotos,photo)
        elseif result=='skipped' then
            progress.skipped=progress.skipped+1
            table.insert(photosForCollection,photo)
            table.insert(importedPhotos,photo)
        else
            progress.failed=progress.failed+1
            progress.error=tostring(err) .. ': ' .. tostring(photoPath)
            appendJobEvent(job,'file_missing','Arquivo ignorado',progress.error,'warning')
        end
        refreshTotals(job)
        safeWriteJob(jobPath,job)
        LrTasks.yield()
    end
    local collectionName=source.collection
    if not collectionName or collectionName=='' then collectionName=LrPathUtils.leafName(source.path or '') end
    if (job.request or {}).create_collections ~= false and #photosForCollection > 0 then
        local collection,collectionErr=ensureCollection(catalog,collectionName)
        if collection then
            local ok,reason=withWrite(catalog,'LRAutomatic: adicionar à coleção',function() collection:addPhotos(photosForCollection) end,collectionName)
            if not ok then progress.error='Coleção falhou: ' .. tostring(reason) end
        else
            progress.error='Coleção não criada: ' .. tostring(collectionErr)
        end
    end
    progress.status=(progress.failed > 0) and 'partial' or 'completed'
    refreshTotals(job)
    safeWriteJob(jobPath,job)
    return progress.status ~= 'completed'
end

local function processJob(jobPath,job)
    if type(job)~='table' or tostring(job.status)~='queued' then processing=false; return false end
    job.request=type(job.request)=='table' and job.request or {}
    job.progress=type(job.progress)=='table' and job.progress or {}
    local catalog=LrApplication.activeCatalog()
    if not catalog then
        job.status='failed'; job.error='nenhum catálogo ativo'; job.finished_at=timestamp()
        safeWriteJob(jobPath,job); processing=false; return false
    end
    job.active_catalog_path=catalog:getPath()
    job.status='running'; job.error=nil; job.started_at=timestamp()
    safeWriteJob(jobPath,job)
    local importedPhotos={}
    local failed=false
    local sources=type(job.request.sources)=='table' and job.request.sources or {}
    local allowed=allowedExtensionTable(job.request)
    for index,source in ipairs(sources) do
        local progress=job.progress[index]
        if type(progress)~='table' then
            progress={status='queued',discovered=0,imported=0,skipped=0,failed=0}
            job.progress[index]=progress
        end
        if processSource(catalog,job,source,progress,jobPath,importedPhotos,allowed) then failed=true end
    end
    local presetOk=applyPreset(catalog,importedPhotos,job)
    safeWriteJob(jobPath,job)
    local smartOk=buildSmartPreviewsWithRetry(catalog,importedPhotos,job,jobPath)
    safeWriteJob(jobPath,job)
    startStandardPreviewsWithRetry(importedPhotos,jobPath,job,failed or not presetOk or not smartOk)
    return true
end

function Runner.processQueuedOnce()
    if processing then return 0 end
    LrFileUtils.createAllDirectories(jobsDir())
    writeState('runner_alive.txt',timestamp() .. '\njobs=' .. jobsDir())
    local inspected=0
    for path in LrFileUtils.files(jobsDir()) do
        if isJobFile(path) then
            inspected=inspected+1
            local job,readError=readJson(path)
            if not job then
                plainLog('JSON_INVALID path=' .. tostring(path) .. ' error=' .. tostring(readError))
            elseif tostring(job.status)=='queued' then
                processing=true
                writeState('last_scan.txt',timestamp() .. '\ninspected=' .. inspected .. '\nprocessed=1')
                processJob(path,job)
                return 1
            end
        end
    end
    writeState('last_scan.txt',timestamp() .. '\ninspected=' .. inspected .. '\nprocessed=0')
    return 0
end

function Runner.runLoop(shouldStop)
    LrFileUtils.createAllDirectories(jobsDir())
    plainLog('Plugin V4.6 iniciado; previews com até 10 tentativas; monitorando ' .. jobsDir())
    while not shouldStop() do
        writeState('heartbeat.txt',timestamp() .. '\nloop=running\nprocessing=' .. tostring(processing) .. '\njobs=' .. jobsDir())
        Runner.processQueuedOnce()
        LrTasks.sleep(2)
    end
    processing=false
    plainLog('Plugin V4.6 loop encerrado')
end

function Runner.getJobsDir() return jobsDir() end
return Runner
