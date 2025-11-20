import logging
import asyncio
import os
import random
import re
import time
from datetime import datetime
from threading import Thread
from flask import Flask

# Libraries
import aiosqlite
import edge_tts
import feedparser
import PIL.Image
from google import genai
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ==============================================================================
# 🔐 CONFIGURATION (Fill these in)
# ==============================================================================
TELEGRAM_TOKEN = "8031061598:AAFoGq0W2whMlW7fKAgbG6TlulPZYKIzDTc"
ADMIN_ID = 8318090503  # Your ID
GEMINI_API_KEY = "AIzaSyBDmPfk4HOR6DWG8V3bCrC9w784N8j4xKQ"

BOT_NAME = "Zara"
# Best voice for Hinglish / South Delhi vibe
VOICE = "en-IN-NeerjaNeural" 
PICS_FOLDER = "photos"

# AI Configuration
client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemini-2.5-flash" 

# ==============================================================================
# 🛠️ LOGGING & 24/7 HEARTBEAT
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
sent_images_tracker = {}

# --- FLASK SERVER (Keeps Render Awake) ---
keep_alive_app = Flask(__name__)

@keep_alive_app.route('/')
def home():
    return "✅ Zara Bot is Online & Running!"

def run_http_server():
    # Render sets the PORT environment variable. We MUST use it.
    port = int(os.environ.get("PORT", 8080))
    keep_alive_app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http_server)
    t.start()
# -----------------------------------------

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
        print("✅ Database Initialized")

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
# 🧠 AI & VOICE LOGIC
# ==============================================================================
async def generate_reply(user_id, user_input, user_profile, media_file_path=None, media_type=None):
    mood = user_profile['mood']
    history_text = await db.get_history_text(user_id)
    
    vibe = "Neutral"
    if mood < 30: vibe = "Angry, Cold"
    elif mood > 80: vibe = "Flirty, Clingy"
    else: vibe = "Chill, Sarcastic, Gen-Z Delhi Girl"

    prompt = f"""
    ACT AS: {BOT_NAME}, a 19-year-old girl from South Delhi.
    TONE: Hinglish, uses "yaar", "lol", "damn".
    MOOD: {vibe}.
    
    RULES:
    1. Keep it SHORT (under 20 words).
    2. Be natural.
    3. If you want to send a voice note, end text with [VOICE].
    4. If you want to send a selfie, end text with [PIC].
    
    HISTORY:
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
        return "My net is slow yaar..."

async def send_voice(update: Update, text: str):
    try:
        # Clean text for TTS
        clean_text = re.sub(r'\[.*?\]', '', text)
        clean_text = re.sub(r'[^\w\s,.]', '', clean_text)
        if len(clean_text) < 2: return 

        filename = f"voice_{update.effective_user.id}_{int(time.time())}.mp3"
        # South Delhi Tuning: Slightly faster, slightly higher pitch
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

    all_pics = [f for f in os.listdir(PICS_FOLDER) if f.lower().endswith(('.jpg', '.png'))]
    available_pics = [p for p in all_pics if p not in sent_images_tracker[user_id]]
    
    if not available_pics:
        sent_images_tracker[user_id] = [] # Reset if all sent
        available_pics = all_pics

    if available_pics:
        pic_name = random.choice(available_pics)
        sent_images_tracker[user_id].append(pic_name)
        with open(os.path.join(PICS_FOLDER, pic_name), "rb") as p:
            await update.message.reply_photo(photo=p)

# ==============================================================================
# 🕵️ REDDIT LEAD FINDER (Manual Posting)
# ==============================================================================
async def check_reddit_leads(context: ContextTypes.DEFAULT_TYPE):
    # RSS Feeds for lonely/friendship subreddits
    feeds = ["https://www.reddit.com/r/lonely/new/.rss", "https://www.reddit.com/r/MakeNewFriendsHere/new/.rss"]
    
    try:
        def get_feeds():
            found = []
            for url in feeds:
                f = feedparser.parse(url)
                for e in f.entries[:2]: # Check top 2 new posts
                    if any(k in e.title.lower() for k in ["lonely", "sad", "bored"]):
                        found.append(e)
            return found

        posts = await asyncio.to_thread(get_feeds)
        
        for post in posts:
            # Generate a reply using AI
            prompt = f"Write a short, viral, empathetic reply to this Reddit post: '{post.title}'. Don't sound like a bot."
            ai_res = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=prompt)
            
            msg = (
                f"🕵️ **Lead Found!**\n\n"
                f"📌 **{post.title}**\n"
                f"🔗 {post.link}\n\n"
                f"📝 **Draft Reply:**\n`{ai_res.text}`"
            )
            
            # Send to Admin (You)
            if ADMIN_ID:
                await context.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
                
    except Exception as e:
        logger.error(f"Reddit Error: {e}")

# ==============================================================================
# 🎮 MAIN EXECUTION
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hey! I'm awake. What's up? 😉")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text or update.message.caption or "[MEDIA]"
    
    # Admin stats command
    if user_text == "/stats" and user_id == ADMIN_ID:
        await update.message.reply_text("📊 System is online and running.")
        return

    # Save user history
    user_profile = await db.get_user(user_id, update.effective_user.username)
    await db.add_history(user_id, "user", user_text)
    
    # Typing indicator
    await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.TYPING)
    
    # Media Handling
    media_path, media_type = None, None
    try:
        if update.message.photo:
            photo = await update.message.photo[-1].get_file()
            media_path = f"temp_{user_id}.jpg"
            await photo.download_to_drive(media_path)
            media_type = "image"
        elif update.message.voice:
            voice = await update.message.voice.get_file()
            media_path = f"temp_{user_id}.ogg"
            await voice.download_to_drive(media_path)
            media_type = "audio"
    except: pass

    # Generate Reply
    reply_full = await generate_reply(user_id, user_text, user_profile, media_path, media_type)
    reply_clean = reply_full.replace("[VOICE]", "").replace("[PIC]", "").strip()
    
    if reply_clean: await update.message.reply_text(reply_clean)

    # Send Media if tagged
    if "[VOICE]" in reply_full:
        await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.RECORD_VOICE)
        await send_voice(update, reply_clean)
    elif "[PIC]" in reply_full:
        await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.UPLOAD_PHOTO)
        await send_smart_pic(update)

    # Cleanup
    if media_path and os.path.exists(media_path): os.remove(media_path)
    await db.update_user(user_id, msg_inc=1)
    await db.add_history(user_id, "assistant", reply_clean)

# Hook to initialize DB on startup
async def post_init(application: Application):
    await db.init_db()

if __name__ == "__main__":
    # 1. Start Flask (Keep Alive)
    keep_alive()
    
    # 2. Start Telegram Bot
    print("🚀 Bot Starting...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VOICE, handle_message))
    
    # Check for Reddit leads every 1 hour (3600 seconds)
    app.job_queue.run_repeating(check_reddit_leads, interval=3600, first=10)
    
    print("✅ Bot is Live!")
    app.run_polling()

