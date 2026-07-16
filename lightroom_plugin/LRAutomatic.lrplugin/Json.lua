local Json = {}

local function decodeError(text, index, message)
    error(string.format('JSON inválido na posição %d: %s', index, message))
end

local function skipWhitespace(text, index)
    while true do
        local c = string.sub(text, index, index)
        if c == ' ' or c == '\t' or c == '\r' or c == '\n' then
            index = index + 1
        else
            return index
        end
    end
end

local escapeMap = {
    ['"'] = '"', ['\\'] = '\\', ['/'] = '/',
    ['b'] = '\b', ['f'] = '\f', ['n'] = '\n', ['r'] = '\r', ['t'] = '\t'
}

local function utf8FromCodepoint(code)
    if code <= 0x7F then
        return string.char(code)
    elseif code <= 0x7FF then
        return string.char(0xC0 + math.floor(code / 0x40), 0x80 + (code % 0x40))
    elseif code <= 0xFFFF then
        return string.char(
            0xE0 + math.floor(code / 0x1000),
            0x80 + (math.floor(code / 0x40) % 0x40),
            0x80 + (code % 0x40)
        )
    end
    return string.char(
        0xF0 + math.floor(code / 0x40000),
        0x80 + (math.floor(code / 0x1000) % 0x40),
        0x80 + (math.floor(code / 0x40) % 0x40),
        0x80 + (code % 0x40)
    )
end

local parseValue

local function parseString(text, index)
    index = index + 1
    local out = {}
    while index <= #text do
        local c = string.sub(text, index, index)
        if c == '"' then
            return table.concat(out), index + 1
        elseif c == '\\' then
            local esc = string.sub(text, index + 1, index + 1)
            if esc == 'u' then
                local hex = string.sub(text, index + 2, index + 5)
                if not string.match(hex, '^%x%x%x%x$') then
                    decodeError(text, index, 'escape unicode inválido')
                end
                table.insert(out, utf8FromCodepoint(tonumber(hex, 16)))
                index = index + 6
            else
                local replacement = escapeMap[esc]
                if not replacement then decodeError(text, index, 'escape inválido') end
                table.insert(out, replacement)
                index = index + 2
            end
        else
            table.insert(out, c)
            index = index + 1
        end
    end
    decodeError(text, index, 'string não terminada')
end

local function parseNumber(text, index)
    local start = index
    local chars = '+-0123456789.eE'
    while index <= #text and string.find(chars, string.sub(text, index, index), 1, true) do
        index = index + 1
    end
    local raw = string.sub(text, start, index - 1)
    local value = tonumber(raw)
    if value == nil then decodeError(text, start, 'número inválido') end
    return value, index
end

local function parseArray(text, index)
    local result = {}
    index = skipWhitespace(text, index + 1)
    if string.sub(text, index, index) == ']' then return result, index + 1 end
    while true do
        local value
        value, index = parseValue(text, index)
        table.insert(result, value)
        index = skipWhitespace(text, index)
        local c = string.sub(text, index, index)
        if c == ']' then return result, index + 1 end
        if c ~= ',' then decodeError(text, index, 'esperado vírgula ou ]') end
        index = skipWhitespace(text, index + 1)
    end
end

local function parseObject(text, index)
    local result = {}
    index = skipWhitespace(text, index + 1)
    if string.sub(text, index, index) == '}' then return result, index + 1 end
    while true do
        if string.sub(text, index, index) ~= '"' then decodeError(text, index, 'esperada chave string') end
        local key
        key, index = parseString(text, index)
        index = skipWhitespace(text, index)
        if string.sub(text, index, index) ~= ':' then decodeError(text, index, 'esperado :') end
        index = skipWhitespace(text, index + 1)
        local value
        value, index = parseValue(text, index)
        result[key] = value
        index = skipWhitespace(text, index)
        local c = string.sub(text, index, index)
        if c == '}' then return result, index + 1 end
        if c ~= ',' then decodeError(text, index, 'esperado vírgula ou }') end
        index = skipWhitespace(text, index + 1)
    end
end

parseValue = function(text, index)
    index = skipWhitespace(text, index)
    local c = string.sub(text, index, index)
    if c == '"' then return parseString(text, index) end
    if c == '{' then return parseObject(text, index) end
    if c == '[' then return parseArray(text, index) end
    if c == '-' or string.match(c, '%d') then return parseNumber(text, index) end
    if string.sub(text, index, index + 3) == 'true' then return true, index + 4 end
    if string.sub(text, index, index + 4) == 'false' then return false, index + 5 end
    if string.sub(text, index, index + 3) == 'null' then return nil, index + 4 end
    decodeError(text, index, 'valor inesperado')
end

function Json.decode(text)
    if type(text) ~= 'string' then error('Json.decode espera string') end
    local value, index = parseValue(text, 1)
    index = skipWhitespace(text, index)
    if index <= #text then decodeError(text, index, 'conteúdo extra') end
    return value
end

local function escapeString(value)
    value = string.gsub(value, '\\', '\\\\')
    value = string.gsub(value, '"', '\\"')
    value = string.gsub(value, '\b', '\\b')
    value = string.gsub(value, '\f', '\\f')
    value = string.gsub(value, '\n', '\\n')
    value = string.gsub(value, '\r', '\\r')
    value = string.gsub(value, '\t', '\\t')
    return '"' .. value .. '"'
end

local function isArray(value)
    local max, count = 0, 0
    for key, _ in pairs(value) do
        if type(key) ~= 'number' or key < 1 or key % 1 ~= 0 then return false end
        if key > max then max = key end
        count = count + 1
    end
    return max == count
end

local encodeValue
encodeValue = function(value, seen)
    local kind = type(value)
    if kind == 'nil' then return 'null' end
    if kind == 'boolean' then return value and 'true' or 'false' end
    if kind == 'number' then
        if value ~= value or value == math.huge or value == -math.huge then error('número JSON inválido') end
        return tostring(value)
    end
    if kind == 'string' then return escapeString(value) end
    if kind ~= 'table' then error('tipo não suportado em JSON: ' .. kind) end
    if seen[value] then error('referência circular em JSON') end
    seen[value] = true
    local parts = {}
    if isArray(value) then
        for i = 1, #value do table.insert(parts, encodeValue(value[i], seen)) end
        seen[value] = nil
        return '[' .. table.concat(parts, ',') .. ']'
    end
    for key, item in pairs(value) do
        table.insert(parts, escapeString(tostring(key)) .. ':' .. encodeValue(item, seen))
    end
    table.sort(parts)
    seen[value] = nil
    return '{' .. table.concat(parts, ',') .. '}'
end

function Json.encode(value)
    return encodeValue(value, {})
end

return Json
