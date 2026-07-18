-- Blindagem definitiva do caminho catalog:addPhoto no Lightroom Classic 10.4.
-- Injeta a correção no JobRunner51 antes de toda a cadeia 51 -> 52 -> 53 ser carregada.
local LrPathUtils = import 'LrPathUtils'

local originalOpen = io.open
local targetPath = LrPathUtils.child(_PLUGIN.path, 'JobRunner51.lua')

local injection = [=[
-- AUDITORIA ADDPHOTO: nenhuma exceção de write gate pode escapar para popup.
source = replaceOnce(source,
[[local function withWrite(catalog,actionName,fn,detail)
    local ran,timedOut=false,false
    plainLog('WRITE_BEGIN action='..actionName..' detail='..tostring(detail))
    local status=catalog:withWriteAccessDo(actionName,function(context) ran=true; fn(context) end,{timeout=15,callback=function() timedOut=true end})
    plainLog('WRITE_END action='..actionName..' status='..tostring(status)..' ran='..tostring(ran)..' timeout='..tostring(timedOut))
    return ran and not timedOut and (status==nil or status=='executed'),tostring(status or 'executed')
end]],
[[local function withWrite(catalog,actionName,fn,detail)
    local ran,timedOut=false,false
    local callbackOk,callbackError=true,nil
    plainLog('WRITE_BEGIN action='..actionName..' detail='..tostring(detail))
    if not catalog then return false,'catálogo indisponível' end
    local gate=catalog.withWriteAccessDo
    if type(gate)~='function' then
        plainLog('WRITE_REJECTED action='..actionName..' reason=withWriteAccessDo_nil catalog_type='..type(catalog))
        return false,'API withWriteAccessDo indisponível'
    end
    local gateOk,statusOrError=pcall(function()
        return gate(catalog,actionName,function(context)
            ran=true
            callbackOk,callbackError=pcall(fn,context)
        end,{timeout=15,callback=function() timedOut=true end})
    end)
    if not gateOk then
        plainLog('WRITE_EXCEPTION action='..actionName..' detail='..tostring(detail)..' error='..tostring(statusOrError))
        return false,tostring(statusOrError)
    end
    if not callbackOk then
        plainLog('WRITE_CALLBACK_EXCEPTION action='..actionName..' detail='..tostring(detail)..' error='..tostring(callbackError))
        return false,tostring(callbackError)
    end
    local status=statusOrError
    plainLog('WRITE_END action='..actionName..' status='..tostring(status)..' ran='..tostring(ran)..' timeout='..tostring(timedOut))
    return ran and not timedOut and (status==nil or status=='executed'),tostring(status or 'executed')
end]],
'write gate protegido contra popup')

-- Reobtém o catálogo ativo antes de cada importação, valida identidade e captura
-- a função addPhoto antes da callback. Se a API estiver indisponível, registra e
-- retorna uma falha retryable; nunca executa nil como método.
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
[[local function importOneAttempt(catalog,path)
    if not path or path=='' then return nil,'failed','caminho vazio' end
    if not LrFileUtils.exists(path) then return nil,'failed','arquivo não encontrado' end

    local expectedPath=nil
    if catalog and type(catalog.getPath)=='function' then
        local ok,value=pcall(function() return catalog:getPath() end)
        if ok then expectedPath=value end
    end

    local active=LrApplication.activeCatalog()
    if not active then
        plainLog('ADD_PHOTO_API_UNAVAILABLE reason=no_active_catalog photo='..tostring(path))
        return nil,'failed','nenhum catálogo ativo'
    end

    local activePath=nil
    if type(active.getPath)=='function' then
        local ok,value=pcall(function() return active:getPath() end)
        if ok then activePath=value end
    end
    if expectedPath and activePath and expectedPath~=activePath then
        plainLog('ADD_PHOTO_CATALOG_CHANGED expected='..tostring(expectedPath)..' active='..tostring(activePath)..' photo='..tostring(path))
        return nil,'failed','catálogo ativo mudou durante a tarefa'
    end
    catalog=active

    local findMethod=catalog.findPhotoByPath
    if type(findMethod)~='function' then
        plainLog('ADD_PHOTO_API_UNAVAILABLE method=findPhotoByPath catalog='..tostring(activePath)..' photo='..tostring(path))
        return nil,'failed','API findPhotoByPath indisponível'
    end
    local beforeOk,before=pcall(findMethod,catalog,path)
    if not beforeOk then
        plainLog('ADD_PHOTO_FIND_EXCEPTION phase=before photo='..tostring(path)..' error='..tostring(before))
        return nil,'failed',tostring(before)
    end
    if before then return before,'skipped',nil end

    local addMethod=catalog.addPhoto
    if type(addMethod)~='function' then
        plainLog('ADD_PHOTO_API_UNAVAILABLE method=addPhoto catalog_type='..type(catalog)..' catalog='..tostring(activePath)..' photo='..tostring(path))
        return nil,'failed','API catalog:addPhoto indisponível; tentativa será repetida sem popup'
    end

    local imported=nil
    local ok,reason=withWrite(catalog,'LRAutomatic: importar foto',function()
        imported=addMethod(catalog,path)
    end,path)
    if not ok then
        plainLog('ADD_PHOTO_FAILED photo='..tostring(path)..' error='..tostring(reason))
        return nil,'failed',reason
    end
    if imported then return imported,'imported',nil end

    local afterOk,after=pcall(findMethod,catalog,path)
    if not afterOk then
        plainLog('ADD_PHOTO_FIND_EXCEPTION phase=after photo='..tostring(path)..' error='..tostring(after))
        return nil,'failed',tostring(after)
    end
    if after then return after,'imported',nil end
    return nil,'failed','foto não apareceu no catálogo após addPhoto'
end]],
'importação addPhoto protegida')

]=]

io.open = function(path, mode)
    if path == targetPath and (mode == 'rb' or mode == 'r') then
        local realFile, openError = originalOpen(path, mode)
        if not realFile then return nil, openError end
        local content = realFile:read('*a') or ''
        realFile:close()
        content = content:gsub('\r\n','\n'):gsub('\r','\n')
        local marker = "_G.import = function(moduleName)"
        local first,last = string.find(content,marker,1,true)
        if not first then error('JobRunner54: marcador de injeção não encontrado') end
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

local ok,runnerOrError=pcall(require,'JobRunner53')
io.open=originalOpen
if not ok then error(runnerOrError) end
return runnerOrError
