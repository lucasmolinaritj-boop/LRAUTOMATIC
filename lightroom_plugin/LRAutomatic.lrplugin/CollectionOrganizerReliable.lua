local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local Json = require 'Json'

local Base = require 'CollectionOrganizer'
local Reliable = {}

local function homePath()
    local home = LrPathUtils.getStandardFilePath('home')
    if home and home ~= '' then return home end
    return 'C:\\Users\\Public'
end

local function jobsDir()
    return LrPathUtils.child(
        LrPathUtils.child(
            LrPathUtils.child(
                LrPathUtils.child(homePath(), 'AppData'),
                'Local'
            ),
            'LRAutomatic'
        ),
        'jobs'
    )
end

local function isJobFile(path)
    local name = string.lower(LrPathUtils.leafName(path) or tostring(path))
    return string.sub(name, 1, 4) == 'job_' and string.sub(name, -5) == '.json'
end

local function stripBom(content)
    if content
        and string.byte(content, 1) == 239
        and string.byte(content, 2) == 187
        and string.byte(content, 3) == 191 then
        return string.sub(content, 4)
    end
    return content
end

local function readJson(path)
    local file = io.open(path, 'rb')
    if not file then return nil end
    local content = file:read('*a')
    file:close()
    local ok, decoded = pcall(Json.decode, stripBom(content or ''))
    if not ok or type(decoded) ~= 'table' then return nil end
    return decoded
end

local function writeJson(path, value)
    local ok, encoded = pcall(Json.encode, value)
    if not ok then return false end
    local temp = path .. '.collections-recovery.tmp'
    local file = io.open(temp, 'wb')
    if not file then return false end
    file:write(encoded)
    file:flush()
    file:close()
    if LrFileUtils.exists(path) then LrFileUtils.delete(path) end
    return LrFileUtils.move(temp, path) == true
end

local function collectionEnabled(job)
    local request = job and job.request or {}
    return request.organize_collections_by_photographer == true
        or request.organize_collections_by_client == true
end

local function recoverInterruptedRequests()
    LrFileUtils.createAllDirectories(jobsDir())
    for path in LrFileUtils.files(jobsDir()) do
        if isJobFile(path) then
            local job = readJson(path)
            local status = job and tostring(job.status or '') or ''
            local collectionsStatus = job and tostring(job.collections_status or '') or ''
            local terminal = status == 'completed' or status == 'partial'
            if job and terminal and collectionEnabled(job)
                and (collectionsStatus == 'requested' or collectionsStatus == 'running') then
                local changed = false
                if collectionsStatus == 'running' then
                    job.collections_status = 'requested'
                    changed = true
                end
                if tostring(job.collections_run_once_token or '') ~= tostring(job.job_id or '') then
                    job.collections_run_once_token = job.job_id
                    changed = true
                end
                if changed then writeJson(path, job) end
            end
        end
    end
end

function Reliable.processOnce()
    recoverInterruptedRequests()
    return Base.processOnce()
end

return Reliable
