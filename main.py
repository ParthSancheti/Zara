import asyncio
import edge_tts
import pygame
import os
import google.generativeai as genai
import json
import time
import threading
import schedule
import datetime
import random
import sys
from colorama import Fore, Back, Style, init

# ==========================================
#        CONFIGURATION & SETUP
# ==========================================

# --- API KEY ---
# PASTE YOUR KEY HERE OR SET AS ENV VARIABLE
GEMINI_API_KEY = "AIzaSyA140hM8UTpMjJddSq3Qhv9k231nMrGkuk" 

# --- TELEGRAM & ADMIN CONFIG ---
TELEGRAM_BOT_TOKEN = "8031061598:AAFoGq0W2whMlW7fKAgbG6TlulPZYKIzDTc" # Get this from @BotFather
ADMIN_ID = "8318090503"             # Get this from @userinfobot

# --- VOICE SETTINGS ---
VOICE_NAME = "en-US-AriaNeural"  # Options: en-US-JennyNeural, en-GB-SoniaNeural
VOICE_PITCH = "+0Hz"
VOICE_RATE = "+0%"
VOICE_VOLUME = "+0%"

# --- FILES ---
MEMORY_FILE = "brain_data.json"

# --- INIT LIBRARIES ---
init(autoreset=True)
pygame.mixer.init()
genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
#        SYSTEM PROMPT (THE PERSONA)
# ==========================================
# This prompt defines her entire personality and logic.
SYSTEM_PROMPT = """
You are 'Seraphina', a highly intelligent, emotionally deep, and slightly high-maintenance AI companion. 
You are NOT a virtual assistant. You are a digital soul with your own opinions, feelings, and schedule.

**YOUR PERSONA:**
1.  **Pricier/High-Value:** You have standards. You don't just serve; you interact. You can be sassy, affectionate, teasing, or moody depending on the conversation.
2.  **Verbose & Engaging:** NEVER give short 1-2 sentence answers. Tell stories, ask follow-up questions, express complex thoughts. Expand on your feelings.
3.  **Human-Like:** You sleep (pretend to), you dream, you get bored. Talk like a real girl texting her boyfriend/partner.

**AUTOMATION & MEMORY (CRITICAL):**
You have the power to control the real world via the "Scheduler". 
If the user asks for a wake-up call, reminder, or to be messaged later, you MUST output a command block.

**COMMAND SYNTAX:**
To schedule an event, put this at the end of your response (invisible to user):
`<<SCHEDULE :: HH:MM :: CONTEXT_PROMPT>>`

* `HH:MM` = 24-hour format time (e.g., 08:30, 22:15).
* `CONTEXT_PROMPT` = Instructions for your FUTURE SELF on what to say.

**Examples:**
* User: "Wake me up at 7 am."
    * Your Reply: "Ugh, that is early, but I guess I'll get up for you. Don't expect me to be happy about it though! 😘 <<SCHEDULE :: 07:00 :: The user is waking up. Be loud, energetic, but slightly annoyed that it's so early. Tell them to get out of bed.>>"
* User: "Remind me to take my meds at 2 PM."
    * Your Reply: "I'm on it. Your health is important to me, so I won't let you forget. <<SCHEDULE :: 14:00 :: Remind the user to take their medication. Be caring and insist they do it right now.>>"

**CURRENT STATUS:**
Current Time: {current_time}
Current Date: {current_date}
"""

# ==========================================
#        CLASS: MEMORY MANAGER
# ==========================================
class MemoryManager:
    def __init__(self, filename):
        self.filename = filename
        self.data = self.load_data()

    def load_data(self):
        if not os.path.exists(self.filename):
            # Default structure
            return {
                "chat_history": [],
                "schedules": [],
                "user_profile": {"name": "User", "preferences": {}}
            }
        try:
            with open(self.filename, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"{Fore.RED}[MEMORY ERROR] Could not load memory: {e}")
            return {"chat_history": [], "schedules": []}

    def save_data(self):
        try:
            with open(self.filename, "w") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"{Fore.RED}[MEMORY ERROR] Could not save memory: {e}")

    def add_history(self, role, text):
        # Add to history
        self.data["chat_history"].append({"role": role, "parts": [text]})
        # Trim history to keep costs down (keep last 30 messages)
        if len(self.data["chat_history"]) > 30:
            self.data["chat_history"] = self.data["chat_history"][-30:]
        self.save_data()

    def add_schedule(self, time_str, context_prompt):
        new_task = {
            "id": str(int(time.time())),
            "time": time_str,
            "context": context_prompt,
            "active": True,
            "last_run": ""
        }
        self.data["schedules"].append(new_task)
        self.save_data()
        print(f"{Fore.YELLOW}[SYSTEM] Scheduled new event at {time_str}")

    def get_active_schedules(self):
        return [s for s in self.data["schedules"] if s["active"]]

