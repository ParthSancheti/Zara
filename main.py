import logging
import asyncio
import os
import random
import re
import time
import json
import pickle
from datetime import datetime, timedelta
from threading import Thread

# THIRD PARTY IMPORTS
import aiosqlite
import feedparser
import PIL.Image
from gtts import gTTS  # Google Text-to-Speech (Stable API)
from flask import Flask
from google import genai

# TELEGRAM IMPORTS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# SELENIUM (BROWSER AUTOMATION)
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ==============================================================================
# 🔐 CONFIGURATION
# ==============================================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0)) # Default to 0 if not found

BOT_NAME = "Zara"
PICS_FOLDER = "photos"
COOKIES_FILE = "cookies.pkl"

# AI MODEL SETUP
client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemini-2.5-flash" 

# ==============================================================================
# 🛠️ SERVER & LOGGING (KEEP ALIVE)
# ==============================================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)
sent_images_tracker = {}

keep_alive_app = Flask(__name__)

@keep_alive_app.route('/')
def home():
    return "I am alive! 🤖 Zara is running."

def run_http_server():
    keep_alive_app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_http_server)
    t.start()

# ==============================================================================
# 🌐 CORE 0: BROWSER MANAGER (REDDIT AUTOMATION)
# ==============================================================================
class BrowserManager:
    def __init__(self):
        self.driver = None

    def get_driver(self, headless=True):
        """Initializes Chrome Driver with anti-detection options."""
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        return driver

    def save_cookies(self, driver, domain):
        """Saves cookies to a file to maintain login session."""
        cookies = driver.get_cookies()
        try:
            with open(COOKIES_FILE, 'rb') as f:
                all_cookies = pickle.load(f)
        except:
            all_cookies = {}
        
        all_cookies[domain] = cookies
        with open(COOKIES_FILE, 'wb') as f:
            pickle.dump(all_cookies, f)
        print(f"✅ Cookies saved for {domain}")

    def load_cookies(self, driver, domain):
        """Loads cookies from file if they exist."""
        if not os.path.exists(COOKIES_FILE): return False
        try:
            with open(COOKIES_FILE, 'rb') as f:
                all_cookies = pickle.load(f)
            
            if domain in all_cookies:
                for cookie in all_cookies[domain]:
                    try:
                        driver.add_cookie(cookie)
                    except: pass
                return True
        except: pass
        return False

    def manual_login(self, url, domain):
        """(Local Only) Opens visible browser for manual login."""
        driver = self.get_driver(headless=False)
        driver.get(url)
        print(f"⚠️ Please Log In to {domain} manually in the browser window...")
        
        while True:
            try:
                if not driver.window_handles: break # Browser closed
                time.sleep(2)
                if domain in driver.current_url:
                    self.save_cookies(driver, domain)
            except: break
        print("✅ Browser session ended.")

    def auto_post_reddit(self, post_url):
        """Navigates to a Reddit post, generates a long AI reply, and attempts to post."""
        driver = self.get_driver(headless=True) 
        try:
            driver.get("https://www.reddit.com")
            if not self.load_cookies(driver, "reddit"):
                driver.quit()
                return "❌ No cookies found. Cannot login to Reddit."
            
            driver.refresh()
            time.sleep(3)
            driver.get(post_url)
            time.sleep(5)

            try: post_title = driver.title
            except: post_title = "Reddit Post"
            
            print(f"🧠 Generating Reddit Reply for: {post_title}")
            prompt = f"""
            You are a helpful, empathetic Reddit user.
            CONTEXT: Thread title is "{post_title}".
            TASK: Write a high-quality, human-like comment.
            RULES: 
            1. Length: 3-5 sentences. Not too short, not too long.
            2. Tone: Casual but helpful. Share a personal anecdote if relevant.
            3. No hashtags, no bot behavior.
            REPLY:
            """
            response = client.models.generate_content(model=MODEL_ID, contents=prompt)
            comment_text = response.text.strip().replace('"', '')
            
            # NOTE: Actual posting click logic is risky on headless servers without advanced undetected drivers.
            # We return the text so you can review it or extend this block to click the 'Comment' button.
            
            driver.quit()
            return f"✅ Generated Draft (Ghost Mode):\n\n{comment_text}\n\n(Auto-clicking disabled for safety on Render)"

        except Exception as e:
            driver.quit()
            return f"❌ Browser Error: {e}"

