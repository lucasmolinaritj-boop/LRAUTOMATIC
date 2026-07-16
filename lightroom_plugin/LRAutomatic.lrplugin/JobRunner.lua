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

local function hardTrace(message)
    LrFileUtils.createAllDirectories(dataDir())
    appendText(LrPathUtils.child(dataDir(), 'runner-trace.log'), timestamp() .. ' ' .. tostring(message) .. '\n')
end

local function plainLog(message)
    hardTrace(message)
    LrFileUtils.createAllDirectories(logsDir())
    appendText(LrPathUtils.child(logsDir(), 'plugin.log'), timestamp() .. ' ' .. tostring(message) .. '\n')
    pcall(function() logger:info(tostring(message)) end)
end

local function writeState(name, text)
    LrFileUtils.createAllDirectories(stateDir())
    local ok, err = writeText(LrPathUtils.child(stateDir(), name), tostring(text or ''))
    if not ok then hardTrace('state_write_failed ' .. tostring(name) .. ': ' .. tostring(err)) end
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
    content = stripBom(content)
    local ok, decoded = pcall(Json.decode, content)
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

local function findCollection(catalog, name)
    for _, collection in ipairs(catalog:getChildCollections()) do
        if collection:getName() == name then return collection end
    end
    return nil
end

local function ensureCollection(catalog, name)
    if not name or name == '' then return nil end
    local existing = findCollection(catalog, name)
    if existing then return existing end
    plainLog('WRITE_WAIT_BEGIN action=create_collection name=' .. tostring(name))
    catalog:withWriteAccessDo('LRAutomatic: criar coleção', function()
        catalog:createCollection(name, nil, true)
    end, { timeout = 30 })
    plainLog('WRITE_WAIT_END action=create_collection name=' .. tostring(name))
    return findCollection(catalog, name)
end

local function importOne(catalog, photoPath)
    plainLog('ADD_PHOTO_BEGIN path=' .. tostring(photoPath))
    local before = catalog:findPhotoByPath(photoPath)
    if before then
        plainLog('ADD_PHOTO_SKIP_ALREADY_EXISTS path=' .. tostring(photoPath) .. ' photo=' .. tostring(before))
        return before, 'skipped'
    end

    local importedPhoto = nil
    plainLog('WRITE_WAIT_BEGIN action=import_photo path=' .. tostring(photoPath))
    catalog:withWriteAccessDo('LRAutomatic: importar foto', function()
        importedPhoto = catalog:addPhoto(photoPath)
    end, { timeout = 30 })
    plainLog('WRITE_WAIT_END action=import_photo path=' .. tostring(photoPath))

    local after = importedPhoto or catalog:findPhotoByPath(photoPath)
    plainLog('ADD_PHOTO_END path=' .. tostring(photoPath) .. ' returned=' .. tostring(importedPhoto) .. ' found_after=' .. tostring(after))
    if after then return after, 'imported' end
    return nil, 'failed'
end

