from flask import Flask, request
import os

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "").strip()


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
    data = request.get_json()
    try:
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]
        print("Message type:", message["type"])
        print("Poora message:", message)
    except (KeyError, IndexError):
        pass
    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)