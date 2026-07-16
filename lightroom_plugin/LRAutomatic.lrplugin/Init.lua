local LrApplication = import 'LrApplication'
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'

_G.LRAutomaticShutdown = false
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticVersion = '0.2.3-rescue'
_G.LRAutomaticLastError = nil

local function homePath()
    local home = LrPathUtils.getStandardFilePath('home')
    if home and home ~= '' then return home end
    return 'C:\\Users\\Public'
end

local function dataDir()
    return LrPathUtils.child(LrPathUtils.child(LrPathUtils.child(homePath(), 'AppData'), 'Local'), 'LRAutomatic')
end

local function stateDir()
    return LrPathUtils.child(dataDir(), 'plugin_state')
end

local function writeState(name, text)
    pcall(function()
        LrFileUtils.createAllDirectories(stateDir())
        LrFileUtils.writeFile(LrPathUtils.child(stateDir(), name), tostring(text or ''))
    end)
end

local function catalogPath()
    local ok, value = pcall(function()
        local catalog = LrApplication.activeCatalog()
        return catalog and catalog:getPath() or '(nenhum catálogo ativo)'
    end)
    return ok and tostring(value) or ('erro: ' .. tostring(value))
end

writeState('bootstrap.txt', 'OK\nversion=' .. _G.LRAutomaticVersion .. '\nplugin_path=' .. tostring(_PLUGIN and _PLUGIN.path or '') .. '\ndata_dir=' .. dataDir() .. '\ncatalog=' .. catalogPath())

LrTasks.startAsyncTask(function()
    writeState('async_started.txt', os.date('!%Y-%m-%dT%H:%M:%SZ'))

    local okRequire, Runner = pcall(require, 'JobRunner')
    if not okRequire then
        _G.LRAutomaticLastError = tostring(Runner)
        writeState('fatal_error.txt', 'JobRunner require failed:\n' .. tostring(Runner))
        return
    end

    _G.LRAutomaticLoopRunning = true
    writeState('heartbeat.txt', os.date('!%Y-%m-%dT%H:%M:%SZ') .. '\nloop=running\ncatalog=' .. catalogPath())

    local okLoop, err = pcall(function()
        Runner.runLoop(function()
            return _G.LRAutomaticShutdown == true
        end)
    end)

    _G.LRAutomaticLoopRunning = false
    if not okLoop then
        _G.LRAutomaticLastError = tostring(err)
        writeState('fatal_error.txt', 'Loop crashed:\n' .. tostring(err))
    else
        writeState('loop_stopped.txt', os.date('!%Y-%m-%dT%H:%M:%SZ'))
    end
end)
