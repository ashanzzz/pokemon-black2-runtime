-- ============================================================================
-- Pokémon Black 2 - BizHawk Probe Script (Layer 0 & 1 Quick Diagnostic)
-- Greenfield Architecture Specification v1.0
-- ============================================================================

console.clear()
console.log("==================================================")
console.log("   Pokémon Black 2 - BizHawk Probe v1.0.0         ")
console.log("==================================================")

-- 1. BizHawk Version & System
local version = client.getversion and client.getversion() or "unknown"
local systemId = emu.getsystemid and emu.getsystemid() or "unknown"
console.log(string.format("[BizHawk] Version: %s", tostring(version)))
console.log(string.format("[BizHawk] System ID: %s", tostring(systemId)))

-- 2. ROM Information
local romName = gameinfo.getromname and gameinfo.getromname() or "none"
local romHash = gameinfo.getromhash and gameinfo.getromhash() or "none"
local dbStatus = gameinfo.getstatus and gameinfo.getstatus() or "unknown"
local inDb = gameinfo.indatabase and gameinfo.indatabase() or false

console.log("--------------------------------------------------")
console.log(string.format("[ROM] Name: %s", tostring(romName)))
console.log(string.format("[ROM] Hash: %s", tostring(romHash)))
console.log(string.format("[ROM] DB Status: %s (In DB: %s)", tostring(dbStatus), tostring(inDb)))

-- 3. Memory Domains
console.log("--------------------------------------------------")
console.log("[Memory] Domain Discovery:")
local domains = memory.getmemorydomainlist and memory.getmemorydomainlist() or {}
local currentDomain = memory.getcurrentmemorydomain and memory.getcurrentmemorydomain() or "unknown"
local mainRamFound = false
local mainRamSize = 0

for i, dom in ipairs(domains) do
    local size = 0
    local ok, sz = pcall(function() return memory.getmemorydomainsize(dom) end)
    if ok then size = sz end
    local isCurrent = (dom == currentDomain) and " (CURRENT)" or ""
    console.log(string.format("  [%d] %-20s : %10d bytes (0x%08X)%s", i, dom, size, size, isCurrent))
    if dom == "Main RAM" or dom == "ARM9 System Bus" or dom == "System Bus" then
        mainRamFound = true
        mainRamSize = size
    end
end

-- 4. Emulator State
console.log("--------------------------------------------------")
local frame = emu.framecount and emu.framecount() or 0
local paused = client.ispaused and client.ispaused() or false
local turbo = client.isturbo and client.isturbo() or false
console.log(string.format("[State] Frame: %d | Paused: %s | Turbo: %s", frame, tostring(paused), tostring(turbo)))

-- 5. Doctor Health Evaluation
console.log("--------------------------------------------------")
console.log("[Doctor] Health Check:")
local ready = true
if systemId ~= "NDS" then
    console.log("  [WARN] System ID is not NDS (got: " .. systemId .. ")")
    ready = false
else
    console.log("  [PASS] System ID is NDS")
end

if not mainRamFound or mainRamSize < 0x400000 then
    console.log(string.format("  [WARN] Main RAM domain check failed (size: 0x%X)", mainRamSize))
    ready = false
else
    console.log(string.format("  [PASS] Main RAM domain present (0x%X / 4MB)", mainRamSize))
end

if ready then
    console.log("  >>> RESULT: DOCTOR STATUS = READY <<<")
else
    console.log("  >>> RESULT: DOCTOR STATUS = DEGRADED / UNREADY <<<")
end
console.log("==================================================")
