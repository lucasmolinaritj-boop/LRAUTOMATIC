local LrTasks = import 'LrTasks'

_G.LRAutomaticShutdown = false
_G.LRAutomaticLoopRunning = false
_G.LRAutomaticVersion = '0.2.4-lr104-compatible'
_G.LRAutomaticLastError = nil

-- Keep top-level initialization deliberately tiny. Any failure below is trapped
-- inside the async task so Lightroom keeps the plug-in enabled and its menu items
-- remain registered.
LrTasks.startAsyncTask(function()
    local okBootstrap, bootstrapError = pcall(function()
        local LrPathUtils = import 'LrPathUtils'

        -- Lightroom 10.4's Lua sandbox may not expose os.getenv. JobRunner still
        -- uses it, so provide a compatible value before loading that module.
        if not os.getenv then
            os.getenv = function(name)
                if name == 'LOCALAPPDATA' then
                    local home = LrPathUtils.getStandardFilePath('home')
                    if home and home ~= '' then
                        return LrPathUtils.child(
                            LrPathUtils.child(
                                LrPathUtils.child(home, 'AppData'),
                                'Local'
                            ),
                            ''
                        )
                    end
                end
                return nil
            end
        end

        local Runner = require 'JobRunner'
        _G.LRAutomaticLoopRunning = true

        Runner.runLoop(function()
            return _G.LRAutomaticShutdown == true
        end)
    end)

    _G.LRAutomaticLoopRunning = false
    if not okBootstrap then
        _G.LRAutomaticLastError = tostring(bootstrapError)
    end
end)
