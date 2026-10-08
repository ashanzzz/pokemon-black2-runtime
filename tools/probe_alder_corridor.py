#!/usr/bin/env python3
import sys, time, json, requests
sys.stdout.reconfigure(encoding='utf-8')
BASE_URL = 'http://127.0.0.1:8765'

def get_player():
    r = requests.get(f'{BASE_URL}/api/v1/player/runtime')
    p = r.json()
    return {
        'zone_id': p.get('zone_id'),
        'grid': p.get('position', {}).get('grid') or p.get('grid'),
        'facing': p.get('orientation', {}).get('facing'),
    }

def get_dialogue():
    r = requests.get(f'{BASE_URL}/api/v1/dialogue/current')
    d = r.json()
    return {
        'active': d.get('is_active'),
        'screen': d.get('screen_type'),
        'text': d.get('text', ''),
        'loaded': d.get('raw_loaded_text', ''),
    }

def get_alder():
    r = requests.get(f'{BASE_URL}/api/v1/map/v6/actors/live')
    actors = r.json().get('actors', [])
    a = next((x for x in actors if x.get('model_id') == 97 or x.get('slot') == 0), {})
    raw = a.get('raw', {})
    return {
        'grid': a.get('position', {}).get('grid') or a.get('grid'),
        'facing': a.get('facing'),
        'face_dir_raw': raw.get('face_dir_raw'),
        'flags_raw': raw.get('flags_raw'),
        'movement_flags_raw': raw.get('movement_flags_raw'),
    }

def press(button, frames=8, delay=0.6):
    requests.post(f'{BASE_URL}/api/actions/press', json={'button': button, 'frames': frames})
    time.sleep(delay)

print('=' * 76)
print('      【实机内存探查】阿戴克 (111, 669) 左右两侧穿行与触发器现场测试')
print('=' * 76)

init_p = get_player()
init_a = get_alder()
init_d = get_dialogue()
print('初始状态:')
print('  主角位置:', init_p['grid'], '| 朝向:', init_p['facing'])
print('  阿戴克位置:', init_a['grid'], '| 朝向:', init_a['facing'], '(face_dir=' + str(init_a['face_dir_raw']) + ')')
print('  对话状态:', init_d['screen'], '| active =', init_d['active'])

print('\n' + '-' * 70)
print('>>> 阶段一：尝试从【右侧 (东侧 X=112)】向北穿过阿戴克身侧')
print('-' * 70)
right_results = []
for step_num in range(1, 6):
    press('Up', frames=8, delay=0.6)
    p = get_player()
    a = get_alder()
    d = get_dialogue()
    right_results.append({'step': step_num, 'p': p, 'a': a, 'd': d})
    print(f'  [右侧第 {step_num} 步 ↑] 主角落脚: {p["grid"]} | 对话激活: {d["active"]} | 阿戴克朝向: {a["facing"]}')
    if d['active']:
        print(f'     💥 触发剧情拦截对话！内容: {repr(d["text"][:50])}')
        for _ in range(4): press('B', frames=4, delay=0.2)

print('  --> 正在将主角退回原始起点 (112, 669)...')
for _ in range(5):
    press('Down', frames=8, delay=0.4)
time.sleep(0.3)
p_ret1 = get_player()
print('  --> 当前确认位置:', p_ret1['grid'])

print('\n' + '-' * 70)
print('>>> 阶段二：尝试从【左侧 (西侧 X=110)】向北穿过阿戴克身侧')
print('-' * 70)
press('Down', frames=8, delay=0.5)
print('  [换道] 向南退到:', get_player()['grid'])
press('Left', frames=8, delay=0.5)
print('  [换道] 向左走一步:', get_player()['grid'])
press('Left', frames=8, delay=0.5)
print('  [换道] 向左走第二步到达左侧跑道:', get_player()['grid'])

left_results = []
for step_num in range(1, 5):
    press('Up', frames=8, delay=0.6)
    p = get_player()
    a = get_alder()
    d = get_dialogue()
    left_results.append({'step': step_num, 'p': p, 'a': a, 'd': d})
    print(f'  [左侧第 {step_num} 步 ↑] 主角落脚: {p["grid"]} | 对话激活: {d["active"]} | 阿戴克朝向: {a["facing"]}')
    if d['active']:
        print(f'     💥 触发剧情拦截对话！内容: {repr(d["text"][:50])}')
        for _ in range(4): press('B', frames=4, delay=0.2)

print('\n' + '-' * 70)
print('>>> 收尾复位：将主角从左侧引导返回至 (112, 669)')
print('-' * 70)
for _ in range(4): press('Down', frames=8, delay=0.4)
press('Right', frames=8, delay=0.4)
press('Right', frames=8, delay=0.4)
press('Up', frames=8, delay=0.4)
press('Left', frames=2, delay=0.2)
final_p = get_player()
print('  最终复位主角位置:', final_p['grid'], '| 朝向:', final_p['facing'])

print('\n' + '=' * 76)
print('                     📊 现场实机测试与触发器对比结论')
print('=' * 76)