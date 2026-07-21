local LrApplication = import 'LrApplication'
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local Json = require 'Json'

local Organizer = {}
local ORGANIZATION_VERSION = 2

local function homePath()
    local home = LrPathUtils.getStandardFilePath('home')
    if home and home ~= '' then return home end
    return 'C:\\Users\\Public'
end

local function dataDir()
    return LrPathUtils.child(LrPathUtils.child(LrPathUtils.child(homePath(), 'AppData'), 'Local'), 'LRAutomatic')
end

local function jobsDir()
    return LrPathUtils.child(dataDir(), 'jobs')
end

local function timestamp()
    local ok, value = pcall(os.date, '!%Y-%m-%dT%H:%M:%SZ')
    return (ok and value) or 'time-unavailable'
end

local function stripBom(content)
    if string.byte(content, 1) == 239 and string.byte(content, 2) == 187 and string.byte(content, 3) == 191 then
        return string.sub(content, 4)
    end
    return content
end

local function readJson(path)
    local file = io.open(path, 'rb')
    if not file then return nil end
    local content = file:read('*a')
    file:close()
    local ok, decoded = pcall(Json.decode, stripBom(content))
    if not ok or type(decoded) ~= 'table' then return nil end
    return decoded
end

local function writeJson(path, value)
    local ok, encoded = pcall(Json.encode, value)
    if not ok then return false end
    local temp = path .. '.collections.tmp'
    local file = io.open(temp, 'wb')
    if not file then return false end
    file:write(encoded)
    file:close()
    if LrFileUtils.exists(path) then LrFileUtils.delete(path) end
    return LrFileUtils.move(temp, path) == true
end

local function isJobFile(path)
    local name = string.lower(LrPathUtils.leafName(path) or tostring(path))
    return string.sub(name, 1, 4) == 'job_' and string.sub(name, -5) == '.json'
end

local function cleanName(value, fallback)
    local text = tostring(value or '')
    text = string.gsub(text, '[\r\n\t]+', ' ')
    text = string.gsub(text, '%s+', ' ')
    text = string.gsub(text, '^%s+', '')
    text = string.gsub(text, '%s+$', '')
    text = string.gsub(text, '^%-+', '')
    text = string.gsub(text, '%-+$', '')
    text = string.gsub(text, '^%s+', '')
    text = string.gsub(text, '%s+$', '')
    if text == '' then return fallback end
    return text
end

local function optionalName(value)
    local text = cleanName(value, '')
    if text == '' then return nil end
    return text
end

local function appendEvent(job, stage, title, detail, level)
    job.events = job.events or {}
    table.insert(job.events, {
        at = timestamp(),
        stage = stage,
        title = title,
        detail = tostring(detail or ''),
        level = level or 'info',
    })
end

local function withWrite(catalog, actionName, fn)
    local ran = false
    local timedOut = false
    local status = catalog:withWriteAccessDo(actionName, function()
        ran = true
        fn()
    end, {
        timeout = 30,
        callback = function() timedOut = true end,
    })
    return ran and not timedOut and (status == nil or status == 'executed')
end

local function findCollectionSet(catalog, name)
    for _, set in ipairs(catalog:getChildCollectionSets() or {}) do
        if set:getName() == name then return set end
    end
    return nil
end

local function ensureCollectionSet(catalog, name)
    local existing = findCollectionSet(catalog, name)
    if existing then return existing, false end
    local created = nil
    local ok = withWrite(catalog, 'LRAutomatic: criar conjunto ' .. name, function()
        created = catalog:createCollectionSet(name, nil, true)
    end)
    if not ok then return nil, false end
    return created or findCollectionSet(catalog, name), true
end

local function findCollection(parent, name)
    for _, collection in ipairs(parent:getChildCollections() or {}) do
        if collection:getName() == name then return collection end
    end
    return nil
end

local function ensureCollection(catalog, parent, name)
    local existing = findCollection(parent, name)
    if existing then return existing, false end
    local created = nil
    local ok = withWrite(catalog, 'LRAutomatic: criar coleção ' .. name, function()
        created = catalog:createCollection(name, parent, true)
    end)
    if not ok then return nil, false end
    return created or findCollection(parent, name), true
end

local function normalizedExtension(path)
    local ext = string.lower(LrPathUtils.extension(path) or '')
    if string.sub(ext, 1, 1) == '.' then ext = string.sub(ext, 2) end
    return ext
end

local function allowedExtensions(request)
    local result = {}
    for _, value in ipairs((request or {}).allowed_extensions or {}) do
        local ext = string.lower(tostring(value or ''))
        if string.sub(ext, 1, 1) == '.' then ext = string.sub(ext, 2) end
        if ext ~= '' then result[ext] = true end
    end
    if next(result) == nil then
        result.cr2, result.cr3, result.dng = true, true, true
    end
    return result