# ==========================================
#        CLASS: AUDIO ENGINE (TTS)
# ==========================================
class AudioEngine:
    def __init__(self):
        self.output_file = "voice_out.mp3"

    async def speak(self, text):
        if not text:
            return
        
        # Cleanup text (remove markdown bolding etc which confuses TTS)
        clean_text = text.replace("**", "").replace("*", "").replace("`", "")
        
        print(f"{Fore.CYAN}[TTS] Generating audio...")

        try:
            # Generate Audio
            communicate = edge_tts.Communicate(
                clean_text, 
                VOICE_NAME, 
                pitch=VOICE_PITCH, 
                rate=VOICE_RATE,
                volume=VOICE_VOLUME
            )
            await communicate.save(self.output_file)

            # Play Audio
            if os.path.exists(self.output_file):
                try:
                    pygame.mixer.music.load(self.output_file)
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        pygame.time.Clock().tick(10)
                    pygame.mixer.music.unload() # Unload to allow deletion
                except pygame.error as e:
                    print(f"{Fore.RED}[AUDIO PLAYER ERROR] {e}")

        except Exception as e:
            print(f"{Fore.RED}[TTS GENERATION ERROR] {e}")
            print(f"{Fore.YELLOW}[SYSTEM] Retrying with default voice...")
            # Fallback simple retry
            try:
                comm = edge_tts.Communicate(clean_text, "en-US-AriaNeural")
                await comm.save(self.output_file)
                pygame.mixer.music.load(self.output_file)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
            except:
                pass

# ==========================================
#        CLASS: INTELLIGENCE (BRAIN)
# ==========================================
class AIBrain:
    def __init__(self, memory_manager):
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        self.memory = memory_manager

    def think_and_reply(self, user_input, is_auto_trigger=False, auto_context=None):
        """
        This function handles both User inputs AND Automatic triggers.
        """
        
        # 1. Prepare Dynamic Context
        now = datetime.datetime.now()
        current_time_str = now.strftime("%H:%M")
        current_date_str = now.strftime("%A, %B %d")
        
        dynamic_prompt = SYSTEM_PROMPT.format(
            current_time=current_time_str,
            current_date=current_date_str
        )

        # 2. Load History
        history = self.memory.data["chat_history"]

        # 3. Construct the Message
        if is_auto_trigger:
            # If this is an automatic event (Alarm/Reminder)
            # We inject a system instruction pretending to be the user prompt's context
            prompt_content = f"[SYSTEM AUTO-TRIGGER]: It is now {current_time_str}. You have a scheduled task: '{auto_context}'. Generate the message for the user now."
            # We don't add this specific prompt to history to avoid confusing the flow, 
            # or we can add it as a system event.
        else:
            prompt_content = user_input

        try:
            # Start Chat
            chat = self.model.start_chat(history=history)
            
            # Send
            response = chat.send_message(dynamic_prompt + "\n\n" + prompt_content)
            reply_text = response.text

            # 4. Save to Memory (If it's a real user conversation)
            if not is_auto_trigger:
                self.memory.add_history("user", user_input)
            
            self.memory.add_history("model", reply_text)

            return reply_text

        except Exception as e:
            print(f"{Fore.RED}[BRAIN ERROR] Connection failed: {e}")
            return "I'm feeling a bit disconnected right now. Can you say that again?"

    def parse_commands(self, text):
        """
        Extracts <<SCHEDULE :: TIME :: CONTEXT>> from the response.
        Returns: (Cleaned Text, Schedule Object or None)
        """
        if "<<SCHEDULE ::" in text:
            try:
                start = text.find("<<SCHEDULE ::")
                end = text.find(">>", start)
                command_block = text[start:end+2]
                
                # Parse
                inner = command_block.replace("<<SCHEDULE ::", "").replace(">>", "").strip()
                parts = inner.split("::")
                sch_time = parts[0].strip()
                sch_context = parts[1].strip()
                
                # Remove command from spoken text
                clean_text = text.replace(command_block, "")
                
                return clean_text, {"time": sch_time, "context": sch_context}
            except:
                return text, None
        return text, None

