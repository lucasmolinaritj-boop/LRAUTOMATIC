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
local SUPPORTED = {
    arw=true, cr2=true, cr3=true, dng=true, heic=true, heif=true,
    jpeg=true, jpg=true, nef=true, orf=true, raf=true, rw2=true,
    tif=true, tiff=true,
}

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
    local temp = path .. '.tmp'
    local ok, err = writeText(temp, Json.encode(value))
    if not ok then error('não foi possível gravar ' .. temp .. ': ' .. tostring(err)) end
    if LrFileUtils.exists(path) then LrFileUtils.delete(path) end
    if not LrFileUtils.move(temp, path) then error('não foi possível substituir ' .. path) end
end

local function normalizedExtension(path)
    local ext = string.lower(LrPathUtils.extension(path) or '')
    if string.sub(ext, 1, 1) == '.' then ext = string.sub(ext, 2) end
    return ext
end

local function isJobFile(path)
    local name = string.lower(LrPathUtils.leafName(path) or tostring(path))
    return string.sub(name, 1, 4) == 'job_' and string.sub(name, -5) == '.json'
end

local function collectFiles(folder, recursive)
    if not LrFileUtils.exists(folder) then error('pasta de origem não existe: ' .. tostring(folder)) end
    local result = {}
    local iterator = recursive and LrFileUtils.recursiveFiles(folder) or LrFileUtils.files(folder)
    for path in iterator do
        if SUPPORTED[normalizedExtension(path)] then table.insert(result, path) end
    end
    table.sort(result)
    return result
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

