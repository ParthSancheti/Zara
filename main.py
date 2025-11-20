import logging
import asyncio
import os
import random
import re
import time
import json
import pickle
from datetime import datetime
import aiosqlite
import edge_tts
import feedparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import PIL.Image

# NEW IMPORTS FOR BROWSER AUTOMATION
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from google import genai
from flask import Flask
from threading import Thread

# --- KEEP ALIVE SERVER ---
keep_alive_app = Flask(__name__)

@keep_alive_app.route('/')
def home():
    return "I am alive! 🤖"

def run_http_server():
    keep_alive_app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_http_server)
    t.start()

# ==============================================================================
# 🔐 CONFIGURATION
# ==============================================================================
TELEGRAM_TOKEN = "8031061598:AAFoGq0W2whMlW7fKAgbG6TlulPZYKIzDTc"
ADMIN_ID = 8318090503  # REPLACE WITH YOUR REAL ID
GEMINI_API_KEY = "AIzaSyBDmPfk4HOR6DWG8V3bCrC9w784N8j4xKQ"

BOT_NAME = "Zara"

# CHANGED: en-IN-NeerjaNeural sounds much more "Real" for Hinglish/South Delhi girls
# It handles English words inside Hindi sentences perfectly.
VOICE = "en-IN-KavyaNeural" 

PICS_FOLDER = "photos"
COOKIES_FILE = "cookies.pkl"

# AI MODEL SETUP
client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemini-2.5-flash" 

# ==============================================================================
# 🛠️ LOGGING & SETUP
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
sent_images_tracker = {}


# ==============================================================================
# 🌐 CORE 0: THE BROWSER ENGINE (AUTOMATION)
# ==============================================================================
class BrowserManager:
    def __init__(self):
        self.driver = None

    def get_driver(self, headless=False):
        options = webdriver.ChromeOptions()
        if headless:
            # This hides the browser completely
            options.add_argument("--headless=new")
        
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--start-maximized")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        return driver

    def save_cookies(self, driver, domain):
        cookies = driver.get_cookies()
        if os.path.exists(COOKIES_FILE):
            try:
                with open(COOKIES_FILE, 'rb') as f:
                    all_cookies = pickle.load(f)
            except:
                all_cookies = {}
        else:
            all_cookies = {}
        
        all_cookies[domain] = cookies
        with open(COOKIES_FILE, 'wb') as f:
            pickle.dump(all_cookies, f)
        print(f"✅ Cookies saved for {domain}")

    def load_cookies(self, driver, domain):
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
        """Opens a VISIBLE browser for you to login manually (Only used once)"""
        driver = self.get_driver(headless=False) # False here so you can see it
        driver.get(url)
        print(f"⚠️ Please Log In to {domain} manually in the browser window...")
        
        while True:
            try:
                driver.title 
                time.sleep(2)
                if "reddit.com" in driver.current_url or "quora.com" in driver.current_url:
                    self.save_cookies(driver, domain)
            except:
                break
        print("✅ Browser closed. Session saved.")

    def auto_post_reddit(self, post_url):
        # CHANGED: Headless is NOW TRUE. It will run silently in background.
        driver = self.get_driver(headless=True) 
        try:
            driver.get("https://www.reddit.com")
            
            # 1. Load Cookies
            if not self.load_cookies(driver, "reddit"):
                driver.quit()
                return "❌ No cookies found. Run `/login_reddit` first."
            
            driver.refresh()
            time.sleep(2)
            driver.get(post_url)
            time.sleep(5)

            # 2. GENERATE REPLY (AI)
            try:
                post_title = driver.title
            except: post_title = "Reddit Post"
            
            print(f"🧠 Generating reply for: {post_title}")
            prompt = f"""
            You are a helpful Reddit user.
            CONTEXT: Thread title is "{post_title}".
            TASK: Write a short, empathetic, human-like comment.
            Do NOT act like a bot. Do NOT use hashtags. Keep it under 15 words.
            REPLY:
            """
            response = client.models.generate_content(model=MODEL_ID, contents=prompt)
            comment_text = response.text.strip().replace('"', '')
            print(f"🤖 Generated: {comment_text}")

            # 3. POSTING LOGIC
            try:
                # Wait for the rich text editor (contenteditable)
                comment_box = WebDriverWait(driver, 15).until(
                    EC.element_to_be_clickable((By.XPATH, "//div[@contenteditable='true']"))
                )
                comment_box.click()
                time.sleep(1)
                
                comment_box.send_keys(comment_text)
                time.sleep(2)
                
                # Submit via Ctrl+Enter
                comment_box.send_keys(Keys.CONTROL, Keys.ENTER)
                time.sleep(5)
                
                driver.quit()
                return f"✅ Posted silently:\n'{comment_text}'"
            
            except Exception as e:
                driver.quit()
                return f"❌ Failed to find comment box: {e}"

        except Exception as e:
            driver.quit()
            return f"❌ Browser Error: {e}"

