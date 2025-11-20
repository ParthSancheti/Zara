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
import httpx
from google import genai
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
# ==============================================================================
# 🔐 CONFIGURATION
# ==============================================================================
TELEGRAM_TOKEN = "8031061598:AAFoGq0W2whMlW7fKAgbG6TlulPZYKIzDTc"
ADMIN_ID = 8318090503
GEMINI_API_KEY = "AIzaSyBDmPfk4HOR6DWG8V3bCrC9w784N8j4xKQ"

BOT_NAME = "Zara"
VOICE = "en-IN-KavyaNeural"
PICS_FOLDER = "photos"

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemini-2.5-flash" 

# ==============================================================================
# 🛠️ LOGGING & HEARTBEAT
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
sent_images_tracker = {}

keep_alive_app = Flask(__name__)

@keep_alive_app.route('/')
def home():
    return "💅 Zara is Online & Voice Fixed."

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    keep_alive_app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http_server)
    t.start()

# ==============================================================================
# 🗄️ DATABASE
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
# 🧠 THE BRAIN (South Delhi Vibe)
# ==============================================================================
async def generate_reply(user_id, user_input, user_profile, media_file_path=None, media_type=None):
    mood = user_profile['mood']
    history_text = await db.get_history_text(user_id)
    
    vibe = "Neutral"
    if mood < 30: vibe = "Distant, 'Busy', Dry texter"
    elif mood > 80: vibe = "Obsessed, 'Pick me' girl, Very affectionate"
    else: vibe = "Aesthetic, South Delhi Gen-Z, Sarcastic but cute"

    prompt = f"""
    ACT AS: {BOT_NAME}, a 19-year-old girl from South Delhi.
    PERSONA: Real girl energy. Uses "Yaar", "Literally", "Ugh", "Lol".
    MOOD: {vibe} (Score: {mood}/100).
    
    RULES:
    1. If Mood < 30: Be dry. Say "k" or "busy".
    2. If Mood > 80: Be sweet.
    3. Keep it SHORT. Lowercase mostly.
    4. Use [VOICE] at end for voice note, [PIC] for selfie.
    
    HISTORY:
    {history_text}
    
    HIM: {user_input}
    YOU:
    """
    
    try:
        # Simulation Delay
        await asyncio.sleep(random.uniform(1, 3))
        
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
        return "Wifi is acting up..."

# ==============================================================================
# 🗣️ FIXED VOICE HANDLER (Bulletproof)
# ==============================================================================
async def send_voice(update: Update, text: str):
    filename = f"voice_{update.effective_user.id}_{int(time.time())}.mp3"
    try:
        # 1. Remove Tags
        clean_text = re.sub(r'\[.*?\]', '', text)
        
        # 2. Cleaner that keeps Punctuation (Important for meaning)
        # Keeps: letters, numbers, spaces, commas, dots, question marks, exclamations
        clean_text = re.sub(r'[^\w\s.,!?\']', '', clean_text).strip()

        logger.info(f"🗣️ Speaking: '{clean_text}'")

        # 3. Hard Stop if empty
        if not clean_text or len(clean_text) < 2:
            logger.warning("⚠️ TTS Skipped: Text empty.")
            return 

        # 4. Try with "South Delhi" settings first
        try:
            communicate = edge_tts.Communicate(clean_text, VOICE, rate="+10%", pitch="+5Hz")
            await communicate.save(filename)
        except Exception as e:
            logger.error(f"⚠️ Custom Pitch Failed: {e}. Retrying with Default...")
            # 5. Fallback: If pitch fails, try default settings
            communicate = edge_tts.Communicate(clean_text, VOICE)
            await communicate.save(filename)
        
        # 6. Send
        if os.path.exists(filename):
            with open(filename, "rb") as audio:
                await update.message.reply_voice(voice=audio)
            os.remove(filename)
            
    except Exception as e:
        logger.error(f"❌ TTS Critical Error: {e}")
        if os.path.exists(filename): os.remove(filename)