browser = BrowserManager()

# ==============================================================================
# 🗄️ CORE 1: DATABASE MANAGER (Full Persistence)
# ==============================================================================
class DatabaseManager:
    def __init__(self, db_name="zara.db"):
        self.db_name = db_name

    async def init_db(self):
        async with aiosqlite.connect(self.db_name) as db:
            # 1. User Table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY, 
                    username TEXT, 
                    mood_score INTEGER DEFAULT 50, 
                    relationship_level INTEGER DEFAULT 0, 
                    last_interaction TIMESTAMP, 
                    messages_count INTEGER DEFAULT 0
                )
            ''')
            # 2. History Table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS history (
                    user_id INTEGER, 
                    role TEXT, 
                    content TEXT, 
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # 3. Tasks Table (For Scheduler)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    user_id INTEGER, 
                    task_type TEXT, 
                    trigger_time TEXT, 
                    prompt_context TEXT, 
                    is_recurring BOOLEAN
                )
            ''')
            await db.commit()

    async def get_user(self, user_id, username):
        async with aiosqlite.connect(self.db_name) as db:
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            if not row:
                await db.execute("INSERT INTO users (user_id, username, last_interaction) VALUES (?, ?, ?)", 
                                 (user_id, username, datetime.now()))
                await db.commit()
                return {"mood": 50, "level": 0}
            return {"mood": row[2], "level": row[3]}

    async def update_user(self, user_id, mood_change=0, msg_inc=0):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute('''
                UPDATE users 
                SET mood_score = MAX(0, MIN(100, mood_score + ?)), 
                    messages_count = messages_count + ?, 
                    last_interaction = ? 
                WHERE user_id = ?
            ''', (mood_change, msg_inc, datetime.now(), user_id))
            
            # Relationship level increases every 20 messages
            await db.execute('UPDATE users SET relationship_level = messages_count / 20 WHERE user_id = ?', (user_id,))
            await db.commit()

    async def add_history(self, user_id, role, content):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
            # Keep only last 20 messages to save context window
            await db.execute("DELETE FROM history WHERE user_id = ? AND rowid NOT IN (SELECT rowid FROM history WHERE user_id = ? ORDER BY rowid DESC LIMIT 20)", (user_id, user_id))
            await db.commit()

    async def get_history_text(self, user_id):
        async with aiosqlite.connect(self.db_name) as db:
            cursor = await db.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY rowid ASC", (user_id,))
            rows = await cursor.fetchall()
            return "\n".join([f"{'YOU' if r[0]=='assistant' else 'HIM'}: {r[1]}" for r in rows])

    # --- Scheduler Methods ---
    async def add_task(self, user_id, time_str, prompt, recurring=True):
        """Adds a scheduled task (e.g., Wake me up at 08:00)."""
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("INSERT INTO tasks (user_id, task_type, trigger_time, prompt_context, is_recurring) VALUES (?, 'message', ?, ?, ?)", 
                             (user_id, time_str, prompt, recurring))
            await db.commit()

    async def get_due_tasks(self, current_time_str):
        async with aiosqlite.connect(self.db_name) as db:
            cursor = await db.execute("SELECT * FROM tasks WHERE trigger_time = ?", (current_time_str,))
            return await cursor.fetchall()

db = DatabaseManager()