browser = BrowserManager()

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
        
        # CHANGED: Using Neerja (Hinglish) with slight pitch adjustment for a younger vibe
        communicate = edge_tts.Communicate(clean_text, VOICE, rate="+0%", pitch="+2Hz")
        
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
# 🕵️ CORE 3: AUTOMATED GRIND (REDDIT)
# ==============================================================================
async def grind_reddit_leads(context: ContextTypes.DEFAULT_TYPE):
    # RSS Scan
    target_feeds = ["https://www.reddit.com/r/lonely/new/.rss", "https://www.reddit.com/r/MakeNewFriendsHere/new/.rss"]
    
    try:
        def scan():
            found = []
            for url in target_feeds:
                f = feedparser.parse(url)
                # Only look at the newest 2 posts
                for e in f.entries[:2]:
                    if any(k in e.title.lower() for k in ["lonely", "sad", "talk"]): found.append(e)
            return found

        posts = await asyncio.to_thread(scan)
        for post in posts:
            prompt = f"Write a viral reply to: '{post.title}'. Be empathetic."
            ai_res = await asyncio.to_thread(client.models.generate_content, model=MODEL_ID, contents=prompt)
            
            # Button calls /autopost logic
            keyboard = [[InlineKeyboardButton("🤖 Auto-Post Now", callback_data=f"autopost|{post.link}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            msg = f"🕵️ **Weekly Lead Found:** {post.title}\n🔗 {post.link}\n\n📝 **Draft:**\n`{ai_res.text}`"
            # Only send if ADMIN_ID is valid
            if ADMIN_ID != 1234567890:
                await context.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Grind Error: {e}")

# ==============================================================================
# 🎮 HANDLERS
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Oye, finally you messaged! 🤨")

async def admin_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return False
    text = update.message.text

    # LOGIN COMMANDS
    if text == "/login_reddit":
        await update.message.reply_text("🖥️ Opening Chrome VISIBLY... Log in manually, check 'Remember Me', then the window will close automatically after you log in.")
        await asyncio.to_thread(browser.manual_login, "https://www.reddit.com/login", "reddit")
        await update.message.reply_text("✅ Login Window Closed. Cookies saved for background use.")
        return True
        
    if text == "/stats":
        await update.message.reply_text("📊 **Stats**\n\nAdmin Mode Active.")
        return True

    # AUTOPOST COMMAND (MANUAL TRIGGER)
    if text.startswith("/autopost"):
        parts = text.split(" ")
        if len(parts) < 2:
            await update.message.reply_text("Usage: `/autopost [URL]`")
            return True
        
        url = parts[1]
        await update.message.reply_text("🤖 Ghost Mode: Analyzing post & Generating reply in background...")
        
        result = await asyncio.to_thread(browser.auto_post_reddit, url)
        await update.message.reply_text(result)
        return True

    return False

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("autopost|"):
        url = data.split("|")[1]
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"🤖 Starting Auto-Post (Ghost Mode) for:\n{url}")
        result = await asyncio.to_thread(browser.auto_post_reddit, url)
        await context.bot.send_message(chat_id=ADMIN_ID, text=result)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text or update.message.caption or "[MEDIA SENT]"

    if await admin_commands(update, context): return

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
    keep_alive()  # Start the keep-alive server
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(db.init_db())

    app = Application.builder().token(TELEGRAM_TOKEN).read_timeout(30).write_timeout(30).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VOICE, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    if ADMIN_ID != 1234567890:
        # CHANGED: Interval set to 604800 seconds (1 week)
        # It runs 10 seconds after startup, then every week.
        app.job_queue.run_repeating(grind_reddit_leads, interval=604800, first=10)

    print(f"🔥 {BOT_NAME} is Online! (Ghost Mode Active - Weekly Schedule)")
    app.run_polling()