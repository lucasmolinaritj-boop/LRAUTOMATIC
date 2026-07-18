-- Compatibilidade final para o runner gerado pelo JobRunner51/52.
-- Os helpers de preview foram injetados antes das declarações locais de readJson
-- e writeJsonAtomic no JobRunner48; em Lua 5.1 isso os fazia procurar globais.
-- Este loader fornece implementações isoladas e robustas para esses helpers.
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'
local Json = require 'Json'

local function stripBom(content)
    if content and string.byte(content,1)==239 and string.byte(content,2)==187 and string.byte(content,3)==191 then
        return string.sub(content,4)
    end
    return content
end

_G.readJson = function(path)
    local file = io.open(path, 'rb')
    if not file then return nil, 'arquivo não pôde ser lido' end
    local content = file:read('*a')
    file:close()
    local ok, value = pcall(Json.decode, stripBom(content or ''))
    if not ok or type(value) ~= 'table' then return nil, tostring(value) end
    return value, nil
end

_G.writeJsonAtomic = function(path, value)
    local okEncode, encoded = pcall(Json.encode, value)
    if not okEncode or not encoded then return false end

    local parent = LrPathUtils.parent(path)
    if parent and parent ~= '' then LrFileUtils.createAllDirectories(parent) end

    local temp = path .. '.preview_retry.tmp.' .. tostring(os.time()) .. '.' .. tostring(math.random(100000,999999))
    local file = io.open(temp, 'wb')
    if not file then return false end
    local writeOk = file:write(encoded)
    file:flush()
    file:close()
    if not writeOk then
        if LrFileUtils.exists(temp) then LrFileUtils.delete(temp) end
        return false
    end

    local verify = io.open(temp, 'rb')
    local valid = false
    if verify then
        valid = verify:read('*a') == encoded
        verify:close()
    end
    if not valid then
        if LrFileUtils.exists(temp) then LrFileUtils.delete(temp) end
        return false
    end

    for attempt=1,10 do
        if LrFileUtils.exists(path) then LrFileUtils.delete(path) end
        if LrFileUtils.move(temp, path) == true then
            local check = io.open(path, 'rb')
            local same = false
            if check then same = check:read('*a') == encoded; check:close() end
            if same then return true end
        end
        LrTasks.sleep(math.min(0.1 * attempt, 0.5))
    end

    if LrFileUtils.exists(temp) then LrFileUtils.delete(temp) end
    return false
end

local ok, runnerOrError = pcall(require, 'JobRunner52')
if not ok then error(runnerOrError) end
return runnerOrError
