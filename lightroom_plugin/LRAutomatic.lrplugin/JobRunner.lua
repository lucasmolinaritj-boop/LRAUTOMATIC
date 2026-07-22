-- Motor oficial e estável do LRAutomatic para Lightroom Classic 10.4.
--
-- Este é o único ponto de entrada permitido para o executor. Não criar novos
-- JobRunnerNN.lua. As camadas numeradas restantes são implementação-base legada
-- temporária e não devem ser referenciadas por Init.lua ou outros módulos.
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'

local Runner = require 'JobRunner57'
local CollectionOrganizer = require 'CollectionOrganizer'
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
    -- O JobRunner57 já fornece o loop direto e contínuo, sem worker filho.
    -- Repassar a condição real de encerramento evita tanto o loop que não inicia
    -- quanto wrappers pcall/xpcall incompatíveis com operações que fazem yield.
    return originalRunLoop(shouldStop)
end

Runner.engine_name = 'JobRunner'
Runner.engine_version = '4.11.2-single-official-entrypoint-lr104'

return Runner
