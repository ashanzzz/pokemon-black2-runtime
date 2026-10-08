with open('frontend/v2.js', 'r', encoding='utf-8', errors='ignore') as f:
    text = f.read()

def dump_func(name):
    pos = text.find('function ' + name)
    if pos != -1:
        end = text.find('\nfunction ', pos + 1)
        sub = text[pos:end if end != -1 else pos+3000]
        with open(f'tools/dump_{name}.txt', 'w', encoding='utf-8') as out:
            out.write(sub)
        print(f'Wrote tools/dump_{name}.txt ({len(sub)} chars)')

dump_func('renderMultiLayerGrid')
dump_func('renderTileInspection')
dump_func('handlePlanNav')
