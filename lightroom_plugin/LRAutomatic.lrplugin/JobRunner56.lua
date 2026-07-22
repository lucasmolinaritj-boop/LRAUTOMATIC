-- Fast-path para fotos já importadas no Lightroom Classic 10.4.
-- Evita log e persistência por foto ignorada e só reprocessa Smart Preview
-- quando houver pendência registrada.
local LrPathUtils = import 'LrPathUtils'

local originalOpen = io.open
local targetPath = LrPathUtils.child(_PLUGIN.path, 'JobRunner51.lua')

local injection = [=[
-- IGNORADAS RÁPIDAS: não reenviar toda foto existente para Smart Preview.
-- Apenas pendências reais entram novamente nas filas de preview.
source = replaceOnce(source,
    "if result=='cancelled' then return true elseif result=='imported' then progress.imported=progress.imported+1; table.insert(photosForCollection,photo); table.insert(importedPhotos,photo); table.insert(smartPhotos,photo); table.insert(standardPhotos,photo) elseif result=='skipped' then progress.skipped=progress.skipped+1; table.insert(photosForCollection,photo); if previewRetry.smart[path] then table.insert(smartPhotos,photo) end; if previewRetry.standard[path] then table.insert(standardPhotos,photo) end else progress.failed=progress.failed+1; progress.error=tostring(err)..': '..tostring(path) end",
    "if result=='cancelled' then return true elseif result=='imported' then progress.imported=progress.imported+1; table.insert(photosForCollection,photo); table.insert(importedPhotos,photo); table.insert(smartPhotos,photo); table.insert(standardPhotos,photo) elseif result=='skipped' then progress.skipped=progress.skipped+1; table.insert(photosForCollection,photo); if previewRetry.smart[path] then table.insert(smartPhotos,photo); job.smart_preview_recheck_skipped=(job.smart_preview_recheck_skipped or 0)+1 end; if previewRetry.standard[path] then table.insert(standardPhotos,photo) end else progress.failed=progress.failed+1; progress.error=tostring(err)..': '..tostring(path) end",
    'fast-path de ignoradas sem log individual')

-- Preserva auditoria: preset continua restrito às fotos realmente importadas.
source = replaceOnce(source,
    "local presetOk=applyPreset(catalog,importedPhotos,job,jobPath); safeWriteJob(jobPath,job)",
    "job.preset_candidate_count=#importedPhotos; job.preset_skipped_existing_count=job.total_skipped or 0; local presetOk=applyPreset(catalog,importedPhotos,job,jobPath); safeWriteJob(jobPath,job)",
    'auditoria de isolamento do preset')

-- Durante esperas longas, cancelamento continua sendo verificado a cada segundo,
-- porém heartbeat/claim e JSON são gravados apenas a cada 5 segundos.
source = replaceOnce(source,
    "        updateClaim(job.job_id)\n        safeWriteJob(jobPath,job)\n        LrTasks.sleep(1)",
    "        if (_ % 5)==0 or _==seconds then updateClaim(job.job_id); safeWriteJob(jobPath,job) end\n        LrTasks.sleep(1)",
    'heartbeat agrupado durante espera')

-- Reaproveita a lista descoberta em uma retomada quando todos os arquivos ainda
-- existem. Se qualquer item sumiu, faz uma varredura nova e atualiza o snapshot.
source = replaceOnce(source,
    "local function collectFiles(folder,recursive,allowed)\n    if not folder or folder=='' then return {},'pasta de origem vazia' end",
    "local function collectFiles(folder,recursive,allowed,cachedFiles)\n    if type(cachedFiles)=='table' and #cachedFiles>0 then\n        local valid=true\n        for _,cachedPath in ipairs(cachedFiles) do if not LrFileUtils.exists(cachedPath) or not allowed[normalizedExtension(cachedPath)] then valid=false; break end end\n        if valid then return cachedFiles,nil,true end\n    end\n    if not folder or folder=='' then return {},'pasta de origem vazia' end",
    'inventário persistente de origem')

source = replaceOnce(source,
    "    return result,nil\nend\n\nlocal function refreshTotals(job)",
    "    return result,nil,false\nend\n\nlocal function refreshTotals(job)",
    'retorno de cache do inventário')

source = replaceOnce(source,
    "local files,scanError=collectFiles(source.path,source.recursive==true,allowed); progress.discovered=#files; progress.status=scanError and 'failed' or 'running'; progress.error=scanError",
    "local files,scanError,reusedInventory=collectFiles(source.path,source.recursive==true,allowed,progress.discovered_files); progress.discovered=#files; progress.status=scanError and 'failed' or 'running'; progress.error=scanError; if not scanError and not reusedInventory then progress.discovered_files=files; progress.scan_completed=true; progress.scan_completed_at=timestamp() end; job.inventory_reused_count=(job.inventory_reused_count or 0)+(reusedInventory and 1 or 0)",
    'uso do inventário persistente')

-- ARQUIVOS DANIFICADOS: isola a falha no nível da foto. Arquivo inexistente,
-- zero bytes ou ilegível não chega ao addPhoto e é registrado para auditoria.
-- Falhas de importação são colocadas em blacklist somente durante o job atual,
-- evitando dez novas tentativas e novos popups para o mesmo caminho.
source = replaceOnce(source,
    "local function importOneAttempt(catalog,path)\n    if not path or path=='' then return nil,'failed','caminho vazio' end",
    "local badFilesInJob={}\nlocal function corruptedReportPath() return LrPathUtils.child(logsDir(),'corrupted_files.txt') end\nlocal function recordBadFile(job,path,reason)\n    local key=tostring(path or '')\n    if key=='' or badFilesInJob[key] then return end\n    badFilesInJob[key]=tostring(reason or 'erro desconhecido')\n    job.bad_files=type(job.bad_files)=='table' and job.bad_files or {}\n    table.insert(job.bad_files,{path=key,reason=tostring(reason or 'erro desconhecido'),at=timestamp()})\n    job.bad_files_count=#job.bad_files\n    appendText(corruptedReportPath(),timestamp()..' job='..tostring(job.job_id)..' path='..key..' reason='..tostring(reason or 'erro desconhecido')..'\\n')\n    appendJobEvent(job,'bad_file','Arquivo ignorado por possível corrupção',key..' — '..tostring(reason or 'erro desconhecido'),'warning')\n    plainLog('BAD_FILE_SKIPPED path='..key..' reason='..tostring(reason or 'erro desconhecido'))\nend\nlocal function validateImportFile(path)\n    if not path or path=='' then return false,'caminho vazio' end\n    if not LrFileUtils.exists(path) then return false,'arquivo não encontrado ou offline' end\n    local file,openError=io.open(path,'rb')\n    if not file then return false,'arquivo não pôde ser aberto: '..tostring(openError or 'erro de leitura') end\n    local firstByte=file:read(1)\n    local size=file:seek('end')\n    file:close()\n    if not size or size<=0 or not firstByte then return false,'arquivo vazio (0 bytes) ou ilegível' end\n    return true,nil\nend\n\nlocal function importOneAttempt(catalog,path)\n    if not path or path=='' then return nil,'failed','caminho vazio' end",
    'helpers de isolamento de arquivo danificado')

source = replaceOnce(source,
    "local function importOneWithRetry(catalog,path,job,jobPath)\n    local lastError=nil\n    for attempt=1,MAX_ATTEMPTS do",
    "local function importOneWithRetry(catalog,path,job,jobPath)\n    if badFilesInJob[path] then return nil,'bad_file',badFilesInJob[path] end\n    local valid,validationError=validateImportFile(path)\n    if not valid then recordBadFile(job,path,validationError); safeWriteJob(jobPath,job); return nil,'bad_file',validationError end\n    local lastError=nil\n    for attempt=1,MAX_ATTEMPTS do",
    'pré-validação antes do Lightroom')

source = replaceOnce(source,
    "        lastError=err\n        if attempt<MAX_ATTEMPTS then",
    "        lastError=err\n        local lowered=string.lower(tostring(err or ''))\n        local permanent=string.find(lowered,'arquivo',1,true) or string.find(lowered,'corrupt',1,true) or string.find(lowered,'damaged',1,true) or string.find(lowered,'inválid',1,true) or string.find(lowered,'invalid',1,true) or string.find(lowered,'unsupported',1,true) or string.find(lowered,'não apareceu',1,true)\n        if permanent then recordBadFile(job,path,lastError); safeWriteJob(jobPath,job); return nil,'bad_file',lastError end\n        if attempt<MAX_ATTEMPTS then",
    'interromper retries permanentes')

source = replaceOnce(source,
    "    return nil,'failed',lastError or 'falha desconhecida após 10 tentativas'\nend",
    "    recordBadFile(job,path,lastError or 'falha desconhecida após 10 tentativas')\n    safeWriteJob(jobPath,job)\n    return nil,'bad_file',lastError or 'falha desconhecida após 10 tentativas'\nend",
    'blacklist depois das tentativas')

source = replaceOnce(source,
    "else progress.failed=progress.failed+1; progress.error=tostring(err)..': '..tostring(path) end",
    "elseif result=='bad_file' then progress.failed=progress.failed+1; progress.error='Arquivo danificado ignorado: '..tostring(path)..' — '..tostring(err); job.completed_with_file_errors=true else progress.failed=progress.failed+1; progress.error=tostring(err)..': '..tostring(path) end",
    'continuar pasta após arquivo danificado')

-- Persistência em lote para todos os resultados. A interface não precisa de uma
-- gravação por foto: salva a cada 10 itens ou 2 segundos e sempre ao fechar a pasta.
source = replaceOnce(source,
    "    local photosForCollection={}\n    for _,path in ipairs(files) do",
    "    local photosForCollection={}\n    local progressSinceWrite=0\n    local lastProgressWrite=os.time()\n    for _,path in ipairs(files) do",
    'estado de progresso em lote')

source = replaceOnce(source,
    "        refreshTotals(job); safeWriteJob(jobPath,job); LrTasks.yield()",
    "        refreshTotals(job)\n        progressSinceWrite=progressSinceWrite+1\n        local now=os.time()\n        if progressSinceWrite>=10 or (now-lastProgressWrite)>=2 then\n            safeWriteJob(jobPath,job)\n            progressSinceWrite=0\n            lastProgressWrite=now\n        end\n        LrTasks.yield()",
    'persistência agrupada de progresso')

source = replaceOnce(source,
    "    local collectionName=source.collection; if not collectionName or collectionName=='' then collectionName=LrPathUtils.leafName(source.path or '') end",
    "    if progressSinceWrite>0 then safeWriteJob(jobPath,job) end\n    local collectionName=source.collection; if not collectionName or collectionName=='' then collectionName=LrPathUtils.leafName(source.path or '') end",
    'flush final do lote de progresso')
]=]

io.open = function(path, mode)
    if path == targetPath and (mode == 'rb' or mode == 'r') then
        local realFile, openError = originalOpen(path, mode)
        if not realFile then return nil, openError end
        local content = realFile:read('*a') or ''
        realFile:close()
        content = content:gsub('\r\n','\n'):gsub('\r','\n')
        local marker = "_G.import = function(moduleName)"
        local first = string.find(content, marker, 1, true)
        if not first then error('JobRunner56: marcador de injeção não encontrado') end
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

local ok,runnerOrError=pcall(require,'JobRunner55')
io.open=originalOpen
if not ok then error(runnerOrError) end
return runnerOrError