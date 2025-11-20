import logging
import asyncio
import os
import random
import re
import time
from datetime import datetime
import aiosqlite
import edge_tts
import feedparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import PIL.Image
from google import genai
from flask import Flask
from threading import Thread

# ==============================================================================
# 🔐 CONFIGURATION
# ==============================================================================
TELEGRAM_TOKEN = "8031061598:AAFoGq0W2whMlW7fKAgbG6TlulPZYKIzDTc"
ADMIN_ID = 8318090503  # REPLACE WITH YOUR REAL ID
GEMINI_API_KEY = "AIzaSyBDmPfk4HOR6DWG8V3bCrC9w784N8j4xKQ"

BOT_NAME = "Zara"
# Using the South Delhi/Hinglish Voice
VOICE = "en-IN-NeerjaNeural" 
PICS_FOLDER = "photos"

# AI MODEL SETUP
client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemini-2.5-flash" 

# ==============================================================================
# 🛠️ LOGGING & KEEP ALIVE
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
sent_images_tracker = {}

# --- FLASK SERVER TO KEEP BOT ALIVE ON RENDER ---
keep_alive_app = Flask(__name__)

@keep_alive_app.route('/')
def home():
    return "I am alive! 🤖"

def run_http_server():
    keep_alive_app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_http_server)
    t.start()
# ------------------------------------------------

# ==============================================================================
# 🗄️ DATABASE MANAGER
# ==============================================================================
class DatabaseManager:
    def __init__(self, db_name="zara.db"):
        self.db_name = db_name

    async def init_db(self):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, mood_score INTEGER DEFAULT 50, relationship_level INTEGER DEFAULT 0, last_interaction TIMESTAMP, messages_count INTEGER DEFAULT 0)''')
            await db.execute('''CREATE TABLE IF NOT EXISTS history (user_id INTEGER, role TEXT, content TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            await db.commit()

    async def get_user(self, user_id, username):
        async with aiosqlite.connect(self.db_name) as db:
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            if not row:
                await db.execute("INSERT INTO users (user_id, username, last_interaction) VALUES (?, ?, ?)", (user_id, username, datetime.now()))
                await db.commit()
                return {"mood": 50, "level": 0}
            return {"mood": row[2], "level": row[3]}

    async def update_user(self, user_id, mood_change=0, msg_inc=0):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute(f'''UPDATE users SET mood_score = MAX(0, MIN(100, mood_score + ?)), messages_count = messages_count + ?, last_interaction = ? WHERE user_id = ?''', (mood_change, msg_inc, datetime.now(), user_id))
            await db.execute('''UPDATE users SET relationship_level = messages_count / 50 WHERE user_id = ?''', (user_id,))
            await db.commit()

    async def add_history(self, user_id, role, content):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
            await db.execute(f"DELETE FROM history WHERE user_id = ? AND rowid NOT IN (SELECT rowid FROM history WHERE user_id = ? ORDER BY rowid DESC LIMIT 15)", (user_id, user_id))
            await db.commit()

    async def get_history_text(self, user_id):
        async with aiosqlite.connect(self.db_name) as db:
            cursor = await db.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY rowid ASC", (user_id,))
            rows = await cursor.fetchall()
            return "\n".join([f"{'YOU' if r[0]=='assistant' else 'HIM'}: {r[1]}" for r in rows])

db = DatabaseManager()

# ==============================================================================
# 🧠 CORE 1: THE BRAIN
# ==============================================================================
async def generate_reply(user_id, user_input, user_profile, media_file_path=None, media_type=None):
    mood = user_profile['mood']
    history_text = await db.get_history_text(user_id)
    
    vibe = "Neutral"
    if mood < 30: vibe = "Angry, Cold, Short responses"
    elif mood > 80: vibe = "Flirty, Clingy, Romantic"
    else: vibe = "Chill, Sarcastic, Gen-Z Delhi Girl"

    prompt = f"""
    ACT AS: {BOT_NAME}, a 19-year-old student from South Delhi. 
    
    YOUR PROFILE:
    - Tone: Hinglish, casual, uses "yaar", "damn", "lol".
    - Current Mood: {vibe} (Score: {mood}/100).
    
    MEDIA INSTRUCTIONS:
    - Use [VOICE] at the end ONLY if you want to reply with a voice note.
    - Use [PIC] at the end ONLY if context requires a selfie.
    
    RULES:
    1. Keep replies SHORT (max 2 sentences).
    2. Be REACTIVE.
    3. NEVER act like an AI.
    
    CHAT HISTORY:
    {history_text}
    
    HIM: {user_input}
    YOU:
    """
    
    try:
        if media_file_path:
            if media_type == "image":
                img = PIL.Image.open(media_file_path)
                response = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=[prompt, img])
            elif media_type == "audio":
                myfile = client.files.upload(path=media_file_path)
                response = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=[prompt, myfile])
        else:
            response = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=prompt)
            
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini Error: {e}")
        return "My net is dying yaar... wait."

