import re

with open("frontend/v2.js", "r", encoding="utf-8") as f:
    lines = f.readlines()

in_func = None
func_name = None
is_async = False
brace_depth = 0

for i, line in enumerate(lines):
    # check function definition
    m = re.search(r'(async\s+)?function\s+([a-zA-Z0-9_]+)\s*\(', line)
    if m:
        is_async = bool(m.group(1))
        func_name = m.group(2)
        brace_depth = 0
    
    brace_depth += line.count('{') - line.count('}')
    
    if not is_async and func_name and 'await ' in line:
        print(f"Line {i+1}: 'await' inside non-async function '{func_name}'!")
