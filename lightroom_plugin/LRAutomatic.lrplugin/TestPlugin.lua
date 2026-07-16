local LrApplication = import 'LrApplication'
local LrDialogs = import 'LrDialogs'

local catalog = LrApplication.activeCatalog()
local catalogPath = catalog and catalog:getPath() or '(nenhum catálogo ativo)'
local loopStatus = _G.LRAutomaticLoopRunning and 'ATIVO' or 'INATIVO'

LrDialogs.message(
    'LRAutomatic 10.4',
    'Plugin carregado com sucesso.\n\nLoop automático: ' .. loopStatus .. '\nCatálogo ativo:\n' .. tostring(catalogPath),
    'info'
)
