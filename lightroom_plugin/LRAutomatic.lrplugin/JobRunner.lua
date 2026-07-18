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
local STANDARD_PREVIEW_TIMEOUT_SECONDS = 180

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

local function readText(path)
    local file, err = io.open(path, 'rb')
    if not file then return nil, err end
    local content = file:read('*a')
    file:close()
    return content, nil
end

local function writeText(path, content)
    local file, err = io.open(path, 'wb')
    if not file then return false, err end
    local ok, writeErr = file:write(content or '')
    file:close()
    if not ok then return false, writeErr end
    return true, nil
end

local function appendText(path, content)
    local file, err = io.open(path, 'ab')
    if not file then return false, err end
    local ok, writeErr = file:write(content or '')
    file:close()
    if not ok then return false, writeErr end
    return true, nil
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
    writeText(LrPathUtils.child(stateDir(), name), tostring(text or ''))
end

local function stripBom(content)
    if string.byte(content, 1) == 239 and string.byte(content, 2) == 187 and string.byte(content, 3) == 191 then
        return string.sub(content, 4)
    end
    return content
end

local function readJson(path)
    local content, err = readText(path)
    if not content then return nil, 'arquivo não pôde ser lido: ' .. tostring(err) end
    local ok, decoded = pcall(Json.decode, stripBom(content))
    if not ok then return nil, tostring(decoded) end
    if type(decoded) ~= 'table' then return nil, 'JSON raiz não é objeto' end
    return decoded, nil
end

local function writeJson(path, value)
    local encodedOk, encoded = pcall(Json.encode, value)
    if not encodedOk then return false, 'falha ao codificar JSON: ' .. tostring(encoded) end
    local temp = path .. '.tmp'
    local ok, err = writeText(temp, encoded)
    if not ok then return false, 'não foi possível gravar temporário: ' .. tostring(err) end
    if LrFileUtils.exists(path) then LrFileUtils.delete(path) end
    if not LrFileUtils.move(temp, path) then
        return false, 'não foi possível substituir ' .. tostring(path)
    end
    return true, nil
end

local function safeWriteJob(jobPath, job)
    local ok, err = writeJson(jobPath, job)
    if not ok then plainLog('JOB_WRITE_FAILED path=' .. tostring(jobPath) .. ' error=' .. tostring(err)) end
    return ok
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
    if next(result) == nil then return DEFAULT_EXTENSIONS end
    return result
end

local function extensionsLabel(allowed)
    local values = {}
    for ext, enabled in pairs(allowed) do if enabled then table.insert(values, ext) end end
    table.sort(values)
    return table.concat(values, ',')
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
        if LrFileUtils.exists(path) and allowed[normalizedExtension(path)] then
            table.insert(result, path)
        end
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

local function appendJobEvent(job, stage, title, detail, level)
    job.events = job.events or {}
    table.insert(job.events, {
        at = timestamp(), stage = stage, title = title,
        detail = tostring(detail or ''), level = level or 'info',
    })
end

local function withWriteRetry(catalog, actionName, fn, detail)
    for attempt = 1, 3 do
        local ran, timedOut, actionError = false, false, nil
        plainLog('WRITE_BEGIN action=' .. actionName .. ' attempt=' .. attempt .. ' detail=' .. tostring(detail))
        local status = catalog:withWriteAccessDo(actionName, function(context)
            ran = true
            -- Este pcall envolve apenas uma operação curta que não chama sleep/yield.
            local ok, err = pcall(fn, context)
            if not ok then actionError = tostring(err) end
        end, {
            timeout = 10,
            callback = function() timedOut = true end,
        })
        plainLog('WRITE_END action=' .. actionName .. ' attempt=' .. attempt .. ' status=' .. tostring(status) .. ' ran=' .. tostring(ran) .. ' timeout=' .. tostring(timedOut) .. ' error=' .. tostring(actionError))
        if ran and not actionError and (status == nil or status == 'executed') then return true, 'executed' end
        if actionError then return false, actionError end
        if attempt < 3 then LrTasks.sleep(attempt) end
    end
    return false, 'write_access_aborted'
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
    local ok, reason = withWriteRetry(catalog, 'LRAutomatic: criar coleção', function()
        catalog:createCollection(name, nil, true)
    end, name)
    if not ok then return nil, reason end
    return findCollection(catalog, name), nil