local function processSource(catalog, job, source, progress, jobPath)
    progress.status = 'running'
    job.current_source = source.path
    local recursive = source.recursive
    if recursive == nil then recursive = job.request.recursive == true end
    local files = collectFiles(source.path, recursive)
    progress.discovered = #files
    refreshTotals(job)
    writeJson(jobPath, job)
    plainLog('Encontradas ' .. tostring(#files) .. ' fotos em ' .. tostring(source.path))

    local addedPhotos = {}
    for _, photoPath in ipairs(files) do
        local photo, result = importOne(catalog, photoPath)
        if result == 'imported' then
            progress.imported = progress.imported + 1
            table.insert(addedPhotos, photo)
        elseif result == 'skipped' then
            progress.skipped = progress.skipped + 1
            table.insert(addedPhotos, photo)
        else
            progress.failed = progress.failed + 1
            progress.error = 'A foto não apareceu no catálogo após addPhoto: ' .. tostring(photoPath)
        end
        refreshTotals(job)
        writeJson(jobPath, job)
    end

    local collectionName = source.collection
    if not collectionName or collectionName == '' then collectionName = LrPathUtils.leafName(source.path) end
    if job.request.create_collections ~= false and #addedPhotos > 0 then
        local collection = ensureCollection(catalog, collectionName)
        if collection then
            plainLog('WRITE_WAIT_BEGIN action=collection_add name=' .. tostring(collectionName))
            catalog:withWriteAccessDo('LRAutomatic: adicionar à coleção', function()
                collection:addPhotos(addedPhotos)
            end, { timeout = 30 })
            plainLog('WRITE_WAIT_END action=collection_add name=' .. tostring(collectionName))
            plainLog('COLLECTION_ADD name=' .. tostring(collectionName) .. ' count=' .. tostring(#addedPhotos))
        else
            progress.failed = progress.failed + #addedPhotos
            progress.error = 'Não foi possível criar ou localizar a coleção ' .. tostring(collectionName)
        end
    end

    local accounted = (progress.imported or 0) + (progress.skipped or 0) + (progress.failed or 0)
    if accounted ~= (progress.discovered or 0) then
        progress.failed = (progress.failed or 0) + math.abs((progress.discovered or 0) - accounted)
        progress.error = 'Totais inconsistentes: discovered=' .. tostring(progress.discovered) .. ' accounted=' .. tostring(accounted)
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
    plainLog('Iniciando job ' .. tostring(job.job_id or jobPath) .. ' no catálogo ' .. tostring(job.active_catalog_path))

    local anyFailed = false
    for index, source in ipairs((job.request and job.request.sources) or {}) do
        local progress = job.progress and job.progress[index]
        if progress then
            processSource(catalog, job, source, progress, jobPath)
            if progress.status == 'failed' then anyFailed = true end
        end
    end

    refreshTotals(job)
    job.current_source = nil
    if anyFailed and job.total_imported > 0 then job.status = 'partial'
    elseif anyFailed then job.status = 'failed'
    elseif job.total_discovered ~= (job.total_imported + job.total_skipped + job.total_failed) then
        job.status = 'failed'
        job.error = 'Totais finais inconsistentes'
    else job.status = 'completed' end
    writeJson(jobPath, job)
    plainLog('Job finalizado: ' .. tostring(job.job_id) .. ' status=' .. tostring(job.status) .. ' discovered=' .. tostring(job.total_discovered) .. ' imported=' .. tostring(job.total_imported) .. ' skipped=' .. tostring(job.total_skipped) .. ' failed=' .. tostring(job.total_failed))
    return true
end

function Runner.processQueuedOnce()
    if processing then
        plainLog('Fila já está sendo processada; chamada concorrente ignorada')
        return 0
    end

    processing = true
    LrFileUtils.createAllDirectories(jobsDir())
    writeState('runner_alive.txt', timestamp() .. '\njobs=' .. jobsDir())
    local processed, inspected = 0, 0

    for path in LrFileUtils.files(jobsDir()) do
        if isJobFile(path) then
            inspected = inspected + 1
            local job, readError = readJson(path)
            if not job then
                plainLog('JSON inválido em ' .. path .. ': ' .. tostring(readError))
            elseif tostring(job.status) == 'queued' then
                processJob(path, job)
                processed = processed + 1
            end
        end
    end

    writeState('last_scan.txt', timestamp() .. '\ninspected=' .. tostring(inspected) .. '\nprocessed=' .. tostring(processed))
    processing = false
    return processed
end

function Runner.runLoop(shouldStop)
    LrFileUtils.createAllDirectories(jobsDir())
    plainLog('Plugin V3.3 Write Wait iniciado; monitorando ' .. jobsDir())
    while not shouldStop() do
        writeState('heartbeat.txt', timestamp() .. '\nloop=running\njobs=' .. jobsDir())
        Runner.processQueuedOnce()
        LrTasks.sleep(2)
    end
end

function Runner.getJobsDir() return jobsDir() end
return Runner