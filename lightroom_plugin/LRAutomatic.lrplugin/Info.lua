return {
    LrSdkVersion = 6.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'com.molinari.lrautomatic.v29',
    LrPluginName = 'LRAutomatic V2.9 Native IO LR 10.4',
    LrInitPlugin = 'Init.lua',
    LrShutdownPlugin = 'Shutdown.lua',
    LrLibraryMenuItems = {
        {
            title = 'LRAutomatic V2.9 - Status e diagnóstico',
            file = 'RescueTest.lua',
        },
        {
            title = 'LRAutomatic V2.9 - Processar fila agora',
            file = 'ProcessNow.lua',
        },
    },
    VERSION = { major = 0, minor = 2, revision = 9, build = 104 },
}