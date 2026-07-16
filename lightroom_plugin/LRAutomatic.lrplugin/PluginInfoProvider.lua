local LrApplication = import 'LrApplication'
local LrView = import 'LrView'

return {
    sectionsForTopOfDialog = function(f, propertyTable)
        local catalog = LrApplication.activeCatalog()
        local catalogPath = catalog and catalog:getPath() or '(nenhum catálogo ativo)'
        local loopStatus = _G.LRAutomaticLoopRunning and 'Ativo' or 'Inativo'

        return {
            {
                title = 'LRAutomatic 0.2.0 - Lightroom Classic 10.4',
                f:row {
                    spacing = f:control_spacing(),
                    f:static_text { title = 'Loop automático:' },
                    f:static_text { title = loopStatus },
                },
                f:row {
                    spacing = f:control_spacing(),
                    f:static_text { title = 'Catálogo ativo:' },
                    f:static_text { title = catalogPath, width_in_chars = 60 },
                },
                f:static_text {
                    title = 'Use Biblioteca > Extras do plug-in > LRAutomatic - Processar fila agora para forçar o processamento.',
                    width_in_chars = 80,
                },
            },
        }
    end,
}
