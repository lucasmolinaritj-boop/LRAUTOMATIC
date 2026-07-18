local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'
local Json = require 'Json'

local SafeRunner = {}
local runner = nil

local function homePath()
    local home = LrPathUtils.getStandardFilePath('home')
    if home and home ~= '' then return home end
    return 'C:\\Users\\Public'
end

local function dataDir()
    return LrPathUtils.child(LrPathUtils.child(LrPathUtils.child(homePath(), 'AppData'), 'Local'), 'LRAutomatic')
end

local function jobsDir()
    return LrPathUtils.child(dataDir(), 'jobs')
end

local function logsDir()
    return LrPathUtils.child(dataDir(), 'logs')
end

local function appendLog(message)
    pcall(function()
        LrFileUtils.createAllDirectories(logsDir())
        local path = LrPathUtils.child(logsDir(), 'plugin-safety.log')
        local file = io.open(path, 'ab')
        if file then
            file:write(os.date('!%Y-%m-%dT%H:%M:%SZ') .. ' ' .. tostring(message) .. '\n')
            file:close()
        end
    end)
end

local function readJson(path)
    local file = io.open(path, 'rb')
    if not file then return nil end
    local content = file:read('*a')
    file:close()
    if string.byte(content, 1) == 239 and string.byte(content, 2) == 187 and string.byte(content, 3) == 191 then
        content = string.sub(content, 4)
    end
    local ok, value = pcall(Json.decode, content)
    if ok and type(value) == 'table' then return value end
    return nil
end

local function writeJson(path, value)
    local temp = path .. '.safety.tmp'
    local file = io.open(temp, 'wb')
    if not file then return false end
    local ok = pcall(function() file:write(Json.encode(value)) end)
    file:close()
    if not ok then
        pcall(function() LrFileUtils.delete(temp) end)
        return false
    end
    if LrFileUtils.exists(path) then pcall(function() LrFileUtils.delete(path) end) end
    return LrFileUtils.move(temp, path) == true
end

local function isJobFile(path)
    local name = string.lower(LrPathUtils.leafName(path) or tostring(path))
    return string.sub(name, 1, 4) == 'job_' and string.sub(name, -5) == '.json'
end

local function finalizeInterruptedJob(errorMessage)
    LrFileUtils.createAllDirectories(jobsDir())
    for path in LrFileUtils.files(jobsDir()) do
        if isJobFile(path) then
            local job = readJson(path)
            local status = job and tostring(job.status) or ''
            if job and (status == 'queued' or status == 'running') then
                local imported = tonumber(job.total_imported or 0) or 0
                job.status = imported > 0 and 'partial' or 'failed'
                job.current_source = nil
                job.error = 'Erro recuperável: arquivo ou diretório indisponível. ' .. tostring(errorMessage)
                job.finished_at = os.date('!%Y-%m-%dT%H:%M:%SZ')
                job.events = job.events or {}
                table.insert(job.events, {
                    at = os.date('!%Y-%m-%dT%H:%M:%SZ'),
                    stage = 'recovered_error',
                    title = 'Job interrompido sem travar o Lightroom',
                    detail = tostring(errorMessage),
                    level = 'error',
                })
                writeJson(path, job)
                appendLog('JOB_RECOVERED path=' .. tostring(path) .. ' error=' .. tostring(errorMessage))
                return
            end
        end
    end
end

local function loadRunner()
    local ok, loaded = pcall(require, 'JobRunner')
    if not ok or type(loaded) ~= 'table' then
        appendLog('RUNNER_LOAD_FAILED error=' .. tostring(loaded))
        runner = nil
        return false
    end
    runner = loaded
    return true
end

function SafeRunner.runLoop(shouldStop)
    if not loadRunner() then
        appendLog('SAFE_RUNNER_WAITING_FOR_JOBRUNNER')
    end
    appendLog('SAFE_RUNNER_STARTED jobs=' .. jobsDir())

    while not shouldStop() do
        if not runner and not loadRunner() then
            LrTasks.sleep(2)
        else
            local ok, result = pcall(function()
                return runner.processQueuedOnce()
            end)

            if not ok then
                appendLog('RUNNER_EXCEPTION error=' .. tostring(result))
                finalizeInterruptedJob(result)
                if runner and type(runner.resetAfterFailure) == 'function' then
                    pcall(function() runner.resetAfterFailure() end)
                end
            end
            LrTasks.sleep(2)
        end
    end

    appendLog('SAFE_RUNNER_STOPPED')
end

function SafeRunner.getJobsDir()
    return jobsDir()
end

return SafeRunner