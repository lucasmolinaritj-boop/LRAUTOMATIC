local LrTasks = import 'LrTasks'

-- O Lightroom Classic 10.4 não expõe currentTime em todas as builds.
-- O runner usa esse valor apenas para variar a semente aleatória da identificação.
if type(LrTasks.currentTime) ~= 'function' then
    LrTasks.currentTime = function()
        return os.clock()
    end
end

return require 'JobRunner48'
