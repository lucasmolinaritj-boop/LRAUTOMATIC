return {
    LrSdkVersion = 10.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'com.molinari.lrautomatic',
    LrPluginName = 'LRAutomatic 10.4',
    LrPluginInfoUrl = 'https://github.com/lucasmolinaritj-boop/LRAUTOMATIC',
    LrInitPlugin = 'Init.lua',
    LrShutdownPlugin = 'Shutdown.lua',
    LrPluginInfoProvider = 'PluginInfoProvider.lua',
    LrLibraryMenuItems = {
        {
            title = 'LRAutomatic - Processar fila agora',
            file = 'ProcessNow.lua',
        },
        {
            title = 'LRAutomatic - Testar plugin',
            file = 'TestPlugin.lua',
        },
    },
    VERSION = { major = 0, minor = 2, revision = 0, build = 104 },
}
