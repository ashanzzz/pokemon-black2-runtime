with open('backend/black2/api/app.py', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if line.strip().startswith('@app.get(\ /\)') or line.strip().startswith(\@app.get / \):
        print(''.join(lines[i:i+20]))
