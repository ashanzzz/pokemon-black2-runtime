-- ============================================================================
-- Pokémon Black 2 - BizHawk Greenfield Bridge v1.5.1
-- 100% Background LuaSocket TCP Bridge (Zero Window Focus Needed)
-- ============================================================================

-- This version advertises the raw, non-ROM multi-domain evidence dump API.
-- Reload this file in BizHawk's Lua Console after changing it: Lua scripts are
-- loaded into the emulator process and do not hot-reload from disk.
local BRIDGE_VERSION = "1.5.1-universal-dump"
local SOCKET_HOST = "127.0.0.1"
local SOCKET_PORT = 8766

console.clear()
console.log("==================================================")
console.log(" Pokémon Black 2 - BizHawk Greenfield Bridge      ")
console.log(" Bridge Version: " .. BRIDGE_VERSION)
console.log(" System:         " .. tostring(emu.getsystemid and emu.getsystemid() or "NDS"))
console.log(" ROM:            " .. tostring(gameinfo.getromname and gameinfo.getromname() or "none"))
console.log(" Target:         TCP " .. SOCKET_HOST .. ":" .. SOCKET_PORT)
console.log(" Mode:           100% Silent Background Direct Joypad")
console.log("==================================================")

local emu_dir = "D:\\SynologyDrive\\download\\desmume-0.9.13-win64\\BizHawk-2.11.1-win-x64"
package.cpath = package.cpath .. ";" .. emu_dir .. "\\Lua\\?.dll;" .. emu_dir .. "\\Lua\\socket\\?.dll;.\\Lua\\?.dll;.\\Lua\\socket\\?.dll;?.dll"

local socket = nil
local ok, sock_lib = pcall(require, "socket.core")
if ok and sock_lib then
    socket = sock_lib
else
    ok, sock_lib = pcall(require, "socket")
    if ok and sock_lib then
        socket = sock_lib
    end
end

-- ============================================================================
-- Embedded Pure-Lua JSON Parser & Serializer
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
        if val ~= val then return "null" end
        if val >= math.huge then return "1e+999" end
        if val <= -math.huge then return "-1e+999" end
        return tostring(val)
    elseif t == "string" then
        return '"' .. escape_str(val) .. '"'
    elseif t == "table" then
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
            return "{}"
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
    idx = idx + 1
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
    idx = idx + 1
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
    idx = idx + 1
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
    local ok, val = pcall(function() return parse_value(str, 1) end)
    if ok then return val end
    return nil
end

-- ============================================================================
-- State & Memory Functions
-- ============================================================================
local state = {
    connected = false,
    input_queue = {},
    sock = nil,
    last_retry_frame = 0,
    exit_requested = false,
    probe = nil,
    last_probe = nil,
    write_trace = nil,
    last_write_trace = nil
}

local function bytes_to_hex(byte_list)
    local hex = {}
    for i = 1, #byte_list do
        hex[i] = string.format("%02x", byte_list[i])
    end
    return table.concat(hex)
end

local function safe_read_u8(addr, domain)
    local val = 0
    if not domain or domain == "Main RAM" or domain == "ARM9 System Bus" then
        local physical_addr = addr
        if physical_addr < 0x02000000 then
            physical_addr = 0x02000000 + addr
        end
        local ok = pcall(function() val = memory.read_u8(physical_addr, "ARM9 System Bus") end)
        if not ok or val == nil then
            pcall(function() val = mainmemory.read_u8(addr) end)
        end
    else
        pcall(function() val = memory.read_u8(addr, domain) end)
    end
    return val or 0
end

local function read_binary(domain, offset, length)
    local ok, data
    if domain == "Main RAM" then
        ok, data = pcall(function() return memory.read_bytes_as_binary_string(offset, length, "Main RAM") end)
        if not ok or not data then
            ok, data = pcall(function() return mainmemory.read_bytes_as_binary_string(offset, length) end)
        end
    else
        ok, data = pcall(function() return memory.read_bytes_as_binary_string(offset, length, domain) end)
    end
    if ok and data then return data end
    return ""
end

local function binary_to_hex(data)
    local hex = {}
    for index = 1, #data do
        hex[index] = string.format("%02x", string.byte(data, index))
    end
    return table.concat(hex)
end

local function bytes_to_binary(byte_list)
    local chars = {}
    for index, value in ipairs(byte_list or {}) do
        chars[index] = string.char(value or 0)
    end
    return table.concat(chars)
end

local function binary_from_hex(value)
    if type(value) ~= "string" or #value == 0 or (#value % 2) ~= 0 then return nil end
    if value:find("[^0-9a-fA-F]") then return nil end
    return (value:gsub("..", function(pair) return string.char(tonumber(pair, 16)) end))
end

