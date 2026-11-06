from flask import Flask, request, jsonify
import sqlite3
import requests
import os
import time
import math
import threading
import hmac

app = Flask(__name__)


def clean_number(value):
    return value.strip().replace("+", "").replace(" ", "")


def read_float(name):
    value = os.environ.get(name, "").strip()
    return float(value) if value else None


VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "").strip()
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "").strip()
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "").strip()
OWNER_NUMBER = clean_number(os.environ.get("OWNER_NUMBER", ""))
FAMILY_NUMBERS = [clean_number(n) for n in os.environ.get("FAMILY_NUMBERS", "").split(",") if n.strip()]
OWNTRACKS_KEY = os.environ.get("OWNTRACKS_KEY", "").strip()
DB_PATH = os.environ.get("DB_PATH", "location.db")

HOME_LAT = read_float("HOME_LAT")
HOME_LON = read_float("HOME_LON")
RADIUS = read_float("RADIUS") or 80.0
MAX_ACCURACY = 100  # isse kharab accuracy wali location se state nahi badlegi

connection = sqlite3.connect(DB_PATH, check_same_thread=False)
db_lock = threading.Lock()
connection.execute("CREATE TABLE IF NOT EXISTS status (id INTEGER PRIMARY KEY, latitude REAL, longitude REAL, updated_at INTEGER, state TEXT, sharing INTEGER)")
connection.execute("INSERT OR IGNORE INTO status VALUES (1, NULL, NULL, NULL, NULL, 1)")
connection.commit()


def get_status():
    with db_lock:
        row = connection.execute("SELECT latitude, longitude, updated_at, state, sharing FROM status WHERE id = 1").fetchone()
    return {"lat": row[0], "lon": row[1], "time": row[2], "state": row[3], "sharing": row[4]}


def save_status(lat, lon, ts, state):
    with db_lock:
        connection.execute("UPDATE status SET latitude = ?, longitude = ?, updated_at = ?, state = ? WHERE id = 1", (lat, lon, ts, state))
        connection.commit()


def set_sharing(on):
    with db_lock:
        connection.execute("UPDATE status SET sharing = ? WHERE id = 1", (1 if on else 0,))
        connection.commit()


def distance_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def next_state(old_state, distance):
    # Nikalne ke liye RADIUS, wapas aane ke liye RADIUS ka 80 percent (GPS hilne se baar baar alert na aaye)
    if old_state == "home":
        return "away" if distance > RADIUS else "home"
    if old_state == "away":
        return "home" if distance < RADIUS * 0.8 else "away"
    return "home" if distance <= RADIUS else "away"


def time_ago(timestamp):
    minutes = int((time.time() - timestamp) / 60)
    if minutes < 1:
        return "abhi abhi"
    if minutes < 60:
        return str(minutes) + " minute pehle"
    return str(minutes // 60) + " ghante pehle"


def post_to_whatsapp(payload):
    url = "https://graph.facebook.com/v21.0/" + PHONE_NUMBER_ID + "/messages"
    headers = {"Authorization": "Bearer " + ACCESS_TOKEN, "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    print("WhatsApp API:", response.status_code, response.text)


def send_text(to_number, text):
    post_to_whatsapp({"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": text}})


def send_location(to_number, lat, lon, name):
    post_to_whatsapp({"messaging_product": "whatsapp", "to": to_number, "type": "location", "location": {"latitude": lat, "longitude": lon, "name": name}})


def tell_family(text, lat=None, lon=None):
    for number in FAMILY_NUMBERS:
        send_text(number, text)
        if lat is not None:
            send_location(number, lat, lon, "Abdullah ki location")


def process_location(lat, lon, ts, accuracy=None):
    status = get_status()
    old_state = status["state"]
    new_state = old_state
    if HOME_LAT is not None and HOME_LON is not None and (accuracy is None or accuracy <= MAX_ACCURACY):
        d = distance_meters(HOME_LAT, HOME_LON, lat, lon)
        new_state = next_state(old_state, d)
        print("Ghar se doori:", round(d), "meter | state:", new_state)
    save_status(lat, lon, ts, new_state)
    if status["sharing"] != 1:
        return
    if old_state == "home" and new_state == "away":
        tell_family("Abdullah ghar se nikal gaya hai.", lat, lon)
    elif old_state == "away" and new_state == "home":
        tell_family("Abdullah ghar pohanch gaya hai.")


def handle_owner(message_type, message, sender):
    if message_type == "location":
        loc = message["location"]
        process_location(loc["latitude"], loc["longitude"], int(time.time()), None)
        send_text(sender, "Location mil gayi aur save ho gayi.")
        return
    text = message.get("text", {}).get("body", "").strip().lower()
    if text in ["stop", "pause"]:
        set_sharing(False)
        send_text(sender, "Sharing band. Gharwalon ko ab location nahi milegi. Dobara shuru karne ke liye 'start' likhein.")
    elif text in ["start", "resume"]:
        set_sharing(True)
        send_text(sender, "Sharing shuru ho gayi.")
    elif text == "status":
        status = get_status()
        sharing = "on" if status["sharing"] == 1 else "off"
        when = time_ago(status["time"]) if status["time"] else "koi update nahi"
        send_text(sender, "Sharing: " + sharing + "\nState: " + str(status["state"]) + "\nAakhri update: " + when)
    else:
        send_text(sender, "Commands: stop, start, status. Ya apni location pin bhejein.")


def handle_family(sender):
    status = get_status()
    if status["sharing"] != 1:
        send_text(sender, "Abdullah ne abhi location sharing band ki hui hai.")
    elif status["lat"] is None:
        send_text(sender, "Abdullah ki location abhi tak nahi aayi.")
    else:
        send_location(sender, status["lat"], status["lon"], "Abdullah ki location")
        where = {"home": " Woh ghar par hain.", "away": " Woh ghar se bahar hain."}.get(status["state"], "")
        send_text(sender, "Yeh location " + time_ago(status["time"]) + " update hui thi." + where)


@app.route("/")
def home():
    return "Location Bot Chal Raha Hai!"


@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json(silent=True) or {}
    try:
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]
    except (KeyError, IndexError):
        return "OK", 200
    try:
        sender = message.get("from")
        message_type = message.get("type")
        if sender == OWNER_NUMBER:
            handle_owner(message_type, message, sender)
        elif sender in FAMILY_NUMBERS:
            handle_family(sender)
        else:
            print("Unknown number ignore kiya:", sender)
    except Exception as error:
        print("Error:", error)
    return "OK", 200


@app.route("/owntracks", methods=["POST"])
def owntracks():
    key = request.args.get("key", "")
    if not OWNTRACKS_KEY or not hmac.compare_digest(key, OWNTRACKS_KEY):
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    try:
        if data.get("_type") == "location":
            process_location(data["lat"], data["lon"], data.get("tst", int(time.time())), data.get("acc"))
    except Exception as error:
        print("Error:", error)
    return jsonify([])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
