-- Motor oficial e estável do LRAutomatic para Lightroom Classic 10.4.
-- O núcleo é carregado diretamente; as antigas camadas numeradas não
-- participam mais do caminho de execução.
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'

local Runner = require 'JobRunnerCore'
local CollectionOrganizer = require 'CollectionOrganizerReliable'
local originalProcessQueuedOnce = Runner.processQueuedOnce
local originalRunLoop = Runner.runLoop

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
    -- Não envolver este fluxo em pcall/xpcall: as APIs do Lightroom e
    -- withWriteAccessDo podem fazer yield, algo incompatível com pcall no Lua 5.1.
    CollectionOrganizer.processOnce()

    if LrFileUtils.exists(pauseFlagPath()) then
        if consumeForceOnce() then
            local processed = originalProcessQueuedOnce()
            CollectionOrganizer.processOnce()
            return processed
        end
        return 0
    end

    local processed = originalProcessQueuedOnce()
    CollectionOrganizer.processOnce()
    return processed
end

function Runner.runLoop(shouldStop)
    -- O núcleo executa diretamente, sem worker filho nem pcall/xpcall sobre APIs
    -- do catálogo que podem fazer yield.
    return originalRunLoop(shouldStop)
end

Runner.engine_name = 'JobRunner'
Runner.engine_version = '5.0.0-canonical-yield-safe'

return Runner
