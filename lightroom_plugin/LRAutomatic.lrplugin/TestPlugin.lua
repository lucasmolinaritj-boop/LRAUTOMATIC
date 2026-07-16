local LrApplication = import 'LrApplication'
local LrDialogs = import 'LrDialogs'
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local Debug = require 'DebugLog'

local lines = {}
local function add(name, value)
    table.insert(lines, tostring(name) .. ': ' .. tostring(value))
end

local catalog = LrApplication.activeCatalog()
local catalogPath = catalog and catalog:getPath() or '(nenhum catálogo ativo)'
local base = os.getenv('LOCALAPPDATA') or LrPathUtils.getStandardFilePath('appData')
local dataDir = LrPathUtils.child(base, 'LRAutomatic')
local jobsDir = LrPathUtils.child(dataDir, 'jobs')
local jobCount = 0
local queuedCount = 0
local scanOk, scanError = pcall(function()
    LrFileUtils.createAllDirectories(jobsDir)
    for path in LrFileUtils.files(jobsDir) do
        if string.lower(LrPathUtils.extension(path) or '') == 'json' then
            jobCount = jobCount + 1
            local text = LrFileUtils.readFile(path) or ''
            if string.find(text, '"status"%s*:%s*"queued"') then queuedCount = queuedCount + 1 end
        end
    end
end)

add('Versão', _G.LRAutomaticVersion or '(não definida)')
add('Loop automático', _G.LRAutomaticLoopRunning and 'ATIVO' or 'INATIVO')
add('Último erro', _G.LRAutomaticLastError or '(nenhum)')
add('Catálogo ativo', catalogPath)
add('Pasta do plugin', _PLUGIN and _PLUGIN.path or '(indisponível)')
add('Pasta de jobs', jobsDir)
add('Jobs encontrados', jobCount)
add('Jobs queued', queuedCount)
add('Leitura da fila', scanOk and 'OK' or ('ERRO: ' .. tostring(scanError)))

local report = table.concat(lines, '\n')
Debug.info('manual_self_test', string.gsub(report, '\n', ' | '))
Debug.writeState('manual_test.txt', os.date('!%Y-%m-%dT%H:%M:%SZ') .. '\n' .. report)

LrDialogs.message(
    'LRAutomatic V2 — Teste instrumentado',
    report .. '\n\nEste resultado foi salvo no ZIP de diagnóstico.',
    scanOk and 'info' or 'critical'
)
