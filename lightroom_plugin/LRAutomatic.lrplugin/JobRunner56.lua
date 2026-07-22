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
