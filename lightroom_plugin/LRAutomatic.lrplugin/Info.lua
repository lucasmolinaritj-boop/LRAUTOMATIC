return {
    LrSdkVersion = 6.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'com.molinari.lrautomatic.v31',
    LrPluginName = 'LRAutomatic V3.1 Task-Safe LR 10.4',
    LrInitPlugin = 'Init.lua',
    LrShutdownPlugin = 'Shutdown.lua',
    LrLibraryMenuItems = {
        {
            title = 'LRAutomatic V3.1 - Status e diagnóstico',
            file = 'RescueTest.lua',
        },
        {
            title = 'LRAutomatic V3.1 - Processar fila agora',
            file = 'ProcessNow.lua',
        },
    },
    VERSION = { major = 0, minor = 3, revision = 1, build = 104 },
}