async def send_smart_pic(update: Update):
    if not os.path.exists(PICS_FOLDER): return
    user_id = update.effective_user.id
    if user_id not in sent_images_tracker: sent_images_tracker[user_id] = []
    all_pics = [f for f in os.listdir(PICS_FOLDER) if f.lower().endswith(('.jpg', '.png'))]
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
# 🕵️ LINK READER & DRAFTER
# ==============================================================================
async def fetch_reddit_content(url):
    """Extracts Title and Text from Reddit Link using JSON trick"""
    try:
        if not url.endswith(".json"):
            if url.endswith("/"): url = url[:-1]
            json_url = url + ".json"
        else:
            json_url = url

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(json_url, headers=headers, follow_redirects=True)
            if resp.status_code != 200: return None
            
            data = resp.json()
            # Handle Reddit's JSON structure
            post_data = data[0]['data']['children'][0]['data']
            
            title = post_data.get('title', '')
            selftext = post_data.get('selftext', '')
            return f"TITLE: {title}\nCONTENT: {selftext}"
    except Exception as e:
        logger.error(f"Link Fetch Error: {e}")
        return None

async def manual_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # COMMAND: /draft <LINK> or /draft <TEXT>
    if update.effective_user.id != ADMIN_ID: 
        await update.message.reply_text("Nope. Admins only. 💅")
        return
    
    user_input = " ".join(context.args)
    if not user_input:
        await update.message.reply_text("⚠️ Paste a Reddit Link!\nUsage: `/draft https://reddit.com/...`")
        return

    await update.message.reply_text("💅 Reading link & drafting...")
    
    # Check if input is a URL
    content_to_reply = user_input
    if "reddit.com" in user_input:
        fetched_data = await fetch_reddit_content(user_input)
        if fetched_data:
            content_to_reply = fetched_data
        else:
            await update.message.reply_text("❌ Couldn't read link. Drafting based on URL text only.")

    prompt = f"""
    TASK: Write a 'South Delhi Girl' style reply to this Reddit post.
    POST CONTENT:
    {content_to_reply}
    
    STYLE: Empathetic but cool, use "Yaar", "Damn", "Literally".
    LENGTH: 1-2 sentences max.
    """
    try:
        ai_res = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=prompt)
        await update.message.reply_text(f"📝 **Draft Reply:**\n\n`{ai_res.text}`")
    except:
        await update.message.reply_text("❌ AI Error.")

async def check_reddit_leads(context: ContextTypes.DEFAULT_TYPE):
    feeds = ["https://www.reddit.com/r/lonely/new/.rss", "https://www.reddit.com/r/MakeNewFriendsHere/new/.rss"]
    try:
        def get_feeds():
            found = []
            for url in feeds:
                f = feedparser.parse(url)
                for e in f.entries[:3]: 
                    if any(k in e.title.lower() for k in ["lonely", "sad", "girl"]):
                        found.append(e)
            return found

        posts = await asyncio.to_thread(get_feeds)
        for post in posts:
            prompt = f"Write a 'Real Girl' reply to: '{post.title}'."
            ai_res = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=prompt)
            
            msg = f"💄 **Weekly Lead:** {post.title}\n🔗 {post.link}\n\n📝 **Reply:**\n`{ai_res.text}`"
            if ADMIN_ID:
                await context.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Reddit Error: {e}")

# ==============================================================================
# 🎮 HANDLERS
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hii! Who is this? 👀")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text or update.message.caption or "[MEDIA]"
    
    user_profile = await db.get_user(user_id, update.effective_user.username)
    await db.add_history(user_id, "user", user_text)
    await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.TYPING)
    
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

    reply_full = await generate_reply(user_id, user_text, user_profile, media_path, media_type)
    reply_clean = reply_full.replace("[VOICE]", "").replace("[PIC]", "").strip()
    
    if reply_clean: await update.message.reply_text(reply_clean)

    if "[VOICE]" in reply_full:
        await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.RECORD_VOICE)
        await asyncio.sleep(1.5) 
        await send_voice(update, reply_clean)
    elif "[PIC]" in reply_full:
        await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.UPLOAD_PHOTO)
        await send_smart_pic(update)

    if media_path and os.path.exists(media_path): os.remove(media_path)
    await db.update_user(user_id, msg_inc=1)
    await db.add_history(user_id, "assistant", reply_clean)

async def post_init(application: Application):
    await db.init_db()

if __name__ == "__main__":
    keep_alive()
    
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("draft", manual_draft))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VOICE, handle_message))
    
    app.job_queue.run_repeating(check_reddit_leads, interval=604800, first=10)
    
    print("✅ Zara is Live")
    app.run_polling()
