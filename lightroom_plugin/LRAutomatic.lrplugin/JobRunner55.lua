-- Otimizações de desempenho sobre o JobRunner54.
-- Mantém toda a cadeia anterior intacta e injeta cache persistente do catálogo
-- durante toda a sessão do Lightroom.
local LrPathUtils = import 'LrPathUtils'

local originalOpen = io.open
local targetPath = LrPathUtils.child(_PLUGIN.path, 'JobRunner51.lua')

local injection = [=[
-- CACHE DE CATÁLOGO DA SESSÃO: indexa todas as fotos apenas uma vez por catálogo
-- enquanto o Lightroom permanecer aberto. Jobs seguintes reutilizam o mesmo índice.
source = replaceOnce(source,
[[local function importOneAttempt(catalog,path)]],
[[local catalogPhotoIndex=nil
local catalogPhotoIndexCatalogPath=nil
local catalogPhotoIndexReadyAt=nil

local function normalizeCatalogPath(path)
    if not path then return nil end
    return string.lower((tostring(path):gsub('/','\\')))
end

local function buildCatalogPhotoIndex(catalog)
    local index={}
    if not catalog or type(catalog.getAllPhotos)~='function' then return index end
    local ok,photos=pcall(function() return catalog:getAllPhotos() end)
    if not ok or type(photos)~='table' then
        plainLog('CATALOG_INDEX_FAILED error='..tostring(photos))
        return index
    end
    for _,photo in ipairs(photos) do
        local path=nil
        local pathOk,pathValue=pcall(function() return photo:getRawMetadata('path') end)
        if pathOk then path=pathValue end
        local key=normalizeCatalogPath(path)
        if key then index[key]=photo end
    end
    catalogPhotoIndexReadyAt=os.time()
    plainLog('CATALOG_INDEX_READY count='..tostring(#photos)..' session_cache=true')
    return index
end

local function ensureCatalogPhotoIndex(catalog)
    local catalogPath=nil
    if catalog and type(catalog.getPath)=='function' then
        local ok,value=pcall(function() return catalog:getPath() end)
        if ok then catalogPath=value end
    end
    if catalogPhotoIndex==nil or catalogPhotoIndexCatalogPath~=catalogPath then
        catalogPhotoIndex=buildCatalogPhotoIndex(catalog)
        catalogPhotoIndexCatalogPath=catalogPath
    end
    return catalogPhotoIndex
end

local function importOneAttempt(catalog,path)]],
'cache persistente do catálogo na sessão')

source = replaceOnce(source,
[[    local findMethod=catalog.findPhotoByPath
    if type(findMethod)~='function' then
        plainLog('ADD_PHOTO_API_UNAVAILABLE method=findPhotoByPath catalog='..tostring(activePath)..' photo='..tostring(path))
        return nil,'failed','API findPhotoByPath indisponível'
    end
    local beforeOk,before=pcall(findMethod,catalog,path)
    if not beforeOk then
        plainLog('ADD_PHOTO_FIND_EXCEPTION phase=before photo='..tostring(path)..' error='..tostring(before))
        return nil,'failed',tostring(before)
    end
    if before then return before,'skipped',nil end]],
[[    local index=ensureCatalogPhotoIndex(catalog)
    local normalizedPath=normalizeCatalogPath(path)
    local before=normalizedPath and index[normalizedPath] or nil
    if before then return before,'skipped',nil end

    local findMethod=catalog.findPhotoByPath]],
'consulta individual substituída por cache persistente')

source = replaceOnce(source,
[[    if imported then return imported,'imported',nil end

    local afterOk,after=pcall(findMethod,catalog,path)]],
[[    if imported then
        if normalizedPath then index[normalizedPath]=imported end
        return imported,'imported',nil
    end

    if type(findMethod)~='function' then
        return nil,'failed','foto não retornada por addPhoto e API findPhotoByPath indisponível'
    end
    local afterOk,after=pcall(findMethod,catalog,path)]],
'cache atualizado após addPhoto')

source = replaceOnce(source,
[[    if after then return after,'imported',nil end]],
[[    if after then
        if normalizedPath then index[normalizedPath]=after end
        return after,'imported',nil
    end]],
'cache atualizado após confirmação')

source = replaceOnce(source,
[[local MAX_ATTEMPTS = 10
local RETRY_DELAY_SECONDS = 60]],
[[local MAX_ATTEMPTS = 10
local RETRY_DELAY_SECONDS = 60
local STANDARD_PREVIEW_MAX_ATTEMPTS = 3
local STANDARD_PREVIEW_RETRY_DELAY_SECONDS = 2]],
'parâmetros rápidos de preview padrão')

source = replaceOnce(source,
[[        for attempt=1,MAX_ATTEMPTS do]],
[[        for attempt=1,STANDARD_PREVIEW_MAX_ATTEMPTS do]],
'tentativas de preview padrão')

source = replaceOnce(source,
[[            if attempt<MAX_ATTEMPTS and not sleepInterruptible(jobPath,job,RETRY_DELAY_SECONDS) then return false end]],
[[            if attempt<STANDARD_PREVIEW_MAX_ATTEMPTS and not sleepInterruptible(jobPath,job,STANDARD_PREVIEW_RETRY_DELAY_SECONDS) then return false end]],
'espera rápida de preview padrão')

source = replaceOnce(source,
[[local currentPath=photoPath(photo); if success then job.standard_previews_created=job.standard_previews_created+1; if currentPath then previewRetry.standard[currentPath]=nil end else job.standard_previews_failed=job.standard_previews_failed+1; if currentPath then previewRetry.standard[currentPath]=true end; plainLog('STANDARD_PREVIEW_GAVE_UP photo='..index..' error='..tostring(lastError)) end; savePreviewRetry()]],
[[local currentPath=photoPath(photo); if success then job.standard_previews_created=job.standard_previews_created+1; if currentPath then previewRetry.standard[currentPath]=nil end else job.standard_previews_failed=job.standard_previews_failed+1; if currentPath then previewRetry.standard[currentPath]=true end; plainLog('STANDARD_PREVIEW_GAVE_UP photo='..index..' error='..tostring(lastError)) end]],
'persistência agrupada de preview padrão')

source = replaceOnce(source,
[[    job.standard_previews_status=job.standard_previews_failed>0 and 'partial' or 'completed'
    return job.standard_previews_failed==0]],
[[    savePreviewRetry()
    job.standard_previews_status=job.standard_previews_failed>0 and 'partial' or 'completed'
    return job.standard_previews_failed==0]],
'salvar preview padrão uma vez')

]=]

io.open = function(path, mode)
    if path == targetPath and (mode == 'rb' or mode == 'r') then
        local realFile, openError = originalOpen(path, mode)
        if not realFile then return nil, openError end
        local content = realFile:read('*a') or ''
        realFile:close()
        content = content:gsub('\r\n','\n'):gsub('\r','\n')
        local marker = "_G.import = function(moduleName)"
        local first = string.find(content,marker,1,true)
        if not first then error('JobRunner55: marcador de injeção não encontrado') end
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

local ok,runnerOrError=pcall(require,'JobRunner54')
io.open=originalOpen
if not ok then error(runnerOrError) end
return runnerOrError