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

local function findOrCreateCollection(catalog, name, setName)
    if not name or name == '' then return nil end
    local parent = nil
    if setName and setName ~= '' then
        for _, set in ipairs(catalog:getChildCollectionSets()) do
            if set:getName() == setName then parent = set break end
        end
        if not parent then parent = catalog:createCollectionSet(setName, nil, true) end
    end
    local collections = parent and parent:getChildCollections() or catalog:getChildCollections()
    for _, collection in ipairs(collections) do
        if collection:getName() == name then return collection end
    end
    return catalog:createCollection(name, parent, true)
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

local function processSource(catalog, job, source, progress, jobPath)
    progress.status = 'running'
    job.current_source = source.path
    writeJson(jobPath, job)

    local recursive = source.recursive
    if recursive == nil then recursive = job.request.recursive == true end
    local files = collectFiles(source.path, recursive)
    progress.discovered = #files
    refreshTotals(job)
    writeJson(jobPath, job)
    plainLog('Encontradas ' .. tostring(#files) .. ' fotos em ' .. tostring(source.path))

    local collectionName = source.collection
    if not collectionName or collectionName == '' then collectionName = LrPathUtils.leafName(source.path) end

    local writeOk, writeErr = pcall(function()
        catalog:withWriteAccessDo('LRAutomatic: importar pasta', function()
            local collection = nil
            if job.request.create_collections ~= false then
                collection = findOrCreateCollection(catalog, collectionName, job.request.collection_set)
            end

            for _, photoPath in ipairs(files) do
                local existing = catalog:findPhotoByPath(photoPath)
                if existing then
                    progress.skipped = progress.skipped + 1
                else
                    local importedPhoto = catalog:addPhoto(photoPath)
                    if importedPhoto then
                        progress.imported = progress.imported + 1
                        if collection then collection:addPhotos({ importedPhoto }) end
                    else
                        progress.failed = progress.failed + 1
                        progress.error = 'catalog:addPhoto retornou nil para ' .. tostring(photoPath)
                    end
                end
            end
        end, { timeout = 30 })
    end)

    if not writeOk then
        progress.status = 'failed'
        progress.error = tostring(writeErr)
        refreshTotals(job)
        writeJson(jobPath, job)
        error(writeErr)
    end

    refreshTotals(job)
    progress.status = progress.failed > 0 and 'failed' or 'completed'
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
            local ok, err = pcall(processSource, catalog, job, source, progress, jobPath)
            if not ok then
                progress.status, progress.error = 'failed', tostring(err)
                anyFailed = true
                plainLog('Erro na origem ' .. tostring(source.path) .. ': ' .. tostring(err))
                writeJson(jobPath, job)
            elseif progress.status == 'failed' then
                anyFailed = true
            end
        end
    end

    refreshTotals(job)
    job.current_source = nil
    if anyFailed and job.total_imported > 0 then job.status = 'partial'
    elseif anyFailed then job.status = 'failed'
    else job.status = 'completed' end
    writeJson(jobPath, job)
    plainLog('Job finalizado: ' .. tostring(job.job_id) .. ' status=' .. tostring(job.status))
    return true
end

function Runner.processQueuedOnce()
    if processing then
        plainLog('Fila já está sendo processada; chamada concorrente ignorada')
        return 0
    end

    processing = true
    local okOuter, resultOrError = pcall(function()
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
                    local ok, result = pcall(processJob, path, job)
                    if not ok then
                        job.status, job.error = 'failed', tostring(result)
                        pcall(writeJson, path, job)
                        plainLog('Job falhou: ' .. tostring(result))
                    elseif result then
                        processed = processed + 1
                    end
                end
            end
        end

        writeState('last_scan.txt', timestamp() .. '\ninspected=' .. tostring(inspected) .. '\nprocessed=' .. tostring(processed))
        return processed
    end)
    processing = false

    if not okOuter then error(resultOrError) end
    return resultOrError
end

function Runner.runLoop(shouldStop)
    LrFileUtils.createAllDirectories(jobsDir())
    plainLog('Plugin V3.0 iniciado; monitorando ' .. jobsDir())
    while not shouldStop() do
        writeState('heartbeat.txt', timestamp() .. '\nloop=running\njobs=' .. jobsDir())
        local ok, err = pcall(Runner.processQueuedOnce)
        if not ok then plainLog('Erro ao verificar fila: ' .. tostring(err)) end
        LrTasks.sleep(2)
    end
end

function Runner.getJobsDir() return jobsDir() end
return Runner
