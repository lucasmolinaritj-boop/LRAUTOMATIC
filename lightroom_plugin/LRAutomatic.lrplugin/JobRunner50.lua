-- Camada de compatibilidade para o Lightroom Classic 10.4.
-- Algumas builds não expõem LrTasks.currentTime(), mas o JobRunner48 usa
-- essa função apenas para variar a semente aleatória da identificação.
local originalImport = import
local originalTasks = originalImport 'LrTasks'

local compatibleTasks = setmetatable({
    currentTime = function()
        return os.clock()
    end,
}, {
    __index = originalTasks,
})

_G.import = function(moduleName)
    if moduleName == 'LrTasks' then
        return compatibleTasks
    end
    return originalImport(moduleName)
end

local ok, runnerOrError = pcall(require, 'JobRunner48')
_G.import = originalImport

if not ok then
    error(runnerOrError)
end

return runnerOrError
