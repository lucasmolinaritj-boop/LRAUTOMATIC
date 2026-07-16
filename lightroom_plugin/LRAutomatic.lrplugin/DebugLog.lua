local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrLogger = import 'LrLogger'

local DebugLog = {}
local sdkLogger = LrLogger('LRAutomaticV2')
sdkLogger:enable('logfile')

local function dataDir()
    local base = os.getenv('LOCALAPPDATA')
    if not base or base == '' then
        base = LrPathUtils.getStandardFilePath('appData')
    end
    return LrPathUtils.child(base, 'LRAutomatic')
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
    LrFileUtils.writeFile(path, old .. line .. '\n')
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
        LrFileUtils.writeFile(LrPathUtils.child(DebugLog.stateDir(), name), tostring(content or ''))
    end)
    if not ok then DebugLog.error('state_write_failed', tostring(err)) end
end

function DebugLog.heartbeat(detail)
    DebugLog.writeState('heartbeat.txt', os.date('!%Y-%m-%dT%H:%M:%SZ') .. '\n' .. tostring(detail or ''))
end

return DebugLog
