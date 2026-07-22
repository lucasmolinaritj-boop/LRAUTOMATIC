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

-- Persistência em lote: ignoradas não precisam regravar o JSON a cada item.
source = replaceOnce(source,
    "    local photosForCollection={}\n    for _,path in ipairs(files) do",
    "    local photosForCollection={}\n    local skippedSinceWrite=0\n    local lastProgressWrite=os.time()\n    for _,path in ipairs(files) do",
    'estado de progresso em lote')

source = replaceOnce(source,
    "        refreshTotals(job); safeWriteJob(jobPath,job); LrTasks.yield()",
    "        refreshTotals(job)\n        if result=='skipped' then\n            skippedSinceWrite=skippedSinceWrite+1\n            local now=os.time()\n            if skippedSinceWrite>=25 or (now-lastProgressWrite)>=1 then\n                safeWriteJob(jobPath,job)\n                skippedSinceWrite=0\n                lastProgressWrite=now\n                LrTasks.yield()\n            end\n        else\n            safeWriteJob(jobPath,job)\n            skippedSinceWrite=0\n            lastProgressWrite=os.time()\n            LrTasks.yield()\n        end",
    'persistência agrupada de ignoradas')

source = replaceOnce(source,
    "    local collectionName=source.collection; if not collectionName or collectionName=='' then collectionName=LrPathUtils.leafName(source.path or '') end",
    "    if skippedSinceWrite>0 then safeWriteJob(jobPath,job) end\n    local collectionName=source.collection; if not collectionName or collectionName=='' then collectionName=LrPathUtils.leafName(source.path or '') end",
    'flush final do lote de ignoradas')
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