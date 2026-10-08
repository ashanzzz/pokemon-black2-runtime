import urllib.request
req = urllib.request.Request("http://127.0.0.1:8765/v2")
with urllib.request.urlopen(req) as resp:
    print("Status:", resp.status)
    print("Cache-Control:", resp.headers.get("Cache-Control"))
    print("Pragma:", resp.headers.get("Pragma"))