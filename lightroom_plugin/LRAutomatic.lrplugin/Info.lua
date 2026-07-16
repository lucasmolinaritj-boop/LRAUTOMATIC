return {
    LrSdkVersion = 6.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'com.molinari.lrautomatic.v32',
    LrPluginName = 'LRAutomatic V3.2 Import Verified LR 10.4',
    LrInitPlugin = 'Init.lua',
    LrShutdownPlugin = 'Shutdown.lua',
    LrLibraryMenuItems = {
        {
            title = 'LRAutomatic V3.2 - Status e diagnóstico',
            file = 'RescueTest.lua',
        },
        {
            title = 'LRAutomatic V3.2 - Processar fila agora',
            file = 'ProcessNow.lua',
        },
    },
    VERSION = { major = 0, minor = 3, revision = 2, build = 104 },
}