-- Ponto único e estável de entrada do motor LRAutomatic.
--
-- O Init.lua não conhece mais números de versões internas. As camadas antigas
-- continuam disponíveis temporariamente para rollback, mas toda inicialização
-- passa somente por este módulo. Não criar novos JobRunnerNN a partir daqui.
local Runner = require 'JobRunner58'

Runner.engine_name = 'JobRunner'
Runner.engine_version = '4.10.0-unified-entrypoint-lr104'

return Runner
