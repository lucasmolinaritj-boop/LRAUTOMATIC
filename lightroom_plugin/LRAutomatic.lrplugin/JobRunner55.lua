-- Corrige a contabilidade ambígua de previews no Lightroom Classic 10.4.
-- Quando buildSmartPreviews retorna zero criadas/existentes/falhas para uma fila
-- não vazia, as fotos não podem ser declaradas concluídas. Elas permanecem no
-- ledger e são retomadas em jobs seguintes, mesmo já estando no catálogo.
local LrPathUtils = import 'LrPathUtils'

local originalOpen = io.open
local targetPath = LrPathUtils.child(_PLUGIN.path, 'JobRunner51.lua')

local injection = [=[
-- PREVIEW RETRY AUDIT: resposta vazia/ambígua nunca significa sucesso.
source = replaceOnce(source,
[[        local result=catalog:buildSmartPreviews(pending)
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return false end
        local created=result and result.created or {}; local existed=result and result.existed or {}; local failed=result and result.failed or pending
        createdTotal=createdTotal+#created; existedTotal=existedTotal+#existed; pending=failed
        job.smart_previews_created=createdTotal; job.smart_previews_existed=existedTotal; job.smart_previews_failed=#pending; job.smart_previews_pending=#pending
        safeWriteJob(jobPath,job)
        if #pending==0 then for _,p in ipairs(inputPhotos) do local path=photoPath(p); if path then previewRetry.smart[path]=nil end end; savePreviewRetry(); job.smart_previews_status='completed'; return true end]],
[[        local attempted=pending
        local callOk,result=pcall(function() return catalog:buildSmartPreviews(attempted) end)
        if isCancelled(jobPath,job) or not stillOwns(jobPath,job) then return false end
        if not callOk then
            plainLog('SMART_PREVIEW_EXCEPTION error='..tostring(result)..' attempted='..tostring(#attempted))
            result=nil
        end

        local created=(type(result)=='table' and type(result.created)=='table') and result.created or {}
        local existed=(type(result)=='table' and type(result.existed)=='table') and result.existed or {}
        local explicitFailed=(type(result)=='table' and type(result.failed)=='table') and result.failed or {}
        local accounted={}
        for _,p in ipairs(created) do accounted[tostring(p)]=true end
        for _,p in ipairs(existed) do accounted[tostring(p)]=true end
        for _,p in ipairs(explicitFailed) do accounted[tostring(p)]=true end

        local unresolved={}
        for _,p in ipairs(explicitFailed) do table.insert(unresolved,p) end
        for _,p in ipairs(attempted) do
            if not accounted[tostring(p)] then table.insert(unresolved,p) end
        end

        createdTotal=createdTotal+#created
        existedTotal=existedTotal+#existed
        pending=unresolved
        job.smart_previews_created=createdTotal
        job.smart_previews_existed=existedTotal
        job.smart_previews_failed=#pending
        job.smart_previews_pending=#pending

        -- Enquanto o Smart Preview estiver sem confirmação, mantém também o
        -- Standard Preview pendente. requestJpegThumbnail pode devolver o preview
        -- embutido/cache e não provar que o Standard persistente foi construído.
        for _,p in ipairs(pending) do
            local path=photoPath(p)
            if path then
                previewRetry.smart[path]=true
                previewRetry.standard[path]=true
            end
        end
        savePreviewRetry()
        safeWriteJob(jobPath,job)

        if #pending==0 then
            for _,p in ipairs(inputPhotos) do
                local path=photoPath(p)
                if path then previewRetry.smart[path]=nil end
            end
            savePreviewRetry()
            job.smart_previews_status='completed'
            return true
        end

        if #created==0 and #existed==0 and #explicitFailed==0 then
            plainLog('SMART_PREVIEW_AMBIGUOUS_ZERO attempted='..tostring(#attempted)..' retry='..tostring(#pending))
        end]],
    'contabilidade verificável de Smart Preview')

-- Um thumbnail retornado pelo cache não limpa o retry padrão enquanto o Smart
-- Preview da mesma foto ainda estiver sem confirmação.
source = replaceOnce(source,
[[local currentPath=photoPath(photo); if success then job.standard_previews_created=job.standard_previews_created+1; if currentPath then previewRetry.standard[currentPath]=nil end else job.standard_previews_failed=job.standard_previews_failed+1; if currentPath then previewRetry.standard[currentPath]=true end; plainLog('STANDARD_PREVIEW_GAVE_UP photo='..index..' error='..tostring(lastError)) end; savePreviewRetry()]],
[[local currentPath=photoPath(photo); if success then job.standard_previews_created=job.standard_previews_created+1; if currentPath then if previewRetry.smart[currentPath] then previewRetry.standard[currentPath]=true; job.standard_previews_retry_deferred=(job.standard_previews_retry_deferred or 0)+1; plainLog('STANDARD_PREVIEW_DEFERRED_UNTIL_SMART path='..tostring(currentPath)) else previewRetry.standard[currentPath]=nil end end else job.standard_previews_failed=job.standard_previews_failed+1; if currentPath then previewRetry.standard[currentPath]=true end; plainLog('STANDARD_PREVIEW_GAVE_UP photo='..index..' error='..tostring(lastError)) end; savePreviewRetry()]],
    'não limpar Standard por thumbnail de cache')

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
