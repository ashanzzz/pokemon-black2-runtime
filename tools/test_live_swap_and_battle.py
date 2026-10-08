import urllib.request, json, time, sys

def get_json(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))

def post_json(url, data=None):
    payload = json.dumps(data or {}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))

print('=== 1. Checking Live Overworld Position ===')
player = get_json('http://127.0.0.1:8765/api/v1/player/runtime')
pos = player.get('position', {}).get('grid', {})
zone = player.get('zone_id')
print('Current Zone: %s, Grid: (%s, %s, Y=%s)' % (zone, pos.get('x'), pos.get('z'), pos.get('y')))

print('\n=== 2. Triggering Wild Encounter in Grass ===')
in_battle = False
for step in range(35):
    bstate = get_json('http://127.0.0.1:8765/api/v1/battle/state')
    if bstate.get('active'):
        in_battle = True
        print('Encounter triggered at step %d!' % step)
        break
    btn = 'Left' if step % 2 == 0 else 'Right'
    post_json('http://127.0.0.1:8765/api/actions/press', {'button': btn, 'frames': 14})
    time.sleep(0.35)

if not in_battle:
    for step in range(25):
        bstate = get_json('http://127.0.0.1:8765/api/v1/battle/state')
        if bstate.get('active'):
            in_battle = True
            print('Encounter triggered during Up/Down patrol at step %d!' % step)
            break
        btn = 'Up' if step % 2 == 0 else 'Down'
        post_json('http://127.0.0.1:8765/api/actions/press', {'button': btn, 'frames': 14})
        time.sleep(0.35)

print('In battle: %s' % in_battle)
if in_battle:
    time.sleep(2.5)
    for _ in range(8):
        post_json('http://127.0.0.1:8765/api/actions/press', {'button': 'B', 'frames': 4})
        time.sleep(0.3)

    print('\n=== 3. Mapping Live Battle Entities from RAM (btl_pokeparam.c) ===')
    identity = get_json('http://127.0.0.1:8765/api/v1/battle/identity')
    opp = identity.get('opponent', {}).get('active', {})
    pl = identity.get('player', {}).get('active', {})
    print('Opponent: %s (Species #%s), Lv.%s, HP: %s/%s' % (opp.get('species', {}).get('name'), opp.get('species_id'), opp.get('level'), opp.get('current_hp'), opp.get('max_hp')))
    print('Player: %s (Species #%s), Lv.%s, HP: %s/%s' % (pl.get('species', {}).get('name'), pl.get('species_id'), pl.get('level'), pl.get('current_hp'), pl.get('max_hp')))
    move_names = [m.get('name') + ' PP:' + str(m.get('current_pp')) + '/' + str(m.get('max_pp')) for m in pl.get('moves', [])]
    print('Player Moves: %s' % move_names)

    print('\n=== 4. Testing Atomic Switch Action (POST /api/v1/battle/switch to slot 3) ===')
    status, res = post_json('http://127.0.0.1:8765/api/v1/battle/switch', {'party_slot': 3})
    print('Switch Response (HTTP %d): %s' % (status, json.dumps(res, ensure_ascii=False)))

    time.sleep(3.5)
    for _ in range(8):
        post_json('http://127.0.0.1:8765/api/actions/press', {'button': 'B', 'frames': 4})
        time.sleep(0.3)

    print('\n=== 5. Testing Atomic Flee (POST /api/v1/battle/flee) ===')
    status, flee_res = post_json('http://127.0.0.1:8765/api/v1/battle/flee')
    print('Flee Response (HTTP %d): %s' % (status, json.dumps(flee_res, ensure_ascii=False)))

    time.sleep(1.5)
    final_state = get_json('http://127.0.0.1:8765/api/v1/battle/state')
    print('Final Battle Active: %s' % final_state.get('active'))

print('\n=== Survey Complete ===')
