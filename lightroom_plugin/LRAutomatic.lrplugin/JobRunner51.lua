-- Compatibilidade LR 10.4 + política estrita para fotos já importadas.
-- O JobRunner48 é carregado com correções em memória para manter o arquivo-base
-- estável, impedir preset em ignoradas e tornar a persistência dos jobs resiliente.
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

-- Persistência robusta no Windows:
-- 1. escreve e valida um temporário completo;
-- 2. troca o original usando backup e rollback;
-- 3. tenta escrita direta validada quando rename/move estiver bloqueado;
-- 4. nunca apaga o último JSON válido antes de existir substituto íntegro.
source = replaceOnce(source,
[[local function writeJsonAtomic(path, value)
    local encoded = encodeJson(value)
    if not encoded then return false end
    local temp = path .. '.tmp.' .. INSTANCE_ID .. '.' .. tostring(math.random(100000,999999))
    local file = io.open(temp, 'wb')
    if not file then return false end
    file:write(encoded)
    file:close()
    for attempt=1,5 do
        if LrFileUtils.exists(path) then LrFileUtils.delete(path) end
        if LrFileUtils.move(temp, path) == true then return true end
        LrTasks.sleep(0.1 * attempt)
    end
    if LrFileUtils.exists(temp) then LrFileUtils.delete(temp) end
    return false
end]],
[[local function writeJsonAtomic(path, value)
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
end]],
    'gravação JSON atômica resiliente')

-- safeWriteJob não desiste por uma falha transitória. O runner mantém o estado em
-- memória e tenta novamente antes de registrar erro persistente.
source = replaceOnce(source,
[[local function safeWriteJob(path, job)
    if diskCancelled(path) then
        job.status = 'cancelled'
        return false
    end
    job.runner_instance_id = INSTANCE_ID
    job.runner_heartbeat_epoch = os.time()
    job.runner_heartbeat_at = timestamp()
    if not writeJsonAtomic(path, job) then
        plainLog('JOB_WRITE_FAILED path=' .. tostring(path))
        return false
    end
    return true
end]],
[[local function safeWriteJob(path, job)
    if diskCancelled(path) then
        job.status = 'cancelled'
        return false
    end
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
end]],
    'repetição segura de safeWriteJob')

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


-- Blindagem de RAWs virtuais/instáveis (Google Drive) e de exceções nativas do
-- addPhoto. A pré-leitura força a materialização do arquivo antes de entregá-lo
-- ao Lightroom. A chamada addPhoto precisa permanecer direta: no Lua 5.1 do
-- Lightroom ela pode fazer yield e, portanto, não pode ficar dentro de pcall.
source = replaceOnce(source,
[[local function importOneAttempt(catalog,path)
    if not path or path=='' then return nil,'failed','caminho vazio' end
    if not LrFileUtils.exists(path) then return nil,'failed','arquivo não encontrado' end
    local before=catalog:findPhotoByPath(path)
    if before then return before,'skipped',nil end
    local imported=nil
    local ok,reason=withWrite(catalog,'LRAutomatic: importar foto',function() imported=catalog:addPhoto(path) end,path)
    if not ok then return nil,'failed',reason end
    local after=imported or catalog:findPhotoByPath(path)
    if after then return after,'imported',nil end
    return nil,'failed','foto não apareceu no catálogo após addPhoto'
end]],
[[local function inspectRawFile(path)
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
    local before=catalog:findPhotoByPath(path)
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
    if after then return after,'imported',nil end
    return nil,'failed','foto não apareceu no catálogo após addPhoto'
end]],
    'pré-validação e proteção do addPhoto')

source = replaceOnce(source,
[[    return nil,'failed',lastError or 'falha desconhecida após 10 tentativas'
end]],
[[    local finalError=lastError or 'falha desconhecida após 10 tentativas'
    job.bad_files=type(job.bad_files)=='table' and job.bad_files or {}
    local alreadyRecorded=false
    for _,bad in ipairs(job.bad_files) do
        if type(bad)=='table' and tostring(bad.path)==tostring(path) then
            bad.error=tostring(finalError)
            bad.attempts=MAX_ATTEMPTS
            alreadyRecorded=true
            break
        end
    end
    if not alreadyRecorded then table.insert(job.bad_files,{path=path,error=tostring(finalError),attempts=MAX_ATTEMPTS,at=timestamp()}) end
    job.bad_files_count=#job.bad_files
    job.completed_with_file_errors=true
    appendJobEvent(job,'import_file_failed','RAW isolado após todas as tentativas',path..' — '..tostring(finalError)..'. A fila continuará com os demais arquivos.','error')
    safeWriteJob(jobPath,job)
    plainLog('IMPORT_FILE_ISOLATED path='..tostring(path)..' error='..tostring(finalError))
    return nil,'failed',finalError
end]],
    'isolamento definitivo do RAW sem interromper a fila')

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
