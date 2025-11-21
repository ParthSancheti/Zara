import logging
import asyncio
import os
import random
import re
import time
import json
import pickle
from datetime import datetime, timedelta
import aiosqlite
import edge_tts
import feedparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import PIL.Image
from threading import Thread
from flask import Flask

# NEW IMPORTS FOR BROWSER AUTOMATION
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from google import genai

# ==============================================================================
# 🔐 CONFIGURATION
# ==============================================================================
TELEGRAM_TOKEN = "8031061598:AAFoGq0W2whMlW7fKAgbG6TlulPZYKIzDTc"
ADMIN_ID = 8318090503  # REPLACE WITH YOUR REAL ID
GEMINI_API_KEY = "AIzaSyA140hM8UTpMjJddSq3Qhv9k231nMrGkuk"

BOT_NAME = "Zara"

# CHANGED: AnanyaNeural is naturally higher pitched and young sounding (Indian Accent)
# We removed the manual pitch parameter to fix the crash.
VOICE = "en-IN-AnanyaNeural" 

PICS_FOLDER = "photos"
COOKIES_FILE = "cookies.pkl"

# AI MODEL SETUP
client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemini-2.5-flash" 

# ==============================================================================
# 🛠️ LOGGING & SERVER
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
sent_images_tracker = {}

keep_alive_app = Flask(__name__)

@keep_alive_app.route('/')
def home(): return "I am alive! 🤖"

def run_http_server(): keep_alive_app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_http_server)
    t.start()

# ==============================================================================
# 🌐 CORE 0: BROWSER (SELENIUM)
# ==============================================================================
class BrowserManager:
    def __init__(self):
        self.driver = None

    def get_driver(self, headless=True):
        options = webdriver.ChromeOptions()
        if headless: options.add_argument("--headless=new")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        service = ChromeService(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)

    def auto_post_reddit(self, post_url):
        driver = self.get_driver(headless=True) 
        try:
            driver.get(post_url)
            time.sleep(5)
            try: post_title = driver.title
            except: post_title = "Reddit Post"
            
            print(f"🧠 Generating Long Reply for: {post_title}")
            
            # CHANGED: Prompt for longer, more detailed Reddit replies
            prompt = f"""
            You are a helpful, empathetic Reddit user.
            CONTEXT: Thread title is "{post_title}".
            TASK: Write a detailed, human-like comment.
            RULES: 
            1. Do NOT be short. Write at least 3-4 sentences. 
            2. Add personal advice or a story.
            3. Do NOT use hashtags or act like a bot.
            REPLY:
            """
            response = client.models.generate_content(model=MODEL_ID, contents=prompt)
            comment_text = response.text.strip().replace('"', '')
            
            # (Posting logic omitted for brevity, assuming cookies are valid)
            # In a real scenario, you would inject cookies here similar to previous code
            driver.quit()
            return f"✅ Generated (Ghost Mode):\n'{comment_text}'"
            
        except Exception as e:
            driver.quit()
            return f"❌ Browser Error: {e}"

browser = BrowserManager()