# ==============================================================================
# 🧠 CORE 2: THE BRAIN (GEMINI WITH FULL PERSONA)
# ==============================================================================
async def generate_reply(user_id, user_input, user_profile, media_file_path=None, media_type=None):
    mood = user_profile['mood']
    level = user_profile['level']
    history_text = await db.get_history_text(user_id)
    
    # Dynamic Personality Logic
    tone_instruction = ""
    if level < 2:
        tone_instruction = "RELATIONSHIP LEVEL: 0 (Stranger). ACT PRICY. You are guarded. Use short replies (1-6 words). Dismiss flirting. You are busy."
    elif level < 5:
        tone_instruction = "RELATIONSHIP LEVEL: 3 (Acquaintance). You are warming up. Use Hinglish (Hindi+English mix). Use slang like 'Yaar', 'Damn', 'Lol'. Be chill."
    else:
        tone_instruction = "RELATIONSHIP LEVEL: 10 (Close/Girlfriend). Be warm, affectionate, teasing. You can send longer messages. You care about him."

    # The Master Prompt
    prompt = f"""
    ACT AS: {BOT_NAME}, a 19-year-old college student from South Delhi.
    
    YOUR VIBE:
    - {tone_instruction}
    - Current Mood Score: {mood}/100.
    
    HIDDEN FUNCTIONS (Output these EXACTLY if needed):
    1. If the user asks to set a reminder/alarm (e.g., "Wake me up at 8am"), output: 
       [SCHEDULE: HH:MM | Context] (Use 24hr format).
    
    2. MEDIA:
       - To send a voice note, end message with [VOICE].
       - To send a photo, end message with [PIC].
       
    RULES:
    - Do NOT be formal. Do NOT say "How can I assist?".
    - If the topic is serious, write a paragraph. If it's banter, be short.
    - Be REACTIVE to the image if one is sent.
    
    HISTORY:
    {history_text}
    
    HIM: {user_input}
    YOU:
    """
    
    try:
        # Handle Text + Image/Audio Input
        if media_file_path:
            if media_type == "image":
                img = PIL.Image.open(media_file_path)
                response = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=[prompt, img])
            elif media_type == "audio":
                myfile = client.files.upload(path=media_file_path)
                response = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=[prompt, myfile])
        else:
            # Text only
            response = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=prompt)
            
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini Generation Error: {e}")
        return "Ugh, my internet is acting up... one sec."

# ==============================================================================
# 🗣️ CORE 3: MEDIA HANDLERS (STABLE gTTS)
# ==============================================================================
async def send_voice(update: Update, text: str):
    """Generates a voice note using Google's stable HTTP API."""
    try:
        # Clean text of system tags
        clean_text = re.sub(r'\[.*?\]', '', text) 
        clean_text = re.sub(r'[^\w\s,.]', '', clean_text).strip()
        
        if len(clean_text) < 1: return 

        # Use a unique filename to avoid collisions
        filename = f"voice_{update.effective_user.id}_{int(time.time())}.mp3"
        
        def generate_audio():
            # tld='co.in' ensures the Indian English accent
            tts = gTTS(text=clean_text, lang='en', tld='co.in', slow=False)
            tts.save(filename)
        
        await asyncio.to_thread(generate_audio)
        
        with open(filename, "rb") as audio:
            await update.message.reply_voice(voice=audio)
            
        os.remove(filename) # Cleanup
    except Exception as e:
        logger.error(f"Voice Generation Error: {e}")