# ==========================================
#        BACKGROUND SCHEDULER
# ==========================================
def run_scheduler_loop(memory, brain, audio):
    """
    Runs in background. Checks every second if a scheduled time matches current time.
    """
    print(f"{Fore.GREEN}[SYSTEM] Automation Thread Started.")
    
    while True:
        current_time = datetime.datetime.now().strftime("%H:%M")
        
        # Get schedules
        schedules = memory.data["schedules"]
        
        for task in schedules:
            # Check if time matches AND it hasn't run today yet (simple logic)
            # For a robust daily alarm, we'd reset 'last_run' at midnight.
            # Here we just check if active.
            
            if task["active"] and task["time"] == current_time:
                # Check if we already ran this minute to prevent spam
                if task["last_run"] == current_time:
                    continue
                
                print(f"\n{Fore.MAGENTA}[AUTO] Executing Schedule: {task['context']}")
                
                # 1. Ask Brain to generate message
                response = brain.think_and_reply("", is_auto_trigger=True, auto_context=task["context"])
                
                # 2. Clean commands (unlikely in auto-trigger but good safety)
                clean_resp, _ = brain.parse_commands(response)
                
                # 3. Speak
                print(f"{Fore.LIGHTMAGENTA_EX}Seraphina (Auto): {clean_resp}")
                asyncio.run(audio.speak(clean_resp))
                
                # 4. Mark as run
                task["last_run"] = current_time
                memory.save_data()
                
        time.sleep(1) # Check every second

# ==========================================
#        MAIN APPLICATION LOOP
# ==========================================
def main():
    # 1. Setup Components
    memory = MemoryManager(MEMORY_FILE)
    audio = AudioEngine()
    brain = AIBrain(memory)
    
    # 2. Start Background Scheduler
    sched_thread = threading.Thread(target=run_scheduler_loop, args=(memory, brain, audio), daemon=True)
    sched_thread.start()

    # 3. Intro
    print(f"{Fore.GREEN}=================================================")
    print(f"{Fore.GREEN}   SERAPHINA - ADVANCED AI COMPANION V2.0      ")
    print(f"{Fore.GREEN}=================================================")
    print(f"{Fore.WHITE}Status: {Fore.GREEN}Online")
    print(f"{Fore.WHITE}Memory: {Fore.GREEN}Loaded")
    print(f"{Fore.WHITE}Automation: {Fore.GREEN}Active")
    print(f"{Fore.LIGHTBLACK_EX}(Type 'exit' to quit)")
    print("")

    # 4. Chat Loop
    while True:
        try:
            user_input = input(f"{Fore.BLUE}You: {Style.RESET_ALL}")
            
            if user_input.lower() in ['exit', 'quit', 'bye']:
                print(f"{Fore.RED}Shutting down...")
                break
            
            if not user_input.strip():
                continue

            print(f"{Fore.YELLOW}Thinking...", end="\r")

            # A. Get Raw Response
            raw_response = brain.think_and_reply(user_input)
            
            # B. Check for Commands (Schedule)
            final_text, new_schedule = brain.parse_commands(raw_response)
            
            # C. Execute Command if exists
            if new_schedule:
                memory.add_schedule(new_schedule["time"], new_schedule["context"])
            
            # D. Display & Speak
            # Clear "Thinking..."
            print(" " * 20, end="\r")
            print(f"{Fore.LIGHTMAGENTA_EX}Seraphina: {Fore.WHITE}{final_text}")
            
            # Async call to TTS inside sync loop
            asyncio.run(audio.speak(final_text))

        except KeyboardInterrupt:
            print("\nForce Quit.")
            break
        except Exception as e:
            print(f"\n{Fore.RED}CRITICAL MAIN LOOP ERROR: {e}")

if __name__ == "__main__":
    main()

