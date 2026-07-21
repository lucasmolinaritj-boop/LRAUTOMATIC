-- Controle de pausa correto para Lightroom Classic 10.4.
-- O scheduler Python continua criando jobs normalmente. Este gate atua somente no
-- instante em que o plugin tentaria assumir o próximo job queued.
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'

local Runner = require 'JobRunner57'
local originalProcessQueuedOnce = Runner.processQueuedOnce

local function homePath()
    local home = LrPathUtils.getStandardFilePath('home')
    if home and home ~= '' then return home end
    return 'C:\\Users\\Public'
end

local function sharedControlDir()
    return LrPathUtils.child(
        LrPathUtils.child(
            LrPathUtils.child(
                LrPathUtils.child(homePath(), 'AppData'),
                'Local'
            ),
            'LRAutomatic'
        ),
        'control'
    )
end

local function pauseFlagPath()
    return LrPathUtils.child(sharedControlDir(), 'automation_paused.flag')
end

local function forceOnceFlagPath()
    return LrPathUtils.child(sharedControlDir(), 'automation_force_once.flag')
end

local function consumeForceOnce()
    local path = forceOnceFlagPath()
    if not LrFileUtils.exists(path) then return false end
    pcall(function() LrFileUtils.delete(path) end)
    return true
end

function Runner.processQueuedOnce()
    if LrFileUtils.exists(pauseFlagPath()) then
        if consumeForceOnce() then
            return originalProcessQueuedOnce()
        end
        return 0
    end
    return originalProcessQueuedOnce()
end

return Runner