end

local function importOne(catalog, photoPath)
    if not photoPath or photoPath == '' then return nil, 'failed', 'caminho de foto vazio' end
    if not LrFileUtils.exists(photoPath) then
        plainLog('ADD_PHOTO_MISSING path=' .. tostring(photoPath))
        return nil, 'missing', 'arquivo não encontrado no momento da importação'
    end

    local before = catalog:findPhotoByPath(photoPath)
    if before then
        plainLog('ADD_PHOTO_SKIPPED path=' .. tostring(photoPath))
        return before, 'skipped', nil
    end

    local importedPhoto = nil
    local ok, reason = withWriteRetry(catalog, 'LRAutomatic: importar foto', function()
        if not LrFileUtils.exists(photoPath) then error('arquivo desapareceu antes de addPhoto') end
        importedPhoto = catalog:addPhoto(photoPath)
    end, photoPath)
    if not ok then return nil, 'failed', reason end

    local after = importedPhoto or catalog:findPhotoByPath(photoPath)
    plainLog('ADD_PHOTO_RESULT path=' .. tostring(photoPath) .. ' returned=' .. tostring(importedPhoto) .. ' found=' .. tostring(after))
    if after then return after, 'imported', nil end
    return nil, 'failed', 'addPhoto executou, mas a foto não apareceu no catálogo'
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
        return nil
    end
    for _, folder in ipairs(LrApplication.developPresetFolders()) do
        local found = searchFolder(folder)
        if found then return found end
    end
    return nil
end

local function applyPreset(catalog, photos, job)
    local request = job.request or {}
    local name, uuid = request.develop_preset_name, request.develop_preset_uuid
    if not name and not uuid then job.preset_status = 'not_requested'; return true end
    if #photos == 0 then job.preset_status = 'completed_no_photos'; return true end

    job.preset_status = 'running'
    local preset = findPresetByNameOrUuid(name, uuid)
    if not preset then
        job.preset_status = 'failed'
        job.error = 'Preset não encontrado: ' .. tostring(name or uuid)
        return false
    end

    local applied = 0
    local ok, reason = withWriteRetry(catalog, 'LRAutomatic: aplicar preset', function()
        for _, photo in ipairs(photos) do
            photo:applyDevelopPreset(preset)
            applied = applied + 1
        end
    end, preset:getName())
    if not ok then
        job.preset_status = 'failed'
        job.error = 'Falha ao aplicar preset: ' .. tostring(reason)
        return false
    end
    job.preset_status = 'completed'
    job.preset_name_applied = preset:getName()
    job.preset_applied_count = applied
    return true
end

