import asyncio
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from bs4 import BeautifulSoup
import os
from flask import Flask, request, Response
import threading
import logging
import sys
import traceback
import time
import psutil
import signal
import platform
from urllib.parse import urlparse

# 設置日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

# 從環境變數獲取 Telegram Bot Token 和 Webhook URL
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# --- ID 設定區 ---
# 系統通知（報錯、重啟、警告）只發送給管理員 (您的私人 ID)
ADMIN_CHAT_ID = 48732810  

# 圖片發送目標 (強制鎖定為您的頻道)
TARGET_CHAT_IDS = [-1004310110864]  
# -----------------

DEFAULT_PAGE_URL = "https://fujiyama.tv/live/?n=26&b=0"
DEFAULT_IMAGE_URL = "https://fujiyama.tv/live/camimg.php?n=26&img=cam.jpg"
DEFAULT_LOCATION = "鳴沢村活き活き広場"

# 全局變數
running = False
task = None
user_page_url = DEFAULT_PAGE_URL
loop = None
last_task_check = 0
session = requests.Session()
last_image_url = None
run_event = None
last_session_reset = time.time()

RUNNING_STATE_FILE = "running.txt"
RUNNING_STATE_ENV = "BOT_RUNNING_STATE"

flask_app = Flask(__name__)

def save_running_state(state):
    try:
        with open(RUNNING_STATE_FILE, "w") as f:
            f.write(str(state))
    except Exception as e:
        logger.error(f"儲存狀態檔案失敗：{e}")
    try:
        os.environ[RUNNING_STATE_ENV] = str(state)
    except Exception as e:
        logger.error(f"儲存狀態環境變數失敗：{e}")
    if not state:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            session.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": "Bot 已停止運行"}, timeout=5).close()
        except:
            pass

def load_running_state():
    try:
        env_state = os.getenv(RUNNING_STATE_ENV)
        if env_state is not None:
            return env_state.lower() == "true"
        if os.path.exists(RUNNING_STATE_FILE):
            with open(RUNNING_STATE_FILE, "r") as f:
                return f.read().strip().lower() == "true"
        save_running_state(True)
        return True
    except:
        save_running_state(True)
        return True

def reset_session():
    global session, last_session_reset
    try:
        session.close()
        session = requests.Session()
        last_session_reset = time.time()
    except Exception as e:
        logger.error(f"重置 session 失敗：{e}")

