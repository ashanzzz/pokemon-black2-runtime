import sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

req = urllib.request.Request("http://127.0.0.1:8765/api/v1/player/fly/regions")
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode("utf-8"))

mount = data["current_flight_status"]["flying_mount"]
print("Flying Mount:", mount)
print("Current Zone:", data["current_flight_status"]["current_zone_id"], data["current_flight_status"]["current_zone_name"])
print("Can fly now:", data["current_flight_status"]["can_fly_now"])
print("Jet badge:", data["current_flight_status"]["jet_badge_obtained"])
print("Total destinations:", data["total_destinations"])