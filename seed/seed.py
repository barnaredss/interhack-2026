import json
import os
import sys
import firebase_admin
from firebase_admin import credentials, firestore, auth as fb_auth

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_PATH = os.path.join(SCRIPT_DIR, "service-account.json")
FAKE_DATA_PATH = os.path.join(SCRIPT_DIR, "fake_data.json")
INTERNAL_DOMAIN = "interhack.bcn"

if not os.path.exists(SERVICE_ACCOUNT_PATH):
    print("ERROR: seed/service-account.json not found.")
    print("Download it from: Firebase Console → Project Settings → Service Accounts → Generate new private key")
    sys.exit(1)

cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
firebase_admin.initialize_app(cred)
db = firestore.client()

def derive_cubes(pallets, deliveries):
    stop_by_pallet = {}
    for i, d in enumerate(deliveries):
        if i == 0: continue
        for p in d.get("pallet_positions", []):
            stop_by_pallet[f"{p['row']},{p['col']}"] = i
            
    cubes = []
    for pal in pallets:
        stop_index = stop_by_pallet.get(f"{pal['row']},{pal['col']}", 0)
        units = []
        for prod in pal.get("products", []):
            units.extend([prod["product_id"]] * prod["quantity"])
            
        for i, pid in enumerate(units[:9]):
            cubes.append({
                "x": pal["col"] * 3 + (i % 3),
                "y": pal["row"] * 3 + (i // 3),
                "z": 0,
                "stop_index": stop_index,
                "product_id": pid
            })
    return cubes

with open(FAKE_DATA_PATH) as f:
    data = json.load(f)

for driver in data["drivers"]:
    driver_id = driver["id"]
    email = f"{driver_id}@{INTERNAL_DOMAIN}"

    try:
        fb_auth.create_user(email=email, password=driver["password"])
        print(f"  [auth] Created account: {email}")
    except firebase_admin.exceptions.AlreadyExistsError:
        print(f"  [auth] Already exists:  {email}")

    cubes = derive_cubes(driver["pallets"], driver["deliveries"])
    cube_grid = {
        "L": driver["truck_layout"]["cols"] * 3,
        "W": driver["truck_layout"]["rows"] * 3,
        "H": 1
    }

    db.collection("routes").document(driver_id).set({
        "driver_id": driver_id,
        "truck_id": driver["truck_id"],
        "truck_layout": driver["truck_layout"],
        "points": driver["points"],
        "pallets": driver["pallets"],
        "deliveries": driver["deliveries"],
        "windows": driver["windows"],
        "service_times": driver["service_times"],
        "delivery_status": ["pending"] * len(driver["points"]),
        "status": "pending",
        "cubes": cubes,
        "cube_grid": cube_grid,
    })
    print(f"  [db]   Route written for {driver_id}  ({len(driver['points'])} stops)")

ADMIN_EMAIL = f"admin@{INTERNAL_DOMAIN}"
ADMIN_PASSWORD = "dammadmin2026"
try:
    fb_auth.create_user(email=ADMIN_EMAIL, password=ADMIN_PASSWORD)
    print(f"  [auth] Created admin account")
except firebase_admin.exceptions.AlreadyExistsError:
    print(f"  [auth] Admin account already exists")
print(f"  Admin login → id: admin   password: {ADMIN_PASSWORD}")

print("\nDone.")