local function buildSmartPreviews(catalog, photos, job)
    if not ((job.request or {}).build_smart_previews == true) then job.smart_previews_status = 'not_requested'; return true end
    if #photos == 0 then job.smart_previews_status = 'completed_no_photos'; return true end

    job.smart_previews_status = 'running'
    plainLog('SMART_PREVIEWS_BEGIN count=' .. tostring(#photos))
    local result = catalog:buildSmartPreviews(photos)
    local created = result and result.created or {}
    local existed = result and result.existed or {}
    local failed = result and result.failed or {}
    job.smart_previews_created = #created
    job.smart_previews_existed = #existed
    job.smart_previews_failed = #failed
    job.smart_previews_status = (#failed > 0) and 'partial' or 'completed'
    return #failed == 0
end

local function finalStatus(job, baseFailed, standardFailed)
    if baseFailed or standardFailed then return (job.total_imported or 0) > 0 and 'partial' or 'failed' end
    return 'completed'
end

local function finishJob(jobPath, job, baseFailed, standardFailed)
    refreshTotals(job)
    job.current_source = nil
    job.finished_at = timestamp()
    job.status = finalStatus(job, baseFailed, standardFailed)
    safeWriteJob(jobPath, job)
    plainLog('JOB_END id=' .. tostring(job.job_id) .. ' status=' .. tostring(job.status) .. ' imported=' .. tostring(job.total_imported))
    previewBatches[job.job_id] = nil
    processing = false
end

local function startStandardPreviews(photos, jobPath, job, baseFailed)
    local request = job.request or {}
    if request.build_standard_previews ~= true then job.standard_previews_status = 'not_requested'; finishJob(jobPath, job, baseFailed, false); return end
    if #photos == 0 then job.standard_previews_status = 'completed_no_photos'; finishJob(jobPath, job, baseFailed, false); return end

    local size = tonumber(request.standard_preview_size) or 2048
    if size < 256 then size = 256 end
    if size > 16384 then size = 16384 end

    job.standard_previews_status = 'running'
    job.standard_previews_created = 0
    job.standard_previews_failed = 0
    safeWriteJob(jobPath, job)

    local batch = { remaining=#photos, handles={}, finished=false, started_at=os.time() }
    previewBatches[job.job_id] = batch

    local function completeOne(success, errorMessage)
        local active = previewBatches[job.job_id]
        if not active or active.finished then return end
        if success then
            job.standard_previews_created = (job.standard_previews_created or 0) + 1
        else
            job.standard_previews_failed = (job.standard_previews_failed or 0) + 1
            plainLog('STANDARD_PREVIEW_FAILED error=' .. tostring(errorMessage))
        end
        active.remaining = active.remaining - 1
        if active.remaining <= 0 then
            active.finished = true
            job.standard_previews_status = (job.standard_previews_failed or 0) > 0 and 'partial' or 'completed'
            finishJob(jobPath, job, baseFailed, (job.standard_previews_failed or 0) > 0)
        end
    end

    for _, photo in ipairs(photos) do
        local handle = photo:requestJpegThumbnail(size, size, function(data, errorMessage)
            completeOne(data ~= nil, errorMessage)
        end)
        table.insert(batch.handles, handle)
    end

    LrTasks.startAsyncTask(function()
        LrTasks.sleep(STANDARD_PREVIEW_TIMEOUT_SECONDS)
        local active = previewBatches[job.job_id]
        if not active or active.finished then return end
        active.finished = true
        local remaining = math.max(0, active.remaining or 0)
        job.standard_previews_failed = (job.standard_previews_failed or 0) + remaining
        job.standard_previews_status = 'partial'
        job.error = job.error or ('Visualizações padrão excederam ' .. tostring(STANDARD_PREVIEW_TIMEOUT_SECONDS) .. ' segundos.')
        finishJob(jobPath, job, baseFailed, true)
    end)
end

local function failSource(job, progress, jobPath, message)
    progress.status = 'failed'
    progress.error = tostring(message)
    progress.failed = progress.failed or 0
    appendJobEvent(job, 'source_missing', 'Pasta ignorada sem travar o Lightroom', message, 'error')
    refreshTotals(job)
    safeWriteJob(jobPath, job)
    plainLog('SOURCE_FAILED_RECOVERABLE error=' .. tostring(message))
end

local function processSource(catalog, job, source, progress, jobPath, postPhotos, allowed)
    progress.status = 'running'
    progress.imported = progress.imported or 0
    progress.skipped = progress.skipped or 0
    progress.failed = progress.failed or 0
    source = source or {}
    job.current_source = source.path

    local recursive = source.recursive
    if recursive == nil then recursive = (job.request or {}).recursive == true end
    local files, collectError = collectFiles(source.path, recursive, allowed)
    if collectError then
        progress.discovered = 0
        failSource(job, progress, jobPath, collectError)
        return false
    end

    progress.discovered = #files
    refreshTotals(job)
    safeWriteJob(jobPath, job)
    plainLog('SOURCE_DISCOVERED path=' .. tostring(source.path) .. ' count=' .. tostring(#files) .. ' extensions=' .. extensionsLabel(allowed))

    local photosForCollection = {}
    for _, photoPath in ipairs(files) do
        local photo, result, err = importOne(catalog, photoPath)
        if result == 'imported' then
            progress.imported = progress.imported + 1
            table.insert(photosForCollection, photo)
            table.insert(postPhotos, photo)
        elseif result == 'skipped' then
            progress.skipped = progress.skipped + 1
            table.insert(photosForCollection, photo)
        else
            progress.failed = progress.failed + 1
            progress.error = tostring(err) .. ': ' .. tostring(photoPath)
            appendJobEvent(job, 'file_missing', 'Arquivo ignorado', progress.error, 'warning')
        end
        refreshTotals(job)
        safeWriteJob(jobPath, job)
        LrTasks.yield()
    end

    local collectionName = source.collection
    if not collectionName or collectionName == '' then collectionName = LrPathUtils.leafName(source.path or '') end
    if (job.request or {}).create_collections ~= false and #photosForCollection > 0 then
        local collection, collectionErr = ensureCollection(catalog, collectionName)
        if collection then
            local ok, reason = withWriteRetry(catalog, 'LRAutomatic: adicionar à coleção', function()
                collection:addPhotos(photosForCollection)
            end, collectionName)
            if not ok then progress.error = 'Fotos importadas, mas coleção falhou: ' .. tostring(reason) end
        else
            progress.error = 'Fotos importadas, mas coleção não foi criada: ' .. tostring(collectionErr)
        end
    end

    progress.status = (progress.failed or 0) > 0 and 'partial' or 'completed'
    refreshTotals(job)
    safeWriteJob(jobPath, job)
    return progress.status ~= 'completed'
end

local function processJob(jobPath, job)
    if type(job) ~= 'table' or tostring(job.status) ~= 'queued' then processing = false; return false end
    job.request = type(job.request) == 'table' and job.request or {}
    job.progress = type(job.progress) == 'table' and job.progress or {}

    local catalog = LrApplication.activeCatalog()
    if not catalog then
        job.status = 'failed'
        job.error = 'nenhum catálogo ativo no Lightroom'
        job.finished_at = timestamp()
        safeWriteJob(jobPath, job)
        processing = false
        return false
    end

    job.active_catalog_path = catalog:getPath()
    job.status, job.error, job.started_at = 'running', nil, timestamp()
    safeWriteJob(jobPath, job)

    local allowed = allowedExtensionTable(job.request)
    local anyFailed = false
    local importedPhotos = {}
    local sources = type(job.request.sources) == 'table' and job.request.sources or {}

    if #sources == 0 then
        job.error = 'job sem pastas de origem'
        anyFailed = true
    end

    for index, source in ipairs(sources) do
        local progress = job.progress[index]
        if type(progress) ~= 'table' then
            progress = { status='queued', discovered=0, imported=0, skipped=0, failed=0 }
            job.progress[index] = progress
        end
        if processSource(catalog, job, source, progress, jobPath, importedPhotos, allowed) then anyFailed = true end
    end

    local presetOk = applyPreset(catalog, importedPhotos, job)
    safeWriteJob(jobPath, job)
    local smartOk = buildSmartPreviews(catalog, importedPhotos, job)
    safeWriteJob(jobPath, job)

    startStandardPreviews(importedPhotos, jobPath, job, anyFailed or not presetOk or not smartOk)
    return true
end

function Runner.processQueuedOnce()
    if processing then return 0 end
    LrFileUtils.createAllDirectories(jobsDir())
    writeState('runner_alive.txt', timestamp() .. '\njobs=' .. jobsDir())

    local inspected = 0
    for path in LrFileUtils.files(jobsDir()) do
        if isJobFile(path) then
            inspected = inspected + 1
            local job, readError = readJson(path)
            if not job then
                plainLog('JSON_INVALID path=' .. tostring(path) .. ' error=' .. tostring(readError))
            elseif tostring(job.status) == 'queued' then
                processing = true
                writeState('last_scan.txt', timestamp() .. '\ninspected=' .. inspected .. '\nprocessed=1')
                processJob(path, job)
                return 1
            end
        end
    end

    writeState('last_scan.txt', timestamp() .. '\ninspected=' .. inspected .. '\nprocessed=0')
    return 0
end

function Runner.runLoop(shouldStop)
    LrFileUtils.createAllDirectories(jobsDir())
    plainLog('Plugin V4.4 iniciado; proteção por pasta e arquivo; monitorando ' .. jobsDir())
    while not shouldStop() do
        writeState('heartbeat.txt', timestamp() .. '\nloop=running\nprocessing=' .. tostring(processing) .. '\njobs=' .. jobsDir())
        Runner.processQueuedOnce()
        LrTasks.sleep(2)
    end
    processing = false
    plainLog('Plugin V4.4 loop encerrado')
end

function Runner.getJobsDir() return jobsDir() end
return Runner
