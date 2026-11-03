import json
import sqlite3
import time
import os
import threading
import urllib.error
import urllib.request
from flask import Flask

# ====== SOZLAMALAR ======
ADMIN_IDS = [7900512582]
# ========================

# Render muhitidan tokenni avtomat olish (agar bo'lmasa terminaldan so'raydi)
TOKEN = os.environ.get("BOT_TOKEN", "").strip().strip('"')
if not TOKEN:
    TOKEN = input("Bot tokenini kiriting va Enter bosing: ").strip().strip('"')

URL = f"https://api.telegram.org/bot{TOKEN}/"

# Ma'lumotlar bazasini Render o'chirmaydigan papkada ochish
DB_PATH = "baza.db"
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute(
    "CREATE TABLE IF NOT EXISTS items ("
    "code TEXT PRIMARY KEY, file_id TEXT, file_type TEXT, title TEXT)"
)
db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
db.commit()


def get_setting(key):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else ""


def set_setting(key, value):
    db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))
    db.commit()


def normalize_channel(text):
    text = text.strip().strip('"')
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.strip("/")
    if not text:
        return ""
    return text if text.startswith("@") else "@" + text


def ask_channels():
    saved = get_setting("channels")
    if saved:
        return [c for c in saved.split(",") if c]
    return []


CHANNELS = ask_channels()
PENDING = {}


def api(method, **params):
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        URL + method, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def send(chat_id, text, **extra):
    try:
        api("sendMessage", chat_id=chat_id, text=text, **extra)
    except Exception as e:
        print("Xabar yuborishda xato:", e)


def is_member(user_id):
    for ch in CHANNELS:
        try:
            res = api("getChatMember", chat_id=ch, user_id=user_id)["result"]
            status = res["status"]
            ok = status in ("creator", "administrator", "member") or (
                status == "restricted" and res.get("is_member")
            )
            if not ok:
                return False
        except Exception:
            return False
    return True


def ask_subscribe(chat_id):
    rows = [
        [{"text": f"📢 {ch}", "url": "https://t.me/" + ch.lstrip("@")}]
        for ch in CHANNELS
    ]
    rows.append([{"text": "🔄 Tekshirish", "callback_data": "check"}])
    names = "\n".join(CHANNELS)
    send(
        chat_id,
        f"❌ Kinoni olish uchun avval kanalga a'zo bo'ling:\n{names}\n\n"
        "A'zo bo'lgach «🔄 Tekshirish» ni bosing, kinoni o'zim yuboraman.",
        reply_markup={"inline_keyboard": rows},
    )


def send_movie(chat_id, code):
    row = db.execute(
        "SELECT file_id, file_type, title FROM items WHERE code = ?", (code,)
    ).fetchone()
    if not row:
        send(chat_id, "❌ Bunday kod topilmadi. Kodni tekshirib qayta yozing.")
        return
    file_id, ftype, title = row
    methods = {
        "video": ("sendVideo", "video"),
        "photo": ("sendPhoto", "photo"),
        "document": ("sendDocument", "document"),
    }
    method, key = methods[ftype]
    try:
        api(method, chat_id=chat_id, caption=title, **{key: file_id})
    except Exception as e:
        print(" Kinoni yuborishda xato:", e)


def handle_callback(cb):
    user_id = cb["from"]["id"]
    chat_id = cb["message"]["chat"]["id"]
    if cb.get("data") != "check":
        return
    if is_member(user_id):
        api("answerCallbackQuery", callback_query_id=cb["id"], text="Rahmat! ✅")
        code = PENDING.pop(user_id, None)
        if code:
            send_movie(chat_id, code)
        else:
            send(chat_id, "✅ Obuna tasdiqlandi! Endi kino kodini yuboring.")
    else:
        api("answerCallbackQuery", callback_query_id=cb["id"],
            text="Siz hali kanalga a'zo bo'lmadingiz!", show_alert=True)


def handle(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = (msg.get("text") or "").strip()
    is_admin = user_id in ADMIN_IDS
    has_media = "video" in msg or "document" in msg or "photo" in msg

    db.execute("INSERT OR IGNORE INTO users VALUES (?)", (user_id,))
    db.commit()

    # Admin: kanal sozlash (/setchannel @kanal)
    if is_admin and text.startswith("/setchannel "):
        new_ch = normalize_channel(text.split(maxsplit=1)[1])
        set_setting("channels", new_ch)
        global CHANNELS
        CHANNELS = ask_channels()
        send(chat_id, f"✅ Majburiy kanal o'rnatildi: {new_ch}")
        return

    # Admin: fayl qo'shish
    if is_admin and has_media:
        caption = (msg.get("caption") or "").strip()
        if not caption:
            send(chat_id, "Izohga kod yozing. Masalan:\n123 | Film nomi")
            return
        code, _, title = caption.partition("|")
        code, title = code.strip(), title.strip()

        if "video" in msg:
            file_id, ftype = msg["video"]["file_id"], "video"
        elif "document" in msg:
            file_id, ftype = msg["document"]["file_id"], "document"
        else:
            file_id, ftype = msg["photo"][-1]["file_id"], "photo"

        db.execute(
            "INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?)",
            (code, file_id, ftype, title),
        )
        db.commit()
        send(chat_id, f"✅ Saqlandi. Kod: {code}")
        return

    if is_admin and text.startswith("/del "):
        code = text.split(maxsplit=1)[1].strip()
        db.execute("DELETE FROM items WHERE code = ?", (code,))
        db.commit()
        send(chat_id, f"🗑 O'chirildi: {code}")
        return

    if is_admin and text == "/stats":
        users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        items = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        channels_list = get_setting("channels") or "Kanal ulanmagan"
        send(chat_id, f"📊 Foydalanuvchilar: {users} ta\n🎬 Kinolar: {items} ta\n📢 Majburiy kanal: {channels_list}")
        return

    if text == "/start":
        if CHANNELS and not is_member(user_id):
            ask_subscribe(chat_id)
            return
        send(chat_id, "Salom! 👋\nKino kodini yuboring, men uni topib beraman.")
        return

    if text:
        if CHANNELS and not is_member(user_id):
            PENDING[user_id] = text
            ask_subscribe(chat_id)
            return
        send_movie(chat_id, text)


# --- FLASK SERVER ---
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot ishlayapti!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def main():
    try:
        me = api("getMe")
        print("Bot ishlayapti:", "@" + me["result"]["username"])
    except Exception as e:
        print("Token xato:", e)
        return

    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    offset = 0
    while True:
        try:
            res = api("getUpdates", offset=offset, timeout=30)
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    if "message" in upd:
                        handle(upd["message"])
                    elif "callback_query" in upd:
                        handle_callback(upd["callback_query"])
                except Exception as e:
                    print("Xabarda xato:", e)
        except Exception as e:
            print("Ulanishda xato:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
