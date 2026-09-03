-- ============================================================================
-- Lightweight Pure Lua JSON Encoder/Decoder (Compatible with Lua 5.1 / LuaJIT / BizHawk)
-- ============================================================================

local json = {}

local function escape_str(s)
    local in_char  = {'\\', '"', '/', '\b', '\f', '\n', '\r', '\t'}
    local out_char = {'\\', '"', '/',  'b',  'f',  'n',  'r',  't'}
    for i, c in ipairs(in_char) do
        s = s:gsub(c, '\\' .. out_char[i])
    end
    return s
end

function json.encode(val)
    local t = type(val)
    if t == "nil" then
        return "null"
    elseif t == "boolean" then
        return val and "true" or "false"
    elseif t == "number" then
        if val ~= val then return "null" end -- NaN
        if val >= math.huge then return "1e+999" end
        if val <= -math.huge then return "-1e+999" end
        return tostring(val)
    elseif t == "string" then
        return '"' .. escape_str(val) .. '"'
    elseif t == "table" then
        -- Check if it is an array
        local is_array = true
        local max_index = 0
        local count = 0
        for k, v in pairs(val) do
            count = count + 1
            if type(k) == "number" and k > 0 and math.floor(k) == k then
                if k > max_index then max_index = k end
            else
                is_array = false
                break
            end
        end
        if count == 0 then
            return "[]"
        end
        if is_array and max_index == count then
            local parts = {}
            for i = 1, count do
                parts[i] = json.encode(val[i])
            end
            return "[" .. table.concat(parts, ",") .. "]"
        else
            local parts = {}
            for k, v in pairs(val) do
                table.insert(parts, json.encode(tostring(k)) .. ":" .. json.encode(v))
            end
            return "{" .. table.concat(parts, ",") .. "}"
        end
    else
        return "null"
    end
end

local function skip_whitespace(str, idx)
    while idx <= #str do
        local c = str:sub(idx, idx)
        if c == " " or c == "\t" or c == "\n" or c == "\r" then
            idx = idx + 1
        else
            break
        end
    end
    return idx
end

local parse_value

local function parse_string(str, idx)
    idx = idx + 1 -- Skip opening quote
    local res = {}
    while idx <= #str do
        local c = str:sub(idx, idx)
        if c == '"' then
            return table.concat(res), idx + 1
        elseif c == '\\' then
            idx = idx + 1
            local next_c = str:sub(idx, idx)
            if next_c == '"' or next_c == '\\' or next_c == '/' then
                table.insert(res, next_c)
            elseif next_c == 'b' then table.insert(res, '\b')
            elseif next_c == 'f' then table.insert(res, '\f')
            elseif next_c == 'n' then table.insert(res, '\n')
            elseif next_c == 'r' then table.insert(res, '\r')
            elseif next_c == 't' then table.insert(res, '\t')
            elseif next_c == 'u' then
                local hex = str:sub(idx + 1, idx + 4)
                idx = idx + 4
                local code = tonumber(hex, 16) or 0
                if code < 128 then
                    table.insert(res, string.char(code))
                else
                    table.insert(res, "?")
                end
            else
                table.insert(res, next_c)
            end
        else
            table.insert(res, c)
        end
        idx = idx + 1
    end
    return table.concat(res), idx
end

local function parse_number(str, idx)
    local s, e = str:find("^%-?%d+%.?%d*[eE]?[%+%-]?%d*", idx)
    if s then
        local num_str = str:sub(s, e)
        return tonumber(num_str), e + 1
    end
    return nil, idx
end

local function parse_array(str, idx)
    idx = idx + 1 -- Skip '['
    local arr = {}
    idx = skip_whitespace(str, idx)
    if str:sub(idx, idx) == ']' then
        return arr, idx + 1
    end
    while idx <= #str do
        local val
        val, idx = parse_value(str, idx)
        table.insert(arr, val)
        idx = skip_whitespace(str, idx)
        local c = str:sub(idx, idx)
        if c == ']' then
            return arr, idx + 1
        elseif c == ',' then
            idx = skip_whitespace(str, idx + 1)
        else
            break
        end
    end
    return arr, idx
end

local function parse_object(str, idx)
    idx = idx + 1 -- Skip '{'
    local obj = {}
    idx = skip_whitespace(str, idx)
    if str:sub(idx, idx) == '}' then
        return obj, idx + 1
    end
    while idx <= #str do
        idx = skip_whitespace(str, idx)
        if str:sub(idx, idx) ~= '"' then
            break
        end
        local key
        key, idx = parse_string(str, idx)
        idx = skip_whitespace(str, idx)
        if str:sub(idx, idx) == ':' then
            idx = skip_whitespace(str, idx + 1)
        end
        local val
        val, idx = parse_value(str, idx)
        obj[key] = val
        idx = skip_whitespace(str, idx)
        local c = str:sub(idx, idx)
        if c == '}' then
            return obj, idx + 1
        elseif c == ',' then
            idx = skip_whitespace(str, idx + 1)
        else
            break
        end
    end
    return obj, idx
end

function parse_value(str, idx)
    idx = skip_whitespace(str, idx)
    local c = str:sub(idx, idx)
    if c == '{' then
        return parse_object(str, idx)
    elseif c == '[' then
        return parse_array(str, idx)
    elseif c == '"' then
        return parse_string(str, idx)
    elseif c == 't' and str:sub(idx, idx + 3) == "true" then
        return true, idx + 4
    elseif c == 'f' and str:sub(idx, idx + 4) == "false" then
        return false, idx + 5
    elseif c == 'n' and str:sub(idx, idx + 3) == "null" then
        return nil, idx + 4
    else
        return parse_number(str, idx)
    end
end

function json.decode(str)
    if not str or str == "" then return nil end
    local val, _ = parse_value(str, 1)
    return val
end

return json
