-- Política final de reparação de Smart Preview para Lightroom Classic 10.4.
-- Toda foto já existente no catálogo entra novamente SOMENTE na etapa de Smart
-- Preview. Ela nunca entra em importedPhotos e, portanto, nunca recebe preset
-- novamente. O Lightroom decide internamente quais Smart Previews já existem.
local LrPathUtils = import 'LrPathUtils'

local originalOpen = io.open
local targetPath = LrPathUtils.child(_PLUGIN.path, 'JobRunner51.lua')

local injection = [=[
-- SMART-ONLY REPAIR: ignoradas sempre verificam/constroem Smart Preview,
-- mas jamais retornam à lista que recebe Develop Preset.
source = replaceOnce(source,
    "if result=='cancelled' then return true elseif result=='imported' then progress.imported=progress.imported+1; table.insert(photosForCollection,photo); table.insert(importedPhotos,photo); table.insert(smartPhotos,photo); table.insert(standardPhotos,photo) elseif result=='skipped' then progress.skipped=progress.skipped+1; table.insert(photosForCollection,photo); if previewRetry.smart[path] then table.insert(smartPhotos,photo) end; if previewRetry.standard[path] then table.insert(standardPhotos,photo) end else progress.failed=progress.failed+1; progress.error=tostring(err)..': '..tostring(path) end",
    "if result=='cancelled' then return true elseif result=='imported' then progress.imported=progress.imported+1; table.insert(photosForCollection,photo); table.insert(importedPhotos,photo); table.insert(smartPhotos,photo); table.insert(standardPhotos,photo) elseif result=='skipped' then progress.skipped=progress.skipped+1; table.insert(photosForCollection,photo); table.insert(smartPhotos,photo); job.smart_preview_recheck_skipped=(job.smart_preview_recheck_skipped or 0)+1; plainLog('SMART_PREVIEW_RECHECK_SKIPPED path='..tostring(path)); if previewRetry.standard[path] then table.insert(standardPhotos,photo) end else progress.failed=progress.failed+1; progress.error=tostring(err)..': '..tostring(path) end",
    'Smart Preview em ignoradas sem preset')

-- Evidência explícita no job de que preset continua restrito às importadas nesta
-- execução. A função applyPreset recebe apenas importedPhotos.
source = replaceOnce(source,
    "local presetOk=applyPreset(catalog,importedPhotos,job,jobPath); safeWriteJob(jobPath,job)",
    "job.preset_candidate_count=#importedPhotos; job.preset_skipped_existing_count=job.total_skipped or 0; local presetOk=applyPreset(catalog,importedPhotos,job,jobPath); safeWriteJob(jobPath,job)",
    'auditoria de isolamento do preset')
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