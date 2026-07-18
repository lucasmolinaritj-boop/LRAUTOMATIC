-- Compatibilidade com instalações antigas do plugin.
-- Todas as entradas devem usar a mesma instância do runner atual para impedir
-- dois loops concorrentes processando a mesma fila e para evitar o antigo
-- pcall em torno de operações do SDK que fazem yield no Lightroom 10.4.
return require 'JobRunner46'
