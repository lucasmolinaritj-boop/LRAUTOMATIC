local LrApplication = import 'LrApplication'
local LrDialogs = import 'LrDialogs'
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'

local function homePath()
    local home = LrPathUtils.getStandardFilePath('home')
    if home and home ~= '' then return home end
    return 'C:\\Users\\Public'
end

local dataDir = LrPathUtils.child(LrPathUtils.child(LrPathUtils.child(homePath(), 'AppData'), 'Local'), 'LRAutomatic')
local stateDir = LrPathUtils.child(dataDir, 'plugin_state')
local path = LrPathUtils.child(stateDir, 'rescue_test.txt')

local catalog = LrApplication.activeCatalog()
local catalogPath = catalog and catalog:getPath() or '(nenhum catálogo ativo)'

pcall(function()
    LrFileUtils.createAllDirectories(stateDir)
    LrFileUtils.writeFile(path,
        'RESCUE TEST OK\n' ..
        'version=0.2.3\n' ..
        'plugin_path=' .. tostring(_PLUGIN and _PLUGIN.path or '') .. '\n' ..
        'catalog=' .. tostring(catalogPath) .. '\n' ..
        'loop=' .. tostring(_G.LRAutomaticLoopRunning) .. '\n' ..
        'last_error=' .. tostring(_G.LRAutomaticLastError)
    )
end)

LrDialogs.message(
    'LRAutomatic V2.3',
    'PLUGIN VIVO!\n\nCatálogo:\n' .. tostring(catalogPath) .. '\n\nLoop automático: ' .. tostring(_G.LRAutomaticLoopRunning) .. '\n\nÚltimo erro: ' .. tostring(_G.LRAutomaticLastError),
    'info'
)