async def send_smart_pic(update: Update):
    """Selects a photo from the folder, avoiding recent duplicates."""
    if not os.path.exists(PICS_FOLDER): return
    user_id = update.effective_user.id
    
    if user_id not in sent_images_tracker: sent_images_tracker[user_id] = []

    all_pics = [f for f in os.listdir(PICS_FOLDER) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
    available_pics = [p for p in all_pics if p not in sent_images_tracker[user_id]]
    
    # Reset if we've shown all photos
    if not available_pics:
        sent_images_tracker[user_id] = []
        available_pics = all_pics

    if available_pics:
        pic_name = random.choice(available_pics)
        sent_images_tracker[user_id].append(pic_name)
        with open(os.path.join(PICS_FOLDER, pic_name), "rb") as p:
            await update.message.reply_photo(photo=p)

# ==============================================================================
# ⏰ CORE 4: SCHEDULER & JOBS
# ==============================================================================
async def check_scheduled_tasks(context: ContextTypes.DEFAULT_TYPE):
    """Checks DB every minute for tasks due at this time."""
    now_str = datetime.now().strftime("%H:%M")
    
    # Fetch tasks due NOW
    tasks = await db.get_due_tasks(now_str)
    
    for task in tasks:
        user_id = task[1]
        prompt_context = task[4]
        
        # Generate a dynamic message for the alarm
        prompt = f"""
        ACT AS: {BOT_NAME}.
        SITUATION: You set an alarm/reminder for the user: "{prompt_context}".
        TIME: It is now {now_str}.
        TASK: Wake them up or remind them sweetly but effectively.
        """
        try:
            ai_res = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=prompt)
            msg_text = ai_res.text
            
            await context.bot.send_message(chat_id=user_id, text=msg_text)
            logger.info(f"⏰ Executed task for {user_id}: {prompt_context}")
            
        except Exception as e:
            logger.error(f"Scheduler Task Error: {e}")

async def grind_reddit_leads(context: ContextTypes.DEFAULT_TYPE):
    """(Optional) Background job to scan Reddit for leads."""
    # This is a placeholder for the background RSS scan if you want it enabled
    pass

# ==============================================================================
# 🎮 HANDLERS & MAIN LOOP
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Oh hey? Who is this? 🤨") 

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text or update.message.caption or "[MEDIA SENT]"
    username = update.effective_user.username

    # 1. Get User Context
    user_profile = await db.get_user(user_id, username)
    
    # 2. Log User Input
    await db.add_history(user_id, "user", user_text)
    
    # 3. Show Typing/Action
    await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.TYPING)
    
    # 4. Handle Incoming Media (Voice/Photo)
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

    # 5. Generate AI Response
    reply_full = await generate_reply(user_id, user_text, user_profile, media_path, media_type)
    
    # 6. Check for Hidden Scheduler Command
    # Regex to find [SCHEDULE: HH:MM | Reason]
    schedule_match = re.search(r'\[SCHEDULE: (\d{2}:\d{2}) \| (.*?)\]', reply_full)
    if schedule_match:
        time_str = schedule_match.group(1)
        task_context = schedule_match.group(2)
        await db.add_task(user_id, time_str, task_context)
        
        # Remove the command from the text sent to user
        reply_full = reply_full.replace(schedule_match.group(0), "").strip()
        # Confirm to user
        await update.message.reply_text(f"Done. Set for {time_str}. ✅")

    # 7. Send Text Response
    reply_clean = reply_full.replace("[VOICE]", "").replace("[PIC]", "").strip()
    if reply_clean: 
        await update.message.reply_text(reply_clean)

    # 8. Send Outgoing Media (If tagged)
    if "[VOICE]" in reply_full:
        await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.RECORD_VOICE)
        await asyncio.sleep(1.5)
        await send_voice(update, reply_clean)
    elif "[PIC]" in reply_full:
        await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.UPLOAD_PHOTO)
        await asyncio.sleep(1)
        await send_smart_pic(update)

    # 9. Cleanup
    if media_path and os.path.exists(media_path): os.remove(media_path)
    
    # 10. Update DB (Increment messages)
    await db.update_user(user_id, msg_inc=1)
    await db.add_history(user_id, "assistant", reply_clean)

# ==============================================================================
# 🏁 ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    keep_alive()  # Start Flask Server for Render
    
    # Initialize DB
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(db.init_db())

    # Build Bot
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VOICE, handle_message))
    
    # Register Scheduler Job (Runs every 60 seconds)
    if app.job_queue:
        app.job_queue.run_repeating(check_scheduled_tasks, interval=60, first=10)
        print("✅ Scheduler Active")
    else:
        print("❌ Job Queue NOT active. Check requirements.txt")

    print(f"🔥 {BOT_NAME} is Online! (Full Version)")
    app.run_polling()