local function find_exact_patterns(payload)
    local data = read_binary(payload.domain or "Main RAM", 0, payload.size or 0x400000)
    local limit = math.max(1, math.min(16, tonumber(payload.max_matches_per_pattern) or 4))
    local matches = {}
    for _, item in ipairs(payload.patterns or {}) do
        local pattern = binary_from_hex(item.hex)
        if pattern and #pattern >= 16 then
            local cursor = 1
            local found = 0
            while cursor <= #data do
                local start = data:find(pattern, cursor, true)
                if not start then break end
                matches[#matches + 1] = {id = item.id, offset = start - 1, length = #pattern}
                found = found + 1
                if found >= limit then break end
                cursor = start + 1
            end
        end
    end
    return {matches = matches, size = #data, frame = emu.framecount and emu.framecount() or 0}
end

local function safe_screenshot(path)
    local ok, error_message = pcall(function() client.screenshot(path) end)
    if not ok then return false, "client.screenshot failed: " .. tostring(error_message) end
    return true, nil
end

local function write_binary_file(path, data)
    local file, open_error = io.open(path, "wb")
    if not file then return false, "io.open failed: " .. tostring(open_error) end
    local ok, write_error = pcall(function() file:write(data) end)
    local close_ok, close_error = pcall(function() file:close() end)
    if not ok then return false, "file write failed: " .. tostring(write_error) end
    if not close_ok then return false, "file close failed: " .. tostring(close_error) end
    return true, nil
end

local function capture_registers()
    local registers = {}
    local ok, raw = pcall(function()
        return emu.getregisters and emu.getregisters() or nil
    end)
    if not ok or type(raw) ~= "table" then
        return registers
    end
    -- Preserve only JSON scalar values.  The raw memory files remain the
    -- authority; this is an optional, emulator-provided register annotation.
    for key, value in pairs(raw) do
        if type(key) == "string" and (type(value) == "number" or type(value) == "string" or type(value) == "boolean") then
            registers[key] = value
        end
    end
    return registers
end

local function dump_universal_memory(payload, current_frame)
    local dump_dir = payload.dump_dir
    local domains = payload.domains or {}
    local results = {}
    local all_domains_ok = true

    if type(dump_dir) ~= "string" or dump_dir == "" then
        return nil, "dump_dir is required"
    end

    for _, spec in ipairs(domains) do
        local name = spec.name
        local file_name = spec.file
        local expected_size = tonumber(spec.size) or 0
        local result = { size = 0, success = false }
        results[name or tostring(#results + 1)] = result

        if type(name) ~= "string" or type(file_name) ~= "string" or expected_size <= 0 then
            result.error = "invalid domain specification"
            all_domains_ok = false
        else
            local bytes = read_binary(name, 0, expected_size)
            result.size = #bytes
            if #bytes ~= expected_size then
                result.error = "read size mismatch: expected " .. expected_size .. ", got " .. #bytes
                all_domains_ok = false
            else
                local wrote, write_error = write_binary_file(dump_dir .. "/" .. file_name, bytes)
                if wrote then
                    result.success = true
                else
                    result.error = write_error
                    all_domains_ok = false
                end
            end
        end
    end

    local screenshot_saved, screenshot_error = safe_screenshot(payload.png_path)
    return {
        frame = current_frame,
        domains = results,
        domains_complete = all_domains_ok,
        screenshot_saved = screenshot_saved,
        screenshot_error = screenshot_error,
        registers = capture_registers(),
    }, nil
end

-- LuaSocket may return a partial write for large memory responses.
local function send_all(sock, data)
    local next_index = 1
    local stalled_writes = 0
    while next_index <= #data do
        local sent, err, last_index = sock:send(data, next_index)
        if sent and sent > 0 then
            next_index = next_index + sent
            stalled_writes = 0
        elseif err == "timeout" and last_index and last_index >= next_index then
            next_index = last_index + 1
            stalled_writes = 0
        else
            stalled_writes = stalled_writes + 1
            if err ~= "timeout" or stalled_writes > 1000 then
                return false, err or "socket send failed"
            end
            if socket.sleep then socket.sleep(0.001) end
        end
    end
    return true
end

local function scan_loaded_headers(payload)
    local data = read_binary(payload.domain or "Main RAM", 0, payload.size or 0x400000)
    local patterns = payload.patterns or {"BMD0", "BTX0"}
    local matches = {}
    for _, pattern in ipairs(patterns) do
        local cursor = 1
        while cursor <= #data do
            local start = data:find(pattern, cursor, true)
            if not start then break end
            matches[#matches + 1] = {
                kind = pattern,
                offset = start - 1,
                prefix_hex = binary_to_hex(data:sub(start, start + 15))
            }
            cursor = start + 1
            if #matches >= 2000 then break end
        end
    end
    return {matches = matches, size = #data, frame = emu.framecount and emu.framecount() or 0}
end

local function scan_byte_pattern(payload)
    local domain = payload.domain or "Main RAM"
    local start = payload.start or payload.offset or 0
    local size = math.min(payload.size or 0x400000, 0x400000)
    local pattern = payload.bytes or {}
    local pattern_data = bytes_to_binary(pattern)
    local data = read_binary(domain, start, size)
    local matches = {}
    local cursor = 1
    local limit = math.min(payload.limit or 64, 256)

    if #pattern_data == 0 or #data < #pattern_data then
        return {matches = matches, start = start, size = #data, frame = emu.framecount and emu.framecount() or 0}
    end

    while cursor <= #data and #matches < limit do
        local found = data:find(pattern_data, cursor, true)
        if not found then break end
        matches[#matches + 1] = start + found - 1
        cursor = found + 1
    end

    return {
        matches = matches,
        pattern = pattern,
        start = start,
        size = #data,
        frame = emu.framecount and emu.framecount() or 0
    }
end

local function get_bizhawk_info()
    return {
        bizhawk_version = client.getversion and client.getversion() or "2.11.1",
        system_id = emu.getsystemid and emu.getsystemid() or "NDS"
    }
end

local function get_rom_info()
    return {
        rom_name = gameinfo.getromname and gameinfo.getromname() or "口袋妖怪黑2",
        rom_hash = gameinfo.getromhash and gameinfo.getromhash() or "none",
        database_status = gameinfo.getstatus and gameinfo.getstatus() or "unknown",
        in_database = gameinfo.indatabase and gameinfo.indatabase() or false
    }
end

local function get_memory_domains()
    local domain_list = memory.getmemorydomainlist and memory.getmemorydomainlist() or {}
    local result = {}
    for _, name in ipairs(domain_list) do
        local sz = 0
        local ok, s = pcall(function() return memory.getmemorydomainsize(name) end)
        if ok then sz = s end
        result[name] = { name = name, size = sz, readable = true, writable = true }
    end
    result["Main RAM"] = { name = "Main RAM", size = 0x400000, readable = true, writable = true }
    return result
end

local function get_capabilities()
    return {
        memory_domains = true,
        read_batch = true,
        scan_headers = true,
        hash = true,
        touch = true,
        pause_resume = true,
        frame_advance = true,
        savestate = true,
        universal_dump = true,
        watch_write = false,
        a_edge_capture = true,
        -- The bridge advertises no successful writer-PC trace until the live
        -- trace-capabilities probe confirms its ARM9 scope and PC register.
        write_pc_trace = false
    }
end

local function capture_probe_ranges(ranges)
    local results = {}
    for index, r in ipairs(ranges or {}) do
        local dom = r.domain or "Main RAM"
        local addr = r.offset or r.addr or 0
        local len = r.length or r.size or 1
        local bytes = {}
        for byte_index = 0, len - 1 do
            bytes[byte_index + 1] = safe_read_u8(addr + byte_index, dom)
        end
        results[#results + 1] = {
            id = r.id or tostring(index), domain = dom, offset = addr, length = len,
            hex = bytes_to_hex(bytes), bytes = bytes
        }
    end
    return results
end

-- ============================================================================
-- Bounded ARM9 memory-write PC trace
--
-- This is deliberately a short-lived RE primitive.  It never changes memory:
-- it registers at most 16 *address-specific* callbacks inside one explicit
-- ARM9 RAM span, records only matching writes, and unregisters itself after a
-- fixed frame or event budget.  It intentionally does not install a global
-- bus-write callback: that would run for every emulated write and can distort
-- the timing under investigation.
-- ============================================================================
local function normalize_arm9_trace_address(value)
    local address = tonumber(value)
    if not address then return nil end
    if address >= 0 and address < 0x02000000 then
        return 0x02000000 + address
    end
    return address
end

local function discover_write_trace_capabilities()
    local capability = {
        callback_params = false,
        scopes = {},
        arm9_scope = nil,
        registers = {},
        register_map = {},
        arm9_pc_register = nil,
        errors = {}
    }

    if not event or not event.on_bus_write or not event.availableScopes or not event.can_use_callback_params then
        capability.errors[#capability.errors + 1] = "required BizHawk event API is unavailable"
        return capability
    end

    local params_ok, params = pcall(function() return event.can_use_callback_params("memory") end)
    capability.callback_params = params_ok and params == true
    if not capability.callback_params then
        capability.errors[#capability.errors + 1] = "memory callback parameters are unavailable"
    end

    local scopes_ok, raw_scopes = pcall(function() return event.availableScopes() end)
    if scopes_ok and type(raw_scopes) == "table" then
        -- BizHawk documents this as a zero-indexed array: pairs preserves its
        -- values regardless of whether the Lua table begins at 0 or 1.
        for _, scope in pairs(raw_scopes) do
            if type(scope) == "string" then
                capability.scopes[#capability.scopes + 1] = scope
                if not capability.arm9_scope and string.find(string.lower(scope), "arm9", 1, true) then
                    capability.arm9_scope = scope
                end
            end
        end
    else
        capability.errors[#capability.errors + 1] = "event.availableScopes failed"
    end
    if not capability.arm9_scope then
        capability.errors[#capability.errors + 1] = "no explicit ARM9 bus scope was reported"
    end

    local by_upper = {}
    if emu and emu.getregisters then
        local registers_ok, raw_registers = pcall(function() return emu.getregisters() end)
        if registers_ok and type(raw_registers) == "table" then
            for name, _ in pairs(raw_registers) do
                if type(name) == "string" then
                    capability.registers[#capability.registers + 1] = name
                    by_upper[string.upper(name)] = name
                end
            end
        else
            capability.errors[#capability.errors + 1] = "emu.getregisters failed"
        end
    else
        capability.errors[#capability.errors + 1] = "emu.getregisters is unavailable"
    end

    local aliases = {
        PC = {"PC", "R15"},
        LR = {"LR", "R14"},
        SP = {"SP", "R13"},
        R0 = {"R0"}, R1 = {"R1"}, R2 = {"R2"}, R3 = {"R3"},
        CPSR = {"CPSR"}
    }
    for canonical, candidates in pairs(aliases) do
        for _, candidate in ipairs(candidates) do
            if by_upper[candidate] then
                capability.register_map[canonical] = by_upper[candidate]
                break
            end
        end
    end
    capability.arm9_pc_register = capability.register_map.PC
    if not capability.arm9_pc_register then
        capability.errors[#capability.errors + 1] = "no PC/R15 register name was reported for the ARM9-scoped callback"
    end
    return capability
end

local function trace_register_snapshot(register_map)
    local snapshot = {}
    for canonical, register_name in pairs(register_map or {}) do
        local value = nil
        if emu and emu.getregister then
            local ok, read_value = pcall(function() return emu.getregister(register_name) end)
            if ok then value = read_value end
        end
        if type(value) == "number" then snapshot[canonical] = value end
    end
    return snapshot
end

local function unregister_write_trace_callback(trace)
    if trace and event and event.unregisterbyid then
        for _, callback_id in ipairs(trace.callback_ids or {}) do
            pcall(function() event.unregisterbyid(callback_id) end)
        end
        trace.callback_ids = {}
    end
end

local function finish_write_trace(reason)
    local trace = state.write_trace
    if not trace then return nil end
    trace.callback_count = #trace.callback_ids
    unregister_write_trace_callback(trace)
    local completed_frame = emu.framecount and emu.framecount() or 0
    state.last_write_trace = {
        active = false,
        complete = true,
        stop_reason = reason or trace.stop_reason or "completed",
        started_frame = trace.started_frame,
        press_frame = trace.press_frame,
        completed_frame = completed_frame,
        frames_captured = trace.frames_captured,
        max_frames = trace.max_frames,
        max_events = trace.max_events,
        button = trace.button,
        target = {
            start_addr = trace.start_addr,
            end_addr_exclusive = trace.end_addr,
            length = trace.length
        },
        callback = {
            api = "event.on_bus_write",
            registered_scope = trace.registered_scope,
            registration_names = trace.registration_names,
            watched_addresses = trace.addresses,
            callback_count = trace.callback_count
        },
        register_map = trace.register_map,
        initial_registers = trace.initial_registers,
        final_registers = trace_register_snapshot(trace.register_map),
        events = trace.events,
        samples = trace.samples,
        registration_errors = trace.registration_errors
    }
    state.write_trace = nil
    return state.last_write_trace
end

local function arm_write_trace(payload, current_frame)
    local start_addr = normalize_arm9_trace_address(payload.start_addr)
    local length = math.floor(tonumber(payload.length) or 0)
    local max_frames = math.max(1, math.min(3, math.floor(tonumber(payload.max_frames) or 3)))
    local max_events = math.max(1, math.min(64, math.floor(tonumber(payload.max_events) or 32)))
    if not start_addr or start_addr < 0x02000000 or length < 1 or start_addr + length > 0x02400000 then
        return nil, "invalid ARM9 Main-RAM trace range"
    end
    if state.probe or state.write_trace or #state.input_queue > 0 then
        return nil, "another probe, write trace, or queued input is active"
    end
    local capability = discover_write_trace_capabilities()
    if not capability.callback_params or not capability.arm9_scope or not capability.arm9_pc_register then
        return nil, "write trace is unsupported: " .. table.concat(capability.errors, "; ")
    end

    local addresses, seen = {}, {}
    for _, raw_addr in ipairs(payload.addresses or {}) do
        local address = normalize_arm9_trace_address(raw_addr)
        if not address or address < start_addr or address >= start_addr + length then
            return nil, "watch address is outside the explicit trace range"
        end
        if not seen[address] then
            seen[address] = true
            addresses[#addresses + 1] = address
        end
    end
    if #addresses < 1 or #addresses > 16 then
        return nil, "trace requires 1..16 distinct address-specific watches"
    end

    local trace = {
        start_addr = start_addr,
        end_addr = start_addr + length,
        length = length,
        max_frames = max_frames,
        max_events = max_events,
        button = payload.button,
        -- The first sampled ``after_frame`` must be after an emulated frame,
        -- not a duplicate of the command-handler baseline.
        phase = payload.button and "press" or "passive_arm",
        started_frame = current_frame,
        press_frame = nil,
        frames_captured = 0,
        events = {},
        ranges = payload.ranges or {},
        samples = {{
            phase = payload.button and "before_edge" or "before_capture",
            frame = current_frame,
            ranges = capture_probe_ranges(payload.ranges or {})
        }},
        addresses = addresses,
        address_set = seen,
        registered_scope = capability.arm9_scope,
        registration_names = {},
        callback_ids = {},
        register_map = capability.register_map,
        registration_errors = {},
        initial_registers = trace_register_snapshot(capability.register_map),
        disabled = false,
        stop_reason = nil
    }

    local function callback(addr, value, flags)
        local active = state.write_trace
        if active ~= trace or trace.disabled then return end
        local arm9_addr = normalize_arm9_trace_address(addr)
        if not arm9_addr or not trace.address_set[arm9_addr] then return end

        local registers = trace_register_snapshot(trace.register_map)
        trace.events[#trace.events + 1] = {
            frame = emu.framecount and emu.framecount() or 0,
            address = arm9_addr,
            address_hex = string.format("0x%08X", arm9_addr),
            value = type(value) == "number" and value or nil,
            flags = flags == nil and nil or tostring(flags),
            writer_pc = registers.PC or registers.R15,
            registers = registers
        }
        if #trace.events >= trace.max_events then
            -- Do not unregister from inside a bus callback.  The next Lua-loop
            -- iteration finalizes it; the cheap ``disabled`` guard prevents
            -- more register snapshots during the remainder of this frame.
            trace.disabled = true
            trace.stop_reason = "event_limit"
        end
    end

    for index, address in ipairs(addresses) do
        local registration_name = "black2_write_trace_" .. tostring(current_frame) .. "_" .. tostring(index)
        local ok, callback_id = pcall(function()
            return event.on_bus_write(callback, address, registration_name, capability.arm9_scope)
        end)
        if ok and callback_id then
            trace.callback_ids[#trace.callback_ids + 1] = callback_id
            trace.registration_names[#trace.registration_names + 1] = registration_name
        else
            trace.registration_errors[#trace.registration_errors + 1] = {
                address = address,
                error = ok and "callback registration returned no id" or tostring(callback_id)
            }
            unregister_write_trace_callback(trace)
            return nil, "unable to register address-specific write callback"
        end
    end
    state.write_trace = trace
    state.last_write_trace = nil
    return trace, nil
end

local function create_hello_payload()
    return {
        type = "hello",
        bridge_version = BRIDGE_VERSION,
        bizhawk = get_bizhawk_info(),
        game = get_rom_info(),
        memory = get_memory_domains(),
        capabilities = get_capabilities(),
        frame = emu.framecount and emu.framecount() or 0
    }
end

-- ============================================================================
-- Command Handler
-- ============================================================================
local function handle_command(cmd)
    local op = cmd.op
    local payload = cmd.payload or {}
    local req_id = cmd.id
    local current_frame = emu.framecount and emu.framecount() or 0

    local resp = {
        v = 1,
        id = req_id,
        type = "response",
        ok = true,
        frame = current_frame,
        payload = {}
    }

    if op == "bridge.hello" then
        resp.payload = create_hello_payload()

    elseif op == "bridge.ping" then
        resp.payload = { pong = true, version = BRIDGE_VERSION, timestamp = payload.timestamp or 0, frame = current_frame }

    elseif op == "bridge.capabilities" then
        resp.payload = get_capabilities()

    elseif op == "bridge.trace_capabilities" then
        -- This is a live API probe, not an assertion that write tracing works
        -- on the loaded core.  The caller must inspect its explicit ARM9
        -- scope and PC register before arming any callback.
        resp.payload = discover_write_trace_capabilities()

    elseif op == "emu.state" then
        resp.payload = {
            frame = current_frame,
            paused = client.ispaused and client.ispaused() or false,
            turbo = client.isturbo and client.isturbo() or false
        }

    elseif op == "emu.pause" then
        if client.pause then client.pause() end
        resp.payload = { paused = true }

    elseif op == "emu.resume" then
        if client.unpause then client.unpause() end
        resp.payload = { paused = false }

    elseif op == "emu.frame_advance" then
        local count = payload.frames or 1
        for _ = 1, count do emu.frameadvance() end
        resp.frame = emu.framecount()
        resp.payload = { advanced = count, frame = resp.frame }

    elseif op == "emu.exit" then
        state.exit_requested = true
        resp.payload = { closing = true }

    elseif op == "game.info" then
        resp.payload = get_rom_info()

    elseif op == "memory.domains" then
        resp.payload = get_memory_domains()

    elseif op == "memory.dump_universal" then
        console.log("[Bridge][memory.dump_universal] frame=" .. tostring(current_frame) .. " requested_domains=" .. tostring(#(payload.domains or {})))
        local dumped, dump_error = dump_universal_memory(payload, current_frame)
        if not dumped then
            resp.ok = false
            resp.error = dump_error
            console.log("[Bridge][memory.dump_universal][ERROR] " .. tostring(dump_error))
        else
            resp.payload = dumped
            console.log(
                "[Bridge][memory.dump_universal] complete=" .. tostring(dumped.domains_complete)
                .. " screenshot=" .. tostring(dumped.screenshot_saved)
            )
        end

    elseif op == "memory.scan_headers" then
        resp.payload = scan_loaded_headers(payload)

    elseif op == "memory.scan_pattern" then
        resp.payload = scan_byte_pattern(payload)

    elseif op == "memory.find_patterns" then
        resp.payload = find_exact_patterns(payload)

    elseif op == "memory.read" then
        local domain = payload.domain or "Main RAM"
        local addr = payload.addr or payload.offset or 0
        local size = payload.size or payload.length or 1
        local format = payload.format or "u8"

        local bytes = {}
        for i = 0, size - 1 do
            local val = safe_read_u8(addr + i, domain)
            table.insert(bytes, val)
        end

        local val_scalar = nil
        if format == "u8" or size == 1 then
            val_scalar = bytes[1]
        elseif format == "u16" or size == 2 then
            val_scalar = (bytes[1] or 0) + ((bytes[2] or 0) * 256)
        elseif format == "u32" or size == 4 then
            val_scalar = (bytes[1] or 0) + ((bytes[2] or 0) * 256) + ((bytes[3] or 0) * 65536) + ((bytes[4] or 0) * 16777216)
        end

        resp.payload = {
            domain = domain,
            addr = addr,
            size = size,
            value = val_scalar,
            hex = bytes_to_hex(bytes),
            bytes = bytes
        }

    elseif op == "memory.read_batch" then
        local ranges = payload.ranges or {}
        local results = {}
        for idx, r in ipairs(ranges) do
            local dom = r.domain or "Main RAM"
            local addr = r.offset or r.addr or 0
            local len = r.length or r.size or 1
            local tag = r.id or r.tag or tostring(idx)

            local bytes = {}
            for i = 0, len - 1 do
                bytes[i + 1] = safe_read_u8(addr + i, dom)
            end

            results[tag] = {
                id = tag,
                domain = dom,
                offset = addr,
                length = len,
                hex = bytes_to_hex(bytes),
                bytes = bytes
            }
        end
        resp.payload = {
            frame = current_frame,
            results = results
        }

    elseif op == "probe.a_edge_begin" then
        if (state.probe and not state.probe.complete) or state.write_trace then
            resp.ok = false
            resp.error = "another probe or write trace is already active"
        else
            local sample_frames = math.max(1, math.min(120, tonumber(payload.sample_frames) or 16))
            state.probe = {
                button = payload.button or "A",
                ranges = payload.ranges or {},
                remaining = sample_frames,
                phase = "press",
                complete = false,
                samples = {{ phase = "before_edge", frame = current_frame, ranges = capture_probe_ranges(payload.ranges or {}) }}
            }
            state.last_probe = nil
            resp.payload = { started = true, button = state.probe.button, sample_frames = sample_frames, frame = current_frame }
        end

    elseif op == "probe.a_edge_status" then
        if state.probe then
            resp.payload = { active = true, complete = false, captured = #state.probe.samples, remaining = state.probe.remaining }
        else
            resp.payload = state.last_probe or { active = false, complete = false }
        end

    elseif op == "probe.write_trace_begin" then
        local trace, trace_error = arm_write_trace(payload, current_frame)
        if not trace then
            resp.ok = false
            resp.error = trace_error or "write trace could not be armed"
        else
            resp.payload = {
                started = true,
                frame = current_frame,
                button = trace.button,
                target = {
                    start_addr = trace.start_addr,
                    end_addr_exclusive = trace.end_addr,
                    length = trace.length
                },
                addresses = trace.addresses,
                max_frames = trace.max_frames,
                max_events = trace.max_events,
                registered_scope = trace.registered_scope
            }
        end

    elseif op == "probe.write_trace_status" then
        if state.write_trace then
            local trace = state.write_trace
            resp.payload = {
                active = true,
                complete = false,
                frame = current_frame,
                frames_captured = trace.frames_captured,
                remaining_frames = trace.max_frames - trace.frames_captured,
                events_captured = #trace.events,
                max_events = trace.max_events,
                target = {
                    start_addr = trace.start_addr,
                    end_addr_exclusive = trace.end_addr,
                    length = trace.length
                },
                addresses = trace.addresses,
                registered_scope = trace.registered_scope
            }
        else
            resp.payload = state.last_write_trace or { active = false, complete = false }
        end

    elseif op == "probe.write_trace_cancel" then
        if state.write_trace then
            local completed = finish_write_trace("cancelled")
            resp.payload = { cancelled = true, completed_frame = completed and completed.completed_frame or current_frame }
        else
            resp.payload = { cancelled = false, reason = "no active write trace" }
        end

    elseif op == "memory.write" then
        local domain = payload.domain or "Main RAM"
        local addr = payload.addr or payload.offset or 0
        local bytes = payload.bytes or {}
        if payload.value ~= nil and #bytes == 0 then
            bytes = { payload.value }
        end
        for i, val in ipairs(bytes) do
            local target_offset = addr + i - 1
            local physical_addr = target_offset
            if physical_addr < 0x02000000 then physical_addr = 0x02000000 + physical_addr end
            pcall(function() memory.write_u8(physical_addr, val, "ARM9 System Bus") end)
            pcall(function() mainmemory.write_u8(target_offset, val) end)
        end
        resp.payload = {
            written = #bytes,
            addr = addr
        }

    elseif op == "input.state" then
        resp.payload = {
            joypad = joypad.get and joypad.get(1) or {},
            queue_len = #state.input_queue
        }

    elseif op == "input.press" then
        local buttons = payload.buttons or {}
        if type(buttons) == "string" then buttons = { buttons } end
        local frames = payload.frames or 8
        table.insert(state.input_queue, {
            buttons = buttons,
            touch = payload.touch,
            remaining_frames = frames
        })
        resp.payload = { queued = true, frames = frames, buttons = buttons }

    elseif op == "input.touch" then
        local x = payload.x or 128
        local y = payload.y or 96
        local frames = payload.frames or 8
        table.insert(state.input_queue, {
            buttons = {},
            touch = { x = x, y = y },
            remaining_frames = frames
        })
        resp.payload = { queued = true, x = x, y = y, frames = frames }

    elseif op == "input.clear" then
        state.input_queue = {}
        pcall(function() joypad.set({}, 1) end)
        resp.payload = { cleared = true }

    elseif op == "screen.capture" then
        local ok, error_message = safe_screenshot(payload.path)
        if ok then
            resp.payload = {path = payload.path}
        else
            resp.ok = false
            resp.error = error_message
        end

    elseif op == "savestate.save" then
        local slot = payload.slot or 1
        savestate.saveslot(slot)
        resp.payload = { slot = slot, saved = true }

    elseif op == "savestate.load" then
        local slot = payload.slot or 1
        savestate.loadslot(slot)
        resp.payload = { slot = slot, loaded = true }

    else
        resp.ok = false
        resp.error = "Unknown operation: " .. tostring(op)
    end

    return resp
end

-- ============================================================================
-- Input Application (100% Background direct core injection)
-- ============================================================================
local function apply_current_inputs()
    if state.probe and state.probe.phase == "press" then
        local pad = {}
        pad[state.probe.button] = true
        pcall(function() joypad.set(pad) end)
        pcall(function() joypad.set(pad, 1) end)
        state.probe.phase = "capture"
        return
    end
    if state.write_trace and state.write_trace.phase == "press" then
        local trace = state.write_trace
        local pad = {}
        pad[trace.button] = true
        pcall(function() joypad.set(pad) end)
        pcall(function() joypad.set(pad, 1) end)
        trace.phase = "capture"
        trace.press_frame = emu.framecount and emu.framecount() or 0
        return
    end
    if state.write_trace and state.write_trace.phase == "passive_arm" then
        state.write_trace.phase = "capture"
    end
    if #state.input_queue > 0 then
        local item = state.input_queue[1]
        local pad = {}
        for _, btn in ipairs(item.buttons or {}) do
            pad[btn] = true
        end
        if item.touch then
            pad["Touch"] = true
            pad["Touch X"] = math.floor(item.touch.x)
            pad["Touch Y"] = math.floor(item.touch.y)
        end
        
        pcall(function() joypad.set(pad) end)
        pcall(function() joypad.set(pad, 1) end)
        
        if item.touch and joypad.setanalog then
            pcall(function()
                joypad.setanalog({["Touch X"] = math.floor(item.touch.x), ["Touch Y"] = math.floor(item.touch.y)}, 1)
            end)
        end
        
        item.remaining_frames = item.remaining_frames - 1
        if item.remaining_frames <= 0 then
            table.remove(state.input_queue, 1)
        end
    else
        pcall(function() joypad.set({}, 1) end)
    end
end

local function advance_probe()
    if not state.probe or state.probe.phase ~= "capture" then return end
    local current_frame = emu.framecount and emu.framecount() or 0
    state.probe.samples[#state.probe.samples + 1] = {
        phase = "after_frame_" .. tostring(#state.probe.samples),
        frame = current_frame,
        ranges = capture_probe_ranges(state.probe.ranges)
    }
    state.probe.remaining = state.probe.remaining - 1
    if state.probe.remaining <= 0 then
        state.probe.complete = true
        state.last_probe = { active = false, complete = true, samples = state.probe.samples }
        state.probe = nil
    end
end

local function advance_write_trace()
    local trace = state.write_trace
    if not trace or trace.phase ~= "capture" then return end

    local current_frame = emu.framecount and emu.framecount() or 0
    trace.frames_captured = trace.frames_captured + 1
    trace.samples[#trace.samples + 1] = {
        phase = "after_frame_" .. tostring(trace.frames_captured),
        frame = current_frame,
        ranges = capture_probe_ranges(trace.sample_ranges or trace.ranges or {})
    }

    if trace.disabled then
        finish_write_trace(trace.stop_reason or "event_limit")
    elseif trace.frames_captured >= trace.max_frames then
        finish_write_trace("frame_limit")
    end
end

-- ============================================================================
-- TCP Socket Connection & IO
-- ============================================================================
local function connect_socket()
    if not socket then return false end
    local s, err = socket.tcp()
    if not s then return false end
    s:settimeout(1)
    local ok, conn_err = s:connect(SOCKET_HOST, SOCKET_PORT)
    if ok or conn_err == "already connected" then
        s:settimeout(0.001)
        state.sock = s
        state.connected = true
        console.log("[Bridge] Connected to Semantic Runtime TCP server!")
        
        local hello_str = json.encode(create_hello_payload()) .. "\n"
        local sent, send_err = send_all(s, hello_str)
        if not sent then
            s:close()
            state.sock = nil
            state.connected = false
            return false
        end
        return true
    end
    return false
end

local function socket_process_events()
    local cur_frame = emu.framecount and emu.framecount() or 0
    if not state.sock or not state.connected then
        if cur_frame - state.last_retry_frame > 20 or cur_frame < state.last_retry_frame then
            state.last_retry_frame = cur_frame
            connect_socket()
        end
        return
    end

    -- Send periodic heartbeat every 20 frames with version
    if cur_frame % 20 == 0 or cur_frame ~= state.last_heartbeat_frame then
        state.last_heartbeat_frame = cur_frame
        local ok, send_err = pcall(function()
            local ping_str = json.encode({type = "heartbeat", version = BRIDGE_VERSION, frame = cur_frame}) .. "\n"
            local res, err = send_all(state.sock, ping_str)
            if not res then
                if state.sock then pcall(function() state.sock:close() end) end
                state.sock = nil
                state.connected = false
            end
        end)
        if not ok then
            if state.sock then pcall(function() state.sock:close() end) end
            state.sock = nil
            state.connected = false
            return
        end
    end

    -- Receive non-blocking
    while state.sock do
        local chunk, err, partial = state.sock:receive("*l")
        local line = chunk
        if not line and partial and partial ~= "" and err ~= "timeout" then
            line = partial
        end

        if line and line ~= "" then
            local decoded, msg = pcall(json.decode, line)
            if not decoded then msg = nil end
            if msg and msg.type == "request" and msg.op then
                local handled, resp = pcall(handle_command, msg)
                if not handled then
                    resp = {
                        v = 1,
                        id = msg.id,
                        type = "response",
                        ok = false,
                        error = tostring(resp),
                        payload = {}
                    }
                end
                local resp_str = json.encode(resp) .. "\n"
                local res, send_err = send_all(state.sock, resp_str)
                if not res then
                    state.sock = nil
                    state.connected = false
                    break
                end
            end
        else
            if err == "closed" then
                console.log("[Bridge] Connection closed, will retry...")
                state.sock = nil
                state.connected = false
            end
            break
        end
    end
end

-- ============================================================================
-- Main Execution Loop: Process Network -> Apply Inputs -> Frame Advance
-- ============================================================================
if event and event.onexit then
    pcall(function()
        event.onexit(function()
            -- A script reload/close must never leave address-specific watches
            -- registered in the Lua Console.
            if state.write_trace then finish_write_trace("bridge_exit") end
        end, "black2_write_trace_cleanup")
    end)
end

connect_socket()

while true do
    socket_process_events()
    advance_probe()
    advance_write_trace()
    apply_current_inputs()
    if state.exit_requested then
        pcall(function() client.exit() end)
        break
    end
    emu.frameadvance()
end
