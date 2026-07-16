local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrLogger = import 'LrLogger'

local DebugLog = {}
local sdkLogger = LrLogger('LRAutomaticV2')
sdkLogger:enable('logfile')

-- Lightroom Classic 10.4 uses a restricted Lua runtime where os.getenv may be nil.
-- Build the same Windows path used by the Python service without environment access:
-- C:\Users\<user>\AppData\Local\LRAutomatic
local function dataDir()
    local home = LrPathUtils.getStandardFilePath('home')
    if home and home ~= '' then
        return LrPathUtils.child(
            LrPathUtils.child(
                LrPathUtils.child(home, 'AppData'),
                'Local'
            ),
            'LRAutomatic'
        )
    end

    -- Last-resort SDK-only fallback. This may resolve to Roaming, but keeps the
    -- plugin alive and produces a diagnostic log instead of aborting at startup.
    local appData = LrPathUtils.getStandardFilePath('appData')
    if not appData or appData == '' then
        error('Lightroom SDK não forneceu home nem appData')
    end
    return LrPathUtils.child(appData, 'LRAutomatic')
end

function DebugLog.dataDir()
    return dataDir()
end

function DebugLog.logsDir()
    return LrPathUtils.child(dataDir(), 'logs')
end

function DebugLog.stateDir()
    return LrPathUtils.child(dataDir(), 'plugin_state')
end

local function append(path, line)
    LrFileUtils.createAllDirectories(LrPathUtils.parent(path))
    local old = LrFileUtils.readFile(path) or ''
    local ok = LrFileUtils.writeFile(path, old .. line .. '\n')
    if not ok then error('não foi possível gravar log em ' .. tostring(path)) end
end

function DebugLog.write(level, event, detail)
    local line = string.format(
        '%s [%s] %s | %s',
        os.date('!%Y-%m-%dT%H:%M:%SZ'),
        tostring(level or 'INFO'),
        tostring(event or 'event'),
        tostring(detail or '')
    )
    pcall(append, LrPathUtils.child(DebugLog.logsDir(), 'plugin-v2.log'), line)
    if level == 'ERROR' then sdkLogger:error(line)
    elseif level == 'WARN' then sdkLogger:warn(line)
    else sdkLogger:info(line) end
end

function DebugLog.info(event, detail) DebugLog.write('INFO', event, detail) end
function DebugLog.warn(event, detail) DebugLog.write('WARN', event, detail) end
function DebugLog.error(event, detail) DebugLog.write('ERROR', event, detail) end

function DebugLog.writeState(name, content)
    local ok, err = pcall(function()
        LrFileUtils.createAllDirectories(DebugLog.stateDir())
        local path = LrPathUtils.child(DebugLog.stateDir(), name)
        local written = LrFileUtils.writeFile(path, tostring(content or ''))
        if not written then error('não foi possível gravar estado em ' .. tostring(path)) end
    end)
    if not ok then DebugLog.error('state_write_failed', tostring(err)) end
end

function DebugLog.heartbeat(detail)
    DebugLog.writeState('heartbeat.txt', os.date('!%Y-%m-%dT%H:%M:%SZ') .. '\n' .. tostring(detail or ''))
end

return DebugLog