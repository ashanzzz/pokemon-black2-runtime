import urllib.request, json

req = urllib.request.Request('http://127.0.0.1:8765/api/v1/runtime/control')
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
print('Keys in /control:', data.keys())
token = data.get('restart_token') or data.get('control', {}).get('restart_token')
print('Token:', token)

if token:
    post_req = urllib.request.Request(
        'http://127.0.0.1:8765/api/v1/runtime/restart',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'x-runtime-restart-token': token}
    )
    with urllib.request.urlopen(post_req) as resp:
        print('Restart response:', resp.read().decode('utf-8'))
