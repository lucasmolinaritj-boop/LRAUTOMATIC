local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'

-- Encerra loops deixados por recargas anteriores antes de iniciar uma nova geração.
_G.LRAutomaticShutdown = true
_G.LRAutomaticGeneration = (_G.LRAutomaticGeneration or 0) + 1
local myGeneration = _G.LRAutomaticGeneration
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticEngineStarting = false
_G.LRAutomaticVersion = '4.9.7-force-init-lr104'
_G.LRAutomaticLastError = nil
_G.LRAutomaticForceInitState = 'scheduled'

local function generationIsCurrent()
    return myGeneration == _G.LRAutomaticGeneration
end

-- Toca deliberadamente partes leves do SDK/catálogo para acordar o contexto do
-- plug-in no Lightroom 10.4 antes de carregar o runner. Não altera fotos, seleção,
-- catálogo nem interface e pode ser chamado repetidamente com segurança.
local function activateLightroomContext(reason)
    _G.LRAutomaticForceInitState = 'activating:' .. tostring(reason or 'startup')

    LrTasks.yield()

    local catalog = LrApplication.activeCatalog()
    if catalog then
        pcall(function() catalog:getPath() end)
        pcall(function() catalog:getTargetPhoto() end)
        pcall(function() catalog:getTargetPhotos() end)
    end

    LrTasks.yield()
    _G.LRAutomaticForceInitState = 'activated:' .. tostring(reason or 'startup')
end

local function ensureEngineStarted(reason)
    if not generationIsCurrent() then return false end
    if _G.LRAutomaticLoopRunning == true then
        _G.LRAutomaticForceInitState = 'already_running'
        return true
    end
    if _G.LRAutomaticEngineStarting == true then return false end

    _G.LRAutomaticEngineStarting = true
    _G.LRAutomaticLastError = nil

    activateLightroomContext(reason)

    -- pcall somente no carregamento: chamadas SDK que cedem ao scheduler ficam
    -- fora dele para evitar problemas com yield no Lightroom 10.4.
    local okRequire, Runner = pcall(require, 'JobRunner56')
    if not okRequire then
        _G.LRAutomaticLastError = tostring(Runner)
        _G.LRAutomaticForceInitState = 'load_failed'
        _G.LRAutomaticEngineStarting = false
        return false
    end

    if not generationIsCurrent() then
        _G.LRAutomaticEngineStarting = false
        return false
    end

    _G.LRAutomaticShutdown = false
    _G.LRAutomaticLoopRunning = true
    _G.LRAutomaticEngineStarting = false
    _G.LRAutomaticLastError = nil
    _G.LRAutomaticForceInitState = 'runner_started:' .. tostring(reason or 'startup')

    Runner.runLoop(function()
        return _G.LRAutomaticShutdown == true or not generationIsCurrent()
    end)

    if generationIsCurrent() then
        _G.LRAutomaticLoopRunning = false
        _G.LRAutomaticForceInitState = 'runner_stopped'
    end
    return true
end

-- Permite que painel/diagnóstico deem outro "tranco" no motor sem criar um
-- segundo loop. A chamada apenas agenda uma tentativa idempotente.
_G.LRAutomaticForceInit = function(reason)
    LrTasks.startAsyncTask(function()
        activateLightroomContext(reason or 'manual')
        ensureEngineStarted(reason or 'manual')
    end)
end

-- LR FORCE INIT: várias tentativas curtas independentes do único disparo de
-- startup. A primeira que iniciar o runner vence; as demais saem sem duplicá-lo.
for attempt, delay in ipairs({ 0.5, 2, 5, 10 }) do
    LrTasks.startAsyncTask(function()
        LrTasks.sleep(delay)
        if not generationIsCurrent() or _G.LRAutomaticLoopRunning == true then return end
        _G.LRAutomaticForceInitState = 'attempt_' .. tostring(attempt)
        ensureEngineStarted('startup_attempt_' .. tostring(attempt))
    end)
end
