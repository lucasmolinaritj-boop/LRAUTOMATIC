return {
    LrSdkVersion = 6.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'com.molinari.lrautomatic.v26',
    LrPluginName = 'LRAutomatic V2.6 LR 10.4 Hardened',
    LrInitPlugin = 'Init.lua',
    LrShutdownPlugin = 'Shutdown.lua',
    LrLibraryMenuItems = {
        {
            title = 'LRAutomatic V2.6 - Status e diagnóstico',
            file = 'RescueTest.lua',
        },
        {
            title = 'LRAutomatic V2.6 - Processar fila agora',
            file = 'ProcessNow.lua',
        },
    },
    VERSION = { major = 0, minor = 2, revision = 6, build = 104 },
}