# ==============================================================================
# 🗄️ DATABASE MANAGER (UPDATED WITH TASKS)
# ==============================================================================
class DatabaseManager:
    def __init__(self, db_name="zara.db"):
        self.db_name = db_name

    async def init_db(self):
        async with aiosqlite.connect(self.db_name) as db:
            # User Table
            await db.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, mood_score INTEGER DEFAULT 50, relationship_level INTEGER DEFAULT 0, last_interaction TIMESTAMP, messages_count INTEGER DEFAULT 0)''')
            # Chat History
            await db.execute('''CREATE TABLE IF NOT EXISTS history (user_id INTEGER, role TEXT, content TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            # NEW: Scheduled Tasks (Memory)
            await db.execute('''CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task_type TEXT, trigger_time TEXT, prompt_context TEXT, is_recurring BOOLEAN)''')
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
            # Update mood
            await db.execute(f'''UPDATE users SET mood_score = MAX(0, MIN(100, mood_score + ?)), messages_count = messages_count + ?, last_interaction = ? WHERE user_id = ?''', (mood_change, msg_inc, datetime.now(), user_id))
            # Update relationship level based on interaction count
            await db.execute('''UPDATE users SET relationship_level = messages_count / 20 WHERE user_id = ?''', (user_id,))
            await db.commit()

    async def add_history(self, user_id, role, content):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
            # Keep last 20 messages
            await db.execute(f"DELETE FROM history WHERE user_id = ? AND rowid NOT IN (SELECT rowid FROM history WHERE user_id = ? ORDER BY rowid DESC LIMIT 20)", (user_id, user_id))
            await db.commit()

    async def get_history_text(self, user_id):
        async with aiosqlite.connect(self.db_name) as db:
            cursor = await db.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY rowid ASC", (user_id,))
            rows = await cursor.fetchall()
            return "\n".join([f"{'YOU' if r[0]=='assistant' else 'HIM'}: {r[1]}" for r in rows])

    # NEW: Task Management
    async def add_task(self, user_id, time_str, prompt, recurring=True):
        # time_str format: "HH:MM" (24 hour)
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("INSERT INTO tasks (user_id, task_type, trigger_time, prompt_context, is_recurring) VALUES (?, 'message', ?, ?, ?)", (user_id, time_str, prompt, recurring))
            await db.commit()

    async def get_due_tasks(self, current_time_str):
        async with aiosqlite.connect(self.db_name) as db:
            cursor = await db.execute("SELECT * FROM tasks WHERE trigger_time = ?", (current_time_str,))
            return await cursor.fetchall()

db = DatabaseManager()

# ==============================================================================
# 🧠 CORE 1: THE BRAIN (GEMINI)
# ==============================================================================
async def generate_reply(user_id, user_input, user_profile, media_file_path=None, media_type=None):
    mood = user_profile['mood']
    level = user_profile['level']
    history_text = await db.get_history_text(user_id)
    
    # PERSONALITY LOGIC
    tone_instruction = ""
    if level < 2:
        tone_instruction = "RELATIONSHIP LEVEL: 0 (Stranger). ACT PRICY. Be distant, use short replies (1-5 words). Ignore his flirting. Act like a busy South Delhi girl."
    elif level < 5:
        tone_instruction = "RELATIONSHIP LEVEL: 3 (Acquaintance). Be casual but cool. Use Hinglish. 'Yaar', 'Lol'."
    else:
        tone_instruction = "RELATIONSHIP LEVEL: 10 (Close). Be warm, affectionate, maybe a bit clingy. You can send longer messages now."

    prompt = f"""
    ACT AS: {BOT_NAME}, a 19-year-old student from South Delhi.
    
    {tone_instruction}
    
    CURRENT MOOD: {mood}/100.
    
    TASKS FOR YOU:
    1. If the user asks to schedule something (e.g., "Wake me up at 8am", "Remind me to eat"), 
       OUTPUT THIS EXACT CODE HIDDEN IN TEXT: [SCHEDULE: HH:MM | Context] (Use 24hr format).
       Example: [SCHEDULE: 08:00 | Wake him up nicely]
    
    2. MESSAGE LENGTH: 
       - If the topic is deep or emotional, write a long paragraph.
       - If it's banter, keep it snappy.
       - Do NOT act like an AI. Never say "How can I help".
       
    MEDIA TOOLS:
    - End with [VOICE] to send audio.
    - End with [PIC] to send a selfie.
    
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
        return "Ugh, my wifi is glitching... wait."

# ==============================================================================
# 🗣️ CORE 2: MEDIA HANDLERS (FIXED TTS)
# ==============================================================================
async def send_voice(update: Update, text: str, chat_id=None):
    try:
        clean_text = re.sub(r'\[.*?\]', '', text) 
        clean_text = re.sub(r'[^\w\s,.]', '', clean_text)
        if len(clean_text) < 2: return 

        # Unique filename
        filename = f"voice_{int(time.time())}_{random.randint(1,100)}.mp3"
        
        # FIX: Removed manual pitch adjustment that was causing errors.
        # Using 'rate' only, as it is safer.
        communicate = edge_tts.Communicate(clean_text, VOICE, rate="+0%")
        await communicate.save(filename)
        
        if update:
            with open(filename, "rb") as audio:
                await update.message.reply_voice(voice=audio)
        elif chat_id:
            # For scheduled messages where there is no 'update' object
            pass 
            
        os.remove(filename)
    except Exception as e:
        logger.error(f"TTS Error: {e}")

async def send_smart_pic(update: Update, chat_id=None):
    if not os.path.exists(PICS_FOLDER): return
    
    target_id = update.effective_user.id if update else chat_id
    
    if target_id not in sent_images_tracker: sent_images_tracker[target_id] = []

    all_pics = [f for f in os.listdir(PICS_FOLDER) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
    available_pics = [p for p in all_pics if p not in sent_images_tracker[target_id]]
    
    if not available_pics:
        sent_images_tracker[target_id] = []
        available_pics = all_pics

    if available_pics:
        pic_name = random.choice(available_pics)
        sent_images_tracker[target_id].append(pic_name)
        with open(os.path.join(PICS_FOLDER, pic_name), "rb") as p:
            if update:
                await update.message.reply_photo(photo=p)
            elif chat_id:
                pass # Logic for scheduled pics

# ==============================================================================
# ⏰ CORE 3: SCHEDULER & AUTO-MESSAGING
# ==============================================================================
async def check_scheduled_tasks(context: ContextTypes.DEFAULT_TYPE):
    """Runs every minute to check if a message needs to be sent."""
    now_str = datetime.now().strftime("%H:%M")
    # print(f"⏰ Checking tasks for {now_str}...") 
    
    tasks = await db.get_due_tasks(now_str)
    
    for task in tasks:
        # task: (id, user_id, type, time, prompt, recurring)
        user_id = task[1]
        context_prompt = task[4]
        
        # Ask Gemini to generate the message based on the context
        prompt = f"""
        ACT AS: {BOT_NAME}.
        TASK: It is currently {now_str}. You have a scheduled task: "{context_prompt}".
        Generate a natural, affectionate message for the user based on this task.
        """
        
        try:
            ai_res = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=prompt)
            msg_text = ai_res.text
            
            # Send the message
            await context.bot.send_message(chat_id=user_id, text=msg_text)
            logger.info(f"✅ Scheduled Message sent to {user_id}: {msg_text}")
            
            # Optional: If it's a morning message, maybe send a voice note too?
            if "morning" in context_prompt.lower():
                # We can convert the text to speech and send it
                # Need to refactor send_voice to accept bot/chat_id instead of update
                pass 
                
        except Exception as e:
            logger.error(f"Scheduled Task Error: {e}")

# ==============================================================================
# 🎮 HANDLERS
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Who is this? 🤔") # Starts 'pricy'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text or update.message.caption or "[MEDIA SENT]"
    username = update.effective_user.username

    # 1. Get User Profile
    user_profile = await db.get_user(user_id, username)
    
    # 2. Save User Message
    await db.add_history(user_id, "user", user_text)
    
    # 3. Typing Indication
    await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.TYPING)
    
    # 4. Generate Reply
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
    
    # 5. Check for Scheduling Commands from Gemini
    # Pattern: [SCHEDULE: HH:MM | Context]
    schedule_match = re.search(r'\[SCHEDULE: (\d{2}:\d{2}) \| (.*?)\]', reply_full)
    if schedule_match:
        time_str = schedule_match.group(1)
        task_context = schedule_match.group(2)
        await db.add_task(user_id, time_str, task_context)
        # Remove the command from the reply shown to user
        reply_full = reply_full.replace(schedule_match.group(0), "").strip()
        await update.message.reply_text(f"Okay, set for {time_str}. ✅")

    # 6. Clean and Send Reply
    reply_clean = reply_full.replace("[VOICE]", "").replace("[PIC]", "").strip()
    
    if reply_clean: 
        await update.message.reply_text(reply_clean)

    # 7. Handle Media Triggers
    if "[VOICE]" in reply_full:
        await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.RECORD_VOICE)
        await asyncio.sleep(1)
        await send_voice(update, reply_clean)
    elif "[PIC]" in reply_full:
        await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.UPLOAD_PHOTO)
        await asyncio.sleep(1)
        await send_smart_pic(update)

    # Cleanup & Update Stats
    if media_path and os.path.exists(media_path): os.remove(media_path)
    
    # Slowly increase relationship level
    await db.update_user(user_id, msg_inc=1)
    await db.add_history(user_id, "assistant", reply_clean)

if __name__ == "__main__":
    keep_alive()
    
    # Initialize DB
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(db.init_db())

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VOICE, handle_message))
    
    # SCHEDULER: Check for auto-messages every 60 seconds
    app.job_queue.run_repeating(check_scheduled_tasks, interval=60, first=10)

    print(f"🔥 {BOT_NAME} Reborn is Online!")
    app.run_polling()

