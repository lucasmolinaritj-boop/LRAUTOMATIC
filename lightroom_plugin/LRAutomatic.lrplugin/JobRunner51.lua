-- Compatibilidade LR 10.4 + política estrita para fotos já importadas.
-- O JobRunner48 é carregado com pequenas correções em memória para manter o
-- arquivo-base estável e impedir definitivamente preset em fotos ignoradas.
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local originalImport = import
local originalTasks = originalImport 'LrTasks'

local compatibleTasks = setmetatable({
    currentTime = function() return os.clock() end,
}, { __index = originalTasks })

local function replaceOnce(source, old, new, label)
    local first, last = string.find(source, old, 1, true)
    if not first then error('JobRunner51: trecho não encontrado: ' .. label) end
    if string.find(source, old, last + 1, true) then
        error('JobRunner51: trecho duplicado inesperadamente: ' .. label)
    end
    return string.sub(source, 1, first - 1) .. new .. string.sub(source, last + 1)
end

local basePath = LrPathUtils.child(_PLUGIN.path, 'JobRunner48.lua')
local file = io.open(basePath, 'rb')
if not file then error('JobRunner51: não foi possível abrir JobRunner48.lua') end
local source = file:read('*a')
file:close()

-- Estado persistente: somente previews que realmente falharam entram novamente
-- quando uma foto já existente aparecer em outro job.
source = replaceOnce(source,
    "local function claimPath(id) return LrPathUtils.child(claimsDir(), 'claim_' .. id .. '.json') end",
    "local function claimPath(id) return LrPathUtils.child(claimsDir(), 'claim_' .. id .. '.json') end\n" ..
    "local function previewRetryPath() return LrPathUtils.child(stateDir(), 'preview_retry.json') end\n" ..
    "local previewRetry = { smart = {}, standard = {} }\n" ..
    "local function loadPreviewRetry()\n" ..
    "    local value = readJson(previewRetryPath())\n" ..
    "    if type(value) == 'table' then\n" ..
    "        value.smart = type(value.smart) == 'table' and value.smart or {}\n" ..
    "        value.standard = type(value.standard) == 'table' and value.standard or {}\n" ..
    "        previewRetry = value\n" ..
    "    else previewRetry = { smart = {}, standard = {} } end\n" ..
    "end\n" ..
    "local function savePreviewRetry()\n" ..
    "    LrFileUtils.createAllDirectories(stateDir())\n" ..
    "    writeJsonAtomic(previewRetryPath(), previewRetry)\n" ..
    "end\n" ..
    "local function photoPath(photo)\n" ..
    "    if not photo then return nil end\n" ..
    "    local ok, path = pcall(function() return photo:getRawMetadata('path') end)\n" ..
    "    return ok and path or nil\n" ..
    "end",
    'helpers de preview pendente')

-- Fotos ignoradas continuam disponíveis para coleção, mas nunca entram na lista
-- que recebe preset. Elas só entram nas filas de preview se houver falha registrada.
source = replaceOnce(source,
    "local function processSource(catalog,job,source,progress,jobPath,importedPhotos,allowed)",
    "local function processSource(catalog,job,source,progress,jobPath,importedPhotos,smartPhotos,standardPhotos,allowed)",
    'assinatura processSource')

source = replaceOnce(source,
    "if result=='cancelled' then return true elseif result=='imported' then progress.imported=progress.imported+1; table.insert(photosForCollection,photo); table.insert(importedPhotos,photo) elseif result=='skipped' then progress.skipped=progress.skipped+1; table.insert(photosForCollection,photo); table.insert(importedPhotos,photo) else progress.failed=progress.failed+1; progress.error=tostring(err)..': '..tostring(path) end",
    "if result=='cancelled' then return true elseif result=='imported' then progress.imported=progress.imported+1; table.insert(photosForCollection,photo); table.insert(importedPhotos,photo); table.insert(smartPhotos,photo); table.insert(standardPhotos,photo) elseif result=='skipped' then progress.skipped=progress.skipped+1; table.insert(photosForCollection,photo); if previewRetry.smart[path] then table.insert(smartPhotos,photo) end; if previewRetry.standard[path] then table.insert(standardPhotos,photo) end else progress.failed=progress.failed+1; progress.error=tostring(err)..': '..tostring(path) end",
    'separação importadas/ignoradas')

