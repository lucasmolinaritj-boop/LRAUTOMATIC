return {
    LrSdkVersion = 6.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'com.molinari.lrautomatic.v40',
    LrPluginName = 'LRAutomatic V4.0 Catálogo + Preset + Smart Preview',
    LrInitPlugin = 'Init.lua',
    LrShutdownPlugin = 'Shutdown.lua',
    LrPluginInfoProvider = 'PluginInfoProvider.lua',
    LrLibraryMenuItems = {
        {
            title = 'LRAutomatic V4.0 - Painel e diagnóstico',
            file = 'RescueTest.lua',
        },
        {
            title = 'LRAutomatic V4.0 - Processar fila agora',
            file = 'ProcessNow.lua',
        },
    },
    VERSION = { major = 0, minor = 4, revision = 0, build = 104 },
}
