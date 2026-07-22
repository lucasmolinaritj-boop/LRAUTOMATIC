-- Controle de pausa correto para Lightroom Classic 10.4.
-- O scheduler Python continua criando jobs normalmente. Este gate atua somente no
-- instante em que o plugin tentaria assumir o próximo job queued.
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'

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

local function errorTrace(err)
    local message = tostring(err or 'erro desconhecido')
    if debug and debug.traceback then
        return debug.traceback(message, 2)
    end
    return message
end

local function appendEmergencyLog(message)
    pcall(function()
        local logDir = LrPathUtils.child(
            LrPathUtils.child(
                LrPathUtils.child(
                    LrPathUtils.child(homePath(), 'AppData'),
                    'Local'
                ),
                'LRAutomatic'
            ),
            'logs'
        )
        LrFileUtils.createAllDirectories(logDir)
        local path = LrPathUtils.child(logDir, 'plugin-emergency.log')
        local file = io.open(path, 'ab')
        if file then
            file:write(os.date('!%Y-%m-%dT%H:%M:%SZ') .. ' ' .. tostring(message) .. '\n')
            file:flush()
            file:close()
        end
    end)
end

local function protectedProcessQueuedOnce()
    local ok, processedOrError = xpcall(function()
        return originalProcessQueuedOnce()
    end, errorTrace)

    if ok then
        _G.LRAutomaticLastError = nil
        return processedOrError or 0
    end

    _G.LRAutomaticLastError = tostring(processedOrError)
    appendEmergencyLog('PROCESS_QUEUED_ONCE_FAILED ' .. tostring(processedOrError))
    return 0
end

function Runner.processQueuedOnce()
    -- A organização de jobs já finalizados continua funcionando mesmo quando o
    -- início de novas importações está pausado. Nenhuma falha desta etapa pode
    -- encerrar o motor principal.
    local organizerOk, organizerError = xpcall(function()
        CollectionOrganizer.processOnce()
    end, errorTrace)
    if not organizerOk then
        _G.LRAutomaticLastError = tostring(organizerError)
        appendEmergencyLog('COLLECTION_ORGANIZER_BEFORE_FAILED ' .. tostring(organizerError))
    end

    if LrFileUtils.exists(pauseFlagPath()) then
        if consumeForceOnce() then
            local processed = protectedProcessQueuedOnce()
            local afterOk, afterError = xpcall(function()
                CollectionOrganizer.processOnce()
            end, errorTrace)
            if not afterOk then
                _G.LRAutomaticLastError = tostring(afterError)
                appendEmergencyLog('COLLECTION_ORGANIZER_AFTER_FAILED ' .. tostring(afterError))
            end
            return processed
        end
        return 0
    end

    local processed = protectedProcessQueuedOnce()
    local afterOk, afterError = xpcall(function()
        CollectionOrganizer.processOnce()
    end, errorTrace)
    if not afterOk then
        _G.LRAutomaticLastError = tostring(afterError)
        appendEmergencyLog('COLLECTION_ORGANIZER_AFTER_FAILED ' .. tostring(afterError))
    end
    return processed
end

function Runner.runLoop(shouldStop)
    -- Proteção externa definitiva: mesmo se uma camada interna deixar uma exceção
    -- escapar após concluir um job, o loop não morre e volta a varrer a fila.
    while not shouldStop() do
        local ok, loopError = xpcall(function()
            originalRunLoop(function()
                return true
            end)
        end, errorTrace)

        if not ok then
            _G.LRAutomaticLastError = tostring(loopError)
            appendEmergencyLog('RUN_LOOP_ITERATION_FAILED ' .. tostring(loopError))
        end

        if not shouldStop() then
            LrTasks.sleep(1)
        end
    end
end

return Runner