end

local function collectPhotos(catalog, source, request)
    local photos = {}
    local folder = source.path
    if not folder or not LrFileUtils.exists(folder) then return photos end
    local recursive = source.recursive
    if recursive == nil then recursive = request.recursive == true end
    local iterator = recursive and LrFileUtils.recursiveFiles(folder) or LrFileUtils.files(folder)
    local allowed = allowedExtensions(request)
    for path in iterator do
        if LrFileUtils.exists(path) and allowed[normalizedExtension(path)] then
            local photo = catalog:findPhotoByPath(path)
            if photo then table.insert(photos, photo) end
        end
    end
    return photos
end

local function addToTree(catalog, setName, collectionName, photos, counters)
    local set, setWasCreated = ensureCollectionSet(catalog, setName)
    if not set then
        counters.failures = counters.failures + 1
        return
    end
    if setWasCreated then counters.setsCreated = counters.setsCreated + 1 end

    local collection, collectionWasCreated = ensureCollection(catalog, set, collectionName)
    if not collection then
        counters.failures = counters.failures + 1
        return
    end
    if collectionWasCreated then counters.collectionsCreated = counters.collectionsCreated + 1 end

    if #photos > 0 then
        local ok = withWrite(catalog, 'LRAutomatic: organizar ' .. collectionName, function()
            collection:addPhotos(photos)
        end)
        if ok then
            counters.photosAdded = counters.photosAdded + #photos
        else
            counters.failures = counters.failures + 1
        end
    end
end

local function needsOrganization(job, request)
    if tostring(job.collections_run_once_token or '') ~= tostring(job.job_id or '') then
        return false
    end
    if request.organize_collections_by_photographer ~= true
        and request.organize_collections_by_client ~= true then
        return false
    end
    return tostring(job.collections_status or '') == 'requested'
end

local function organizeJob(path, job)
    local request = job.request or {}
    if not needsOrganization(job, request) then return false end

    local status = tostring(job.status or '')
    if status ~= 'completed' and status ~= 'partial' then return false end

    local catalog = LrApplication.activeCatalog()
    if not catalog then return false end
    if job.active_catalog_path and tostring(job.active_catalog_path) ~= tostring(catalog:getPath()) then
        return false
    end

    job.collections_status = 'running'
    writeJson(path, job)

    local counters = {
        setsCreated = 0,
        collectionsCreated = 0,
        photosAdded = 0,
        failures = 0,
        photographerTrees = 0,
        clientTrees = 0,
        clientSkipped = 0,
    }

    for _, source in ipairs(request.sources or {}) do
        local photos = collectPhotos(catalog, source, request)
        local workId = cleanName(source.work_id, cleanName(LrPathUtils.leafName(source.path or ''), 'Sem ID'))

        if request.organize_collections_by_photographer == true then
            local photographer = cleanName(source.photographer, 'Sem fotógrafo')
            local photographerCollection = cleanName(source.collection, workId)
            addToTree(catalog, photographer, photographerCollection, photos, counters)
            counters.photographerTrees = counters.photographerTrees + 1
        end

        if request.organize_collections_by_client == true then
            local client = optionalName(source.client)
            if client then
                addToTree(catalog, client, workId, photos, counters)
                counters.clientTrees = counters.clientTrees + 1
            else
                counters.clientSkipped = counters.clientSkipped + 1
            end
        end
    end

    job.collection_sets_created = counters.setsCreated
    job.collections_created = counters.collectionsCreated
    job.collections_status = counters.failures > 0 and 'partial' or 'completed'
    job.collections_organization_version = ORGANIZATION_VERSION
    job.collections_run_once_token = nil
    appendEvent(
        job,
        'collections',
        counters.failures > 0 and 'Coleções organizadas com ressalvas' or 'Coleções organizadas ao concluir o job',
        'Execução única; conjuntos novos: ' .. counters.setsCreated
            .. '; coleções novas: ' .. counters.collectionsCreated
            .. '; vínculos de fotos: ' .. counters.photosAdded
            .. '; árvores por fotógrafo: ' .. counters.photographerTrees
            .. '; árvores por cliente: ' .. counters.clientTrees
            .. '; clientes ausentes ignorados: ' .. counters.clientSkipped
            .. '; falhas: ' .. counters.failures .. '.',
        counters.failures > 0 and 'warning' or 'info'
    )
    writeJson(path, job)
    return true
end

function Organizer.processOnce()
    LrFileUtils.createAllDirectories(jobsDir())
    for path in LrFileUtils.files(jobsDir()) do
        if isJobFile(path) then
            local job = readJson(path)
            if job and organizeJob(path, job) then return 1 end
        end
    end
    return 0
end

return Organizer
