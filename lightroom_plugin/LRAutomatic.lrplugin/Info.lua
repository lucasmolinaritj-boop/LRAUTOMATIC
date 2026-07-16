return {
    LrSdkVersion = 10.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'com.molinari.lrautomatic',
    LrPluginName = 'LRAutomatic V2.2 Instrumentado',
    LrPluginInfoUrl = 'https://github.com/lucasmolinaritj-boop/LRAUTOMATIC',
    LrInitPlugin = 'Init.lua',
    LrShutdownPlugin = 'Shutdown.lua',
    LrPluginInfoProvider = 'PluginInfoProvider.lua',
    LrLibraryMenuItems = {
        {
            title = 'LRAutomatic V2.2 - Processar fila agora',
            file = 'ProcessNow.lua',
        },
        {
            title = 'LRAutomatic V2.2 - Teste instrumentado',
            file = 'TestPlugin.lua',
        },
    },
    VERSION = { major = 0, minor = 2, revision = 2, build = 104 },
}