local function withWriteRetry(catalog, actionName, fn, detail)
    for attempt = 1, 3 do
        local ran = false
        local timedOut = false
        plainLog('WRITE_BEGIN action=' .. actionName .. ' attempt=' .. attempt .. ' detail=' .. tostring(detail))
        local status = catalog:withWriteAccessDo(actionName, function(context)
            ran = true
            fn(context)
        end, {
            timeout = 10,
            callback = function() timedOut = true end,
        })
        plainLog('WRITE_END action=' .. actionName .. ' attempt=' .. attempt .. ' status=' .. tostring(status) .. ' ran=' .. tostring(ran) .. ' timeout=' .. tostring(timedOut))
        if ran and (status == nil or status == 'executed') then return true, 'executed' end
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
    local before = catalog:findPhotoByPath(photoPath)
    if before then
        plainLog('ADD_PHOTO_SKIPPED path=' .. tostring(photoPath))
        return before, 'skipped', nil
    end
    local importedPhoto = nil
    local ok, reason = withWriteRetry(catalog, 'LRAutomatic: importar foto', function()
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
    if not name and not uuid then
        job.preset_status = 'not_requested'
        return true
    end
    job.preset_status = 'running'
    local preset = findPresetByNameOrUuid(name, uuid)
    if not preset then
        job.preset_status = 'failed'
        job.error = 'Preset não encontrado: ' .. tostring(name or uuid)
        plainLog('PRESET_NOT_FOUND value=' .. tostring(name or uuid))
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
    plainLog('PRESET_APPLIED name=' .. tostring(preset:getName()) .. ' count=' .. tostring(applied))
    return true
end

local function buildSmartPreviews(catalog, photos, job)
    if not ((job.request or {}).build_smart_previews == true) then
        job.smart_previews_status = 'not_requested'
        return true
    end
    if #photos == 0 then
        job.smart_previews_status = 'completed_no_photos'
        return true
    end
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
    plainLog('SMART_PREVIEWS_END created=' .. #created .. ' existed=' .. #existed .. ' failed=' .. #failed)
    return #failed == 0
end

local function processSource(catalog, job, source, progress, jobPath, postPhotos)
    progress.status = 'running'
    job.current_source = source.path
    local recursive = source.recursive
    if recursive == nil then recursive = job.request.recursive == true end
    local files = collectFiles(source.path, recursive)
    progress.discovered = #files
    refreshTotals(job)
    writeJson(jobPath, job)
    plainLog('SOURCE_DISCOVERED path=' .. tostring(source.path) .. ' count=' .. tostring(#files))

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
        end
        refreshTotals(job)
        writeJson(jobPath, job)
    end

    local collectionName = source.collection
    if not collectionName or collectionName == '' then collectionName = LrPathUtils.leafName(source.path) end
    if job.request.create_collections ~= false and #photosForCollection > 0 then
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

    local accounted = (progress.imported or 0) + (progress.skipped or 0) + (progress.failed or 0)
    if accounted ~= (progress.discovered or 0) then
        progress.failed = (progress.failed or 0) + math.abs((progress.discovered or 0) - accounted)
        progress.error = 'Totais inconsistentes'
    end
    progress.status = (progress.failed or 0) > 0 and 'failed' or 'completed'
    refreshTotals(job)
    writeJson(jobPath, job)
end

local function processJob(jobPath, job)
    if tostring(job.status) ~= 'queued' then return false end
    local catalog = LrApplication.activeCatalog()
    if not catalog then error('nenhum catálogo ativo no Lightroom') end
    job.active_catalog_path = catalog:getPath()
    job.status, job.error = 'running', nil
    writeJson(jobPath, job)
    plainLog('JOB_BEGIN id=' .. tostring(job.job_id) .. ' catalog=' .. tostring(job.active_catalog_path))

    local anyFailed = false
    local importedPhotos = {}
    for index, source in ipairs((job.request and job.request.sources) or {}) do
        local progress = job.progress and job.progress[index]
        if progress then
            processSource(catalog, job, source, progress, jobPath, importedPhotos)
            if progress.status == 'failed' then anyFailed = true end
        end
    end

    local presetOk = applyPreset(catalog, importedPhotos, job)
    writeJson(jobPath, job)
    local previewsOk = buildSmartPreviews(catalog, importedPhotos, job)
    writeJson(jobPath, job)

    refreshTotals(job)
    job.current_source = nil
    if anyFailed or not presetOk or not previewsOk then
        job.status = job.total_imported > 0 and 'partial' or 'failed'
    else
        job.status = 'completed'
    end
    writeJson(jobPath, job)
    plainLog('JOB_END id=' .. tostring(job.job_id) .. ' status=' .. tostring(job.status) .. ' imported=' .. tostring(job.total_imported) .. ' preset=' .. tostring(job.preset_status) .. ' smart=' .. tostring(job.smart_previews_status))
    return true
end

function Runner.processQueuedOnce()
    if processing then return 0 end
    processing = true
    LrFileUtils.createAllDirectories(jobsDir())
    writeState('runner_alive.txt', timestamp() .. '\njobs=' .. jobsDir())
    local processed, inspected = 0, 0
    for path in LrFileUtils.files(jobsDir()) do
        if isJobFile(path) then
            inspected = inspected + 1
            local job, readError = readJson(path)
            if not job then
                plainLog('JSON_INVALID path=' .. path .. ' error=' .. tostring(readError))
            elseif tostring(job.status) == 'queued' then
                processJob(path, job)
                processed = processed + 1
            end
        end
    end
    writeState('last_scan.txt', timestamp() .. '\ninspected=' .. inspected .. '\nprocessed=' .. processed)
    processing = false
    return processed
end

function Runner.runLoop(shouldStop)
    LrFileUtils.createAllDirectories(jobsDir())
    plainLog('Plugin V4.0 Pipeline iniciado; monitorando ' .. jobsDir())
    while not shouldStop() do
        writeState('heartbeat.txt', timestamp() .. '\nloop=running\njobs=' .. jobsDir())
        Runner.processQueuedOnce()
        LrTasks.sleep(2)
    end
    plainLog('Plugin V4.0 loop encerrado')
end

function Runner.getJobsDir() return jobsDir() end
return Runner