async def get_latest_image_url(page_url):
    try:
        response = session.get(page_url, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        img_tag = soup.find("img", id="mov")
        if img_tag and img_tag.get("src"):
            image_url = img_tag["src"]
        else:
            meta_img = soup.find("meta", property="og:image")
            image_url = meta_img["content"] if meta_img and meta_img.get("content") else DEFAULT_IMAGE_URL
                
        if image_url.startswith("/"):
            parsed_url = urlparse(page_url)
            image_url = f"{parsed_url.scheme}://{parsed_url.netloc}{image_url}"
        elif not image_url.startswith("http"):
            image_url = DEFAULT_IMAGE_URL
            
        location_tag = soup.find("span", class_="auto-style3")
        if location_tag:
            location = location_tag.text.strip().replace("/ ", "")
        else:
            location = DEFAULT_LOCATION
                
        return image_url, location
    except Exception:
        return DEFAULT_IMAGE_URL, DEFAULT_LOCATION
    finally:
        if 'response' in locals():
            response.close()

def seturl(update: Update, context: CallbackContext):
    global user_page_url
    if not context.args:
        update.message.reply_text("請提供網址！用法：/seturl <網址>")
        return
    new_url = context.args[0]
    user_page_url = new_url
    update.message.reply_text(f"網址已設定為：{new_url}\n這將套用至頻道的圖片更新。")

def start(update: Update, context: CallbackContext):
    global running, task, run_event
    # 移除了動態覆蓋目標 ID 的邏輯，永遠發送到頻道
    if running:
        update.message.reply_text("Bot 已經在運行！圖片正持續發送至頻道。")
        return
    running = True
    run_event.set()
    save_running_state(running)
    update.message.reply_text("✅ Bot 已啟動！將每分鐘傳送圖片至您的「頻道」。")
    if task is None or task.done():
        asyncio.run_coroutine_threadsafe(start_send_images(), loop)
    global last_task_check
    last_task_check = time.time()

def resume(update: Update, context: CallbackContext):
    global running, task, run_event
    if running:
        update.message.reply_text("Bot 已經在運行！")
        return
    running = True
    run_event.set()
    save_running_state(running)
    update.message.reply_text("▶️ Bot 已恢復，繼續發送圖片至頻道。")
    if task is None or task.done():
        asyncio.run_coroutine_threadsafe(start_send_images(), loop)
    global last_task_check
    last_task_check = time.time()

def stop(update: Update, context: CallbackContext):
    global running, task, run_event
    if not running:
        update.message.reply_text("Bot 已經停止！")
        return
    running = False
    run_event.clear()
    save_running_state(running)
    if task and not task.done():
        task.cancel()
    update.message.reply_text("⏸️ Bot 已停止向頻道發送圖片。使用 /resume 恢復。")

async def start_send_images():
    global task
    if task is None or task.done():
        task = loop.create_task(send_images())

async def send_images():
    global user_page_url, running, last_task_check, last_image_url
    while True:
        try:
            start_time = time.time()
            await run_event.wait()
            if not running:
                break
            last_task_check = time.time()
            
            current_url, location = await get_latest_image_url(user_page_url)
            if current_url != last_image_url:
                last_image_url = current_url
                
            request_url = f"{current_url}&_t={int(time.time() * 1000)}" if "?" in current_url else f"{current_url}?_t={int(time.time() * 1000)}"
            response = session.get(request_url, timeout=5)
            
            if response.status_code == 200:
                jst_time = datetime.now(ZoneInfo("Asia/Tokyo"))
                caption = f"{jst_time.strftime('%Y-%m-%d JST %H:%M')} {location} (Source: Fujiyama.TV)"
                url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
                photo_data = response.content
                
                # 始終發送到 TARGET_CHAT_IDS (頻道)
                for target_id in TARGET_CHAT_IDS:
                    files = {"photo": photo_data}
                    data = {"chat_id": target_id, "caption": caption}
                    telegram_response = session.post(url, data=data, files=files, timeout=5)
                    telegram_response.close()
                response.close()
            else:
                response.close()
                
            process = psutil.Process()
            mem_info = process.memory_info()
            if mem_info.rss / 1024 / 1024 > 400 or time.time() - last_session_reset > 3600:
                reset_session()
                
            await asyncio.sleep(60)
            
        except asyncio.CancelledError:
            run_event.clear()
            break
        except Exception as e:
            logger.error(f"圖片傳送異常：{e}")
            await asyncio.sleep(10)
        finally:
            if not running:
                break

async def watchdog():
    global running, task, last_task_check
    while True:
        try:
            current_time = time.time()
            task_status = task is not None and not task.done()
            if running and (not task_status or (current_time - last_task_check > 90)):
                if task and not task.done():
                    task.cancel()
                running = True
                save_running_state(running)
                run_event.set()
                task = loop.create_task(send_images())
                last_task_check = time.time()
            elif not running and task_status:
                task.cancel()
                save_running_state(False)
            await asyncio.sleep(10)
        except Exception:
            await asyncio.sleep(10)

async def keep_alive():
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            data = {"chat_id": ADMIN_CHAT_ID, "text": f"系統運行中：{datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d JST %H:%M')}"}
            session.post(url, json=data, timeout=5).close()
            await asyncio.sleep(21600) # 改為每6小時回報一次，避免通知過多
        except:
            await asyncio.sleep(300)

def signal_handler(sig, frame):
    save_running_state(running)
    sys.exit(0)

def initialize_bot():
    global updater, running, task, run_event, loop
    try:
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        run_event = asyncio.Event()
        running = load_running_state()
        if running:
            run_event.set()
        
        updater = Updater(TOKEN, use_context=True)
        dispatcher = updater.dispatcher
        
        dispatcher.add_handler(CommandHandler("seturl", seturl))
        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(CommandHandler("resume", resume))
        dispatcher.add_handler(CommandHandler("stop", stop))
        
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/setWebhook",
            json={"url": WEBHOOK_URL}
        ).close()
        
        asyncio.run_coroutine_threadsafe(watchdog(), loop)
        asyncio.run_coroutine_threadsafe(keep_alive(), loop)
        if running and (task is None or task.done()):
            task = loop.create_task(send_images())
            last_task_check = time.time()
    except Exception as e:
        logger.error(f"初始化失敗：{e}")

def run_event_loop():
    global loop
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_forever()
    finally:
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    global updater, last_task_check
    if updater is None:
        initialize_bot()
    try:
        update = Update.de_json(request.get_json(), updater.bot)
        if update:
            updater.dispatcher.process_update(update)
            last_task_check = time.time()
        return Response(status=200)
    except:
        return Response(status=500)

@flask_app.route("/", methods=["GET", "HEAD"])
def health_check():
    return Response("Bot is running", status=200)

if __name__ == "__main__":
    try:
        threading.Thread(target=run_event_loop, daemon=True).start()
        initialize_bot()
        port = int(os.getenv("PORT", 8443))
        flask_app.run(host="0.0.0.0", port=port)
    except Exception as e:
        logger.error(f"啟動異常：{e}")