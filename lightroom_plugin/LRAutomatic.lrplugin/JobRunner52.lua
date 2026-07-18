-- Loader robusto para Lightroom Classic 10.4.
-- Normaliza CRLF/LF do JobRunner48 antes de o JobRunner51 aplicar seus patches.
-- Isso evita falhas "trecho não encontrado" causadas apenas por finais de linha do Windows.
local LrPathUtils = import 'LrPathUtils'

local originalOpen = io.open
local basePath = LrPathUtils.child(_PLUGIN.path, 'JobRunner48.lua')

io.open = function(path, mode)
    if path == basePath and (mode == 'rb' or mode == 'r') then
        local realFile, openError = originalOpen(path, mode)
        if not realFile then return nil, openError end
        local content = realFile:read('*a') or ''
        realFile:close()
        content = content:gsub('\r\n', '\n'):gsub('\r', '\n')

        local consumed = false
        return {
            read = function(_, format)
                if consumed then return nil end
                consumed = true
                return content
            end,
            close = function() return true end,
        }
    end
    return originalOpen(path, mode)
end

local ok, runnerOrError = pcall(require, 'JobRunner51')
io.open = originalOpen

if not ok then
    error(runnerOrError)
end

return runnerOrError