source = replaceOnce(source,
    "job.request=type(job.request)=='table' and job.request or {}; job.progress=type(job.progress)=='table' and job.progress or {}",
    "job.request=type(job.request)=='table' and job.request or {}; job.progress=type(job.progress)=='table' and job.progress or {}; loadPreviewRetry()",
    'carregar pendências')

source = replaceOnce(source,
    "local importedPhotos={}; local failed=false; local sources=type(job.request.sources)=='table' and job.request.sources or {}; local allowed=allowedExtensionTable(job.request)",
    "local importedPhotos={}; local smartPhotos={}; local standardPhotos={}; local failed=false; local sources=type(job.request.sources)=='table' and job.request.sources or {}; local allowed=allowedExtensionTable(job.request)",
    'listas separadas')

source = replaceOnce(source,
    "if processSource(catalog,job,source,progress,jobPath,importedPhotos,allowed) then failed=true end",
    "if processSource(catalog,job,source,progress,jobPath,importedPhotos,smartPhotos,standardPhotos,allowed) then failed=true end",
    'chamada processSource')

source = replaceOnce(source,
    "local smartOk=buildSmartPreviewsWithRetry(catalog,importedPhotos,job,jobPath); safeWriteJob(jobPath,job)",
    "local smartOk=buildSmartPreviewsWithRetry(catalog,smartPhotos,job,jobPath); safeWriteJob(jobPath,job)",
    'fila smart preview')

source = replaceOnce(source,
    "local standardOk=standardPreviewsSerial(importedPhotos,jobPath,job)",
    "local standardOk=standardPreviewsSerial(standardPhotos,jobPath,job)",
    'fila preview padrão')

-- Smart Preview: limpa a pendência quando criado/existente e grava apenas os que
-- efetivamente falharam após todas as tentativas.
source = replaceOnce(source,
    "local pending=photos; local createdTotal,existedTotal=0,0",
    "local pending=photos; local inputPhotos=photos; local createdTotal,existedTotal=0,0",
    'entrada smart preview')

source = replaceOnce(source,
    "if #pending==0 then job.smart_previews_status='completed'; return true end",
    "if #pending==0 then for _,p in ipairs(inputPhotos) do local path=photoPath(p); if path then previewRetry.smart[path]=nil end end; savePreviewRetry(); job.smart_previews_status='completed'; return true end",
    'sucesso smart preview')

source = replaceOnce(source,
    "job.smart_previews_status='failed_after_retries'; job.smart_previews_failed=#pending\n    return false",
    "job.smart_previews_status='failed_after_retries'; job.smart_previews_failed=#pending; for _,p in ipairs(pending) do local path=photoPath(p); if path then previewRetry.smart[path]=true end end; savePreviewRetry()\n    return false",
    'falha smart preview')

-- Preview padrão: registra ou remove a pendência individualmente.
source = replaceOnce(source,
    "if success then job.standard_previews_created=job.standard_previews_created+1 else job.standard_previews_failed=job.standard_previews_failed+1; plainLog('STANDARD_PREVIEW_GAVE_UP photo='..index..' error='..tostring(lastError)) end",
    "local currentPath=photoPath(photo); if success then job.standard_previews_created=job.standard_previews_created+1; if currentPath then previewRetry.standard[currentPath]=nil end else job.standard_previews_failed=job.standard_previews_failed+1; if currentPath then previewRetry.standard[currentPath]=true end; plainLog('STANDARD_PREVIEW_GAVE_UP photo='..index..' error='..tostring(lastError)) end; savePreviewRetry()",
    'controle preview padrão')

_G.import = function(moduleName)
    if moduleName == 'LrTasks' then return compatibleTasks end
    return originalImport(moduleName)
end

local chunk, loadError = loadstring(source, '@JobRunner48-patched-by-JobRunner51')
if not chunk then _G.import = originalImport; error(loadError) end
local ok, runnerOrError = pcall(chunk)
_G.import = originalImport
if not ok then error(runnerOrError) end
return runnerOrError
