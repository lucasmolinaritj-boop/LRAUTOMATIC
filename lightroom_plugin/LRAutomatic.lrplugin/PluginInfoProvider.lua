local LrApplication = import 'LrApplication'
local LrPathUtils = import 'LrPathUtils'

local function statusText()
    if _G.LRAutomaticLoopRunning then return 'ATIVO — monitorando a fila' end
    if _G.LRAutomaticLastError then return 'ERRO — ' .. tostring(_G.LRAutomaticLastError) end
    return 'INATIVO'
end

return {
    sectionsForTopOfDialog = function(f, propertyTable)
        local catalog = LrApplication.activeCatalog()
        local catalogPath = catalog and catalog:getPath() or '(nenhum catálogo ativo)'
        local catalogName = catalog and LrPathUtils.leafName(catalogPath) or '—'
        return {
            {
                title = 'LRAutomatic V4.0 — Central de automação',
                synopsis = 'Importação, presets e Smart Previews',
                f:column {
                    spacing = f:control_spacing(),
                    f:row {
                        f:static_text { title = 'Motor automático:', width_in_chars = 20 },
                        f:static_text { title = statusText(), width_in_chars = 55 },
                    },
                    f:row {
                        f:static_text { title = 'Catálogo ativo:', width_in_chars = 20 },
                        f:static_text { title = catalogName, width_in_chars = 55 },
                    },
                    f:row {
                        f:static_text { title = 'Caminho:', width_in_chars = 20 },
                        f:static_text { title = catalogPath, width_in_chars = 55 },
                    },
                    f:separator { fill_horizontal = 1 },
                    f:static_text {
                        title = 'Pipeline: importar → organizar em coleções → aplicar preset → criar Smart Previews oficiais.',
                        width_in_chars = 80,
                    },
                    f:static_text {
                        title = 'Biblioteca > Extras do plug-in permite forçar a fila ou abrir o diagnóstico.',
                        width_in_chars = 80,
                    },
                },
            },
        }
    end,
}
