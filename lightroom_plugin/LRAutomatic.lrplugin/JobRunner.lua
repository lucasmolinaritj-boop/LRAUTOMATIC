local LrApplication = import 'LrApplication'
local LrFileUtils = import 'LrFileUtils'
local LrJson = import 'LrJson'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'

local Runner = {}
local SUPPORTED = {
    arw=true, cr2=true, cr3=true, dng=true, heic=true, heif=true,
    jpeg=true, jpg=true, nef=true, orf=true, raf=true, rw2=true,
    tif=true, tiff=true,
}

local function dataDir()
    local base = os.getenv('LOCALAPPDATA') or LrPathUtils.getStandardFilePath('appData')
    return LrPathUtils.child(base, 'LRAutomatic')
end
local function jobsDir() return LrPathUtils.child(dataDir(), 'jobs') end
local function logsDir() return LrPathUtils.child(dataDir(), 'logs') end
local function log(message)
    LrFileUtils.createAllDirectories(logsDir())
    local path = LrPathUtils.child(logsDir(), 'plugin.log')
    local old = LrFileUtils.readFile(path) or ''
    LrFileUtils.writeFile(path, old .. os.date('!%Y-%m-%dT%H:%M:%SZ') .. ' ' .. tostring(message) .. '\n')
end

local function readJson(path)
    local content = LrFileUtils.readFile(path)
    if not content then return nil end
    local ok, decoded = pcall(LrJson.decode, content)
    return ok and decoded or nil
end

local function writeJson(path, value)
    local temp = path .. '.tmp'
    LrFileUtils.writeFile(temp, LrJson.encode(value))
    if LrFileUtils.exists(path) then LrFileUtils.delete(path) end
    LrFileUtils.move(temp, path)
end

local function extension(path) return string.lower(LrPathUtils.extension(path) or '') end
local function collectFiles(folder, recursive)
    local result = {}
    local iterator = recursive and LrFileUtils.recursiveFiles(folder) or LrFileUtils.files(folder)
    for path in iterator do if SUPPORTED[extension(path)] then table.insert(result, path) end end
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
    for _, collection in ipairs(collections) do if collection:getName() == name then return collection end end
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

local function externallyCancelled(jobPath)
    local latest = readJson(jobPath)
    return latest and latest.status == 'cancelled'
end

local function quoteWindows(value)
    return '"' .. string.gsub(value, '"', '\\"') .. '"'
end

local function requestSmartPreviews(catalog, photos, progress)
    if not photos or #photos == 0 then return true end
    local selectedOk, selectedErr = pcall(function()
        catalog:setSelectedPhotos(photos[1], photos)
    end)
    if not selectedOk then
        progress.smart_previews_status = 'selection_failed'
        progress.smart_previews_error = tostring(selectedErr)
        log('Falha ao selecionar fotos para Smart Preview: ' .. tostring(selectedErr))
        return false
    end

    local script = LrPathUtils.child(_PLUGIN.path, 'BuildSmartPreviews.ps1')
    local command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ' .. quoteWindows(script)
    log('Executando Smart Preview: ' .. command)
    local exitCode = LrTasks.execute(command)
    progress.smart_previews_exit_code = exitCode
    if exitCode == 0 then
        progress.smart_previews_status = 'requested'
        return true
    end
    progress.smart_previews_status = 'failed'
    progress.smart_previews_error = 'PowerShell retornou código ' .. tostring(exitCode)
    return false
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

    local collectionName = source.collection
    if not collectionName or collectionName == '' then collectionName = LrPathUtils.leafName(source.path) end
    local collection = nil
    if job.request.create_collections ~= false then
        catalog:withWriteAccessDo('LRAutomatic: preparar coleção', function()
            collection = findOrCreateCollection(catalog, collectionName, job.request.collection_set)
        end)
    end

    local importedPhotos = {}
    for _, photoPath in ipairs(files) do
        if externallyCancelled(jobPath) then
            job.status, progress.status = 'cancelled', 'cancelled'
            writeJson(jobPath, job)
            return
        end

        local existing = catalog:findPhotoByPath(photoPath)
        if existing then
            progress.skipped = progress.skipped + 1
        else
            local importedPhoto = nil
            local ok, err = pcall(function()
                catalog:withWriteAccessDo('LRAutomatic: importar foto', function()
                    importedPhoto = catalog:addPhoto(photoPath)
                    if collection and importedPhoto then collection:addPhotos({ importedPhoto }) end
                    if importedPhoto and source.keywords then
                        for _, keywordName in ipairs(source.keywords) do
                            local keyword = catalog:createKeyword(keywordName, {}, true, nil, true)
                            importedPhoto:addKeyword(keyword)
                        end
                    end
                end)
            end)
            if ok and importedPhoto then
                progress.imported = progress.imported + 1
                table.insert(importedPhotos, importedPhoto)
            else
                progress.failed = progress.failed + 1
                progress.error = tostring(err or 'Falha desconhecida ao importar')
                log('Falha ao importar ' .. photoPath .. ': ' .. progress.error)
            end
        end
        refreshTotals(job)
        writeJson(jobPath, job)
        LrTasks.yield()
    end

    if job.request.build_smart_previews == true and #importedPhotos > 0 then
        requestSmartPreviews(catalog, importedPhotos, progress)
        writeJson(jobPath, job)
    end
    progress.status = progress.failed > 0 and 'failed' or 'completed'
end

local function processJob(jobPath, job)
    if job.status ~= 'queued' then return end
    local catalog = LrApplication.activeCatalog()
    job.status, job.error = 'running', nil
    writeJson(jobPath, job)
    log('Iniciando job ' .. tostring(job.id or jobPath))
    local anyFailed = false

    for index, source in ipairs(job.request.sources or {}) do
        local progress = job.progress[index]
        if progress then
            local ok, err = pcall(processSource, catalog, job, source, progress, jobPath)
            if not ok then
                progress.status, progress.error = 'failed', tostring(err)
                anyFailed = true
                log('Erro no source ' .. tostring(source.path) .. ': ' .. tostring(err))
                writeJson(jobPath, job)
            elseif progress.status == 'failed' then anyFailed = true end
        end
        if job.status == 'cancelled' then break end
    end

    refreshTotals(job)
    job.current_source = nil
    if job.status ~= 'cancelled' then
        if anyFailed and job.total_imported > 0 then job.status = 'partial'
        elseif anyFailed then job.status = 'failed'
        else job.status = 'completed' end
    end
    writeJson(jobPath, job)
    log('Finalizando job com status ' .. tostring(job.status))
end

function Runner.runLoop(shouldStop)
    LrFileUtils.createAllDirectories(jobsDir())
    LrFileUtils.createAllDirectories(logsDir())
    log('Plugin iniciado')
    while not shouldStop() do
        for path in LrFileUtils.files(jobsDir()) do
            if extension(path) == 'json' and string.match(LrPathUtils.leafName(path), '^job_') then
                local job = readJson(path)
                if job and job.status == 'queued' then
                    local ok, err = pcall(processJob, path, job)
                    if not ok then
                        job.status, job.error = 'failed', tostring(err)
                        writeJson(path, job)
                        log('Erro fatal no job: ' .. tostring(err))
                    end
                end
            end
        end
        LrTasks.sleep(2)
    end
    log('Plugin encerrado')
end

return Runner