# ==============================================================================
# 🗣️ CORE 2: MEDIA HANDLERS
# ==============================================================================
async def send_voice(update: Update, text: str):
    try:
        clean_text = re.sub(r'\[.*?\]', '', text) 
        clean_text = re.sub(r'[^\w\s,.]', '', clean_text)
        if len(clean_text) < 2: return 

        filename = f"voice_{update.effective_user.id}_{int(time.time())}.mp3"
        
        # Neerja (Hinglish) Tuned
        communicate = edge_tts.Communicate(clean_text, VOICE, rate="+10%", pitch="+2Hz")
        
        await communicate.save(filename)
        
        with open(filename, "rb") as audio:
            await update.message.reply_voice(voice=audio)
        os.remove(filename)
    except Exception as e:
        logger.error(f"TTS Error: {e}")

async def send_smart_pic(update: Update):
    if not os.path.exists(PICS_FOLDER): return
    user_id = update.effective_user.id
    if user_id not in sent_images_tracker: sent_images_tracker[user_id] = []

    all_pics = [f for f in os.listdir(PICS_FOLDER) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
    available_pics = [p for p in all_pics if p not in sent_images_tracker[user_id]]
    
    if not available_pics:
        sent_images_tracker[user_id] = []
        available_pics = all_pics

    if available_pics:
        pic_name = random.choice(available_pics)
        sent_images_tracker[user_id].append(pic_name)
        with open(os.path.join(PICS_FOLDER, pic_name), "rb") as p:
            await update.message.reply_photo(photo=p)

# ==============================================================================
# 🕵️ CORE 3: MANUAL LEAD GRIND (NO CHROME)
# ==============================================================================
async def grind_reddit_leads(context: ContextTypes.DEFAULT_TYPE):
    # RSS Scan
    target_feeds = ["https://www.reddit.com/r/lonely/new/.rss", "https://www.reddit.com/r/MakeNewFriendsHere/new/.rss"]
    
    try:
        def scan():
            found = []
            for url in target_feeds:
                f = feedparser.parse(url)
                for e in f.entries[:2]:
                    if any(k in e.title.lower() for k in ["lonely", "sad", "talk"]): found.append(e)
            return found

        posts = await asyncio.to_thread(scan)
        for post in posts:
            prompt = f"Write a viral reply to: '{post.title}'. Be empathetic. Keep it human."
            ai_res = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=prompt)
            
            msg = f"🕵️ **New Lead Found!**\n\n**Title:** {post.title}\n**Link:** {post.link}\n\n📝 **AI Draft Reply:**\n`{ai_res.text}`\n\n_Click the link and paste the draft manually._"
            
            if ADMIN_ID != 1234567890:
                await context.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Grind Error: {e}")

# ==============================================================================
# 🎮 HANDLERS
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Oye, finally you messaged! 🤨")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text or update.message.caption or "[MEDIA SENT]"

    if user_text == "/stats" and user_id == ADMIN_ID:
        await update.message.reply_text("📊 Bot is running smoothly!")
        return

    user_profile = await db.get_user(user_id, update.effective_user.username)
    await db.add_history(user_id, "user", user_text)
    
    await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.TYPING)
    await asyncio.sleep(random.uniform(1, 3))

    media_path = None
    media_type = None

    try:
        if update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
            media_path = f"temp_img_{user_id}.jpg"
            await photo_file.download_to_drive(media_path)
            media_type = "image"
        elif update.message.voice:
            voice_file = await update.message.voice.get_file()
            media_path = f"temp_voice_{user_id}.ogg"
            await voice_file.download_to_drive(media_path)
            media_type = "audio"
    except: pass

    reply_full = await generate_reply(user_id, user_text, user_profile, media_path, media_type)
    reply_clean = reply_full.replace("[VOICE]", "").replace("[PIC]", "").strip()
    
    if reply_clean: await update.message.reply_text(reply_clean)

    if "[VOICE]" in reply_full:
        await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.RECORD_VOICE)
        await asyncio.sleep(1.5)
        await send_voice(update, reply_clean)
    elif "[PIC]" in reply_full:
        await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.UPLOAD_PHOTO)
        await asyncio.sleep(1)
        await send_smart_pic(update)

    if media_path and os.path.exists(media_path): os.remove(media_path)
    await db.update_user(user_id, msg_inc=1)
    await db.add_history(user_id, "assistant", reply_clean)

if __name__ == "__main__":
    keep_alive() # Starts the fake server to satisfy Render
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(db.init_db())

    app = Application.builder().token(TELEGRAM_TOKEN).read_timeout(30).write_timeout(30).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VOICE, handle_message))
    
    if ADMIN_ID != 1234567890:
        # Runs every 30 minutes (1800 seconds) to check for new posts
        app.job_queue.run_repeating(grind_reddit_leads, interval=1800, first=10)

    print(f"🔥 {BOT_NAME} is Online! (Lite Version - No Chrome)")
    app.run_polling()
