import asyncio
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext, Filters
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
# 系統通知（報錯、重啟、警告）只發送給管理員
ADMIN_CHAT_ID = 48732810  

# 預設發送目標 (您的頻道 ID)
# 當使用 /start 時，此列表會被動態覆蓋為下指令的頻道/聊天室 ID
TARGET_CHAT_IDS = [-1004310110864]  
# -----------------

# 更新為新的預設網址
DEFAULT_PAGE_URL = "https://fujiyama.tv/live/?n=26&b=0"
DEFAULT_IMAGE_URL = "https://fujiyama.tv/live/camimg.php?n=26&img=cam.jpg"
DEFAULT_LOCATION = "鳴沢村活き活き広場"

# 用於控制任務和儲存網址的全局變數
running = False
task = None
user_page_url = DEFAULT_PAGE_URL
loop = None
last_task_check = 0
session = requests.Session()
last_image_url = None
run_event = None
last_session_reset = time.time()

# 持久化運行狀態檔案和環境變數
RUNNING_STATE_FILE = "running.txt"
RUNNING_STATE_ENV = "BOT_RUNNING_STATE"

# 初始化 Flask 應用
flask_app = Flask(__name__)

def save_running_state(state):
    """儲存 running 狀態到檔案和環境變數"""
    try:
        with open(RUNNING_STATE_FILE, "w") as f:
            f.write(str(state))
        logger.info(f"儲存 running 狀態：{state} 到 {RUNNING_STATE_FILE}")
    except Exception as e:
        logger.error(f"儲存 running 狀態到檔案失敗：{e}")
    try:
        os.environ[RUNNING_STATE_ENV] = str(state)
        logger.info(f"儲存 running 狀態：{state} 到環境變數 {RUNNING_STATE_ENV}")
    except Exception as e:
        logger.error(f"儲存 running 狀態到環境變數失敗：{e}")
    if not state:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            data = {"chat_id": ADMIN_CHAT_ID, "text": f"Bot 已停止運行"}
            session.post(url, json=data, timeout=5).close()
        except Exception as notify_e:
            logger.error(f"通知用戶失敗：{notify_e}")

def load_running_state():
    """從檔案或環境變數載入 running 狀態"""
    try:
        env_state = os.getenv(RUNNING_STATE_ENV)
        if env_state is not None:
            state = env_state.lower() == "true"
            logger.info(f"從環境變數 {RUNNING_STATE_ENV} 載入 running 狀態：{state}")
            return state
        if os.path.exists(RUNNING_STATE_FILE):
            with open(RUNNING_STATE_FILE, "r") as f:
                state = f.read().strip()
                logger.info(f"從 {RUNNING_STATE_FILE} 載入 running 狀態：{state}")
                return state.lower() == "true"
        logger.warning(f"{RUNNING_STATE_FILE} 不存在，預設 running=True")
        save_running_state(True)
        return True
    except Exception as e:
        logger.error(f"載入 running 狀態失敗：{e}")
        save_running_state(True)
        return True

def reset_session():
    """重置 requests.Session 以釋放資源"""
    global session, last_session_reset
    try:
        session.close()
        session = requests.Session()
        last_session_reset = time.time()
        logger.info("已重置 requests.Session")
    except Exception as e:
        logger.error(f"重置 session 失敗：{e}")

async def get_latest_image_url(page_url):
    """從指定頁面爬取圖片網址和地點名稱"""
    logger.info(f"開始爬取圖片網址，頁面：{page_url}")
    try:
        start_time = time.time()
        response = session.get(page_url, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        # 1. 獲取圖片網址
        img_tag = soup.find("img", id="mov")
        if img_tag and img_tag.get("src"):
            image_url = img_tag["src"]
        else:
            meta_img = soup.find("meta", property="og:image")
            if meta_img and meta_img.get("content"):
                image_url = meta_img["content"]
            else:
                image_url = DEFAULT_IMAGE_URL
                
        # 處理相對路徑
        if image_url.startswith("/"):
            parsed_url = urlparse(page_url)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            image_url = f"{base_url}{image_url}"
        elif not image_url.startswith("http"):
            image_url = DEFAULT_IMAGE_URL
            
        # 2. 獲取地點名稱
        location_tag = soup.find("span", class_="auto-style3")
        if location_tag:
            location = location_tag.text.strip().replace("/ ", "")
        else:
            title_tag = soup.find("title")
            if title_tag:
                title_text = title_tag.text
                if "-" in title_text and "|" in title_text:
                    location = title_text.split("-")[1].split("|")[0].strip()
                else:
                    location = DEFAULT_LOCATION
            else:
                location = DEFAULT_LOCATION
                
        logger.info(f"成功爬取圖片網址：{image_url}，地點：{location}，耗時：{time.time() - start_time:.2f}s")
        return image_url, location
    except Exception as e:
        logger.error(f"爬取圖片網址或地點失敗：{e}\n{traceback.format_exc()}")
        return DEFAULT_IMAGE_URL, DEFAULT_LOCATION
    finally:
        if 'response' in locals():
            response.close()

def seturl(update: Update, context: CallbackContext):
    global user_page_url
    if not context.args:
        update.effective_message.reply_text("請提供網址！用法：/seturl <網址>")
        return

    new_url = context.args[0]
    if not new_url.startswith("http"):
        update.effective_message.reply_text("請提供有效的網址（以 http 或 https 開頭）！")
        return

    try:
        response = session.get(new_url, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        img_tag = soup.find("img", id="mov")
        meta_img = soup.find("meta", property="og:image")
        
        if not (img_tag and img_tag.get("src")) and not (meta_img and meta_img.get("content")):
            update.effective_message.reply_text("該網址無效，無法找到圖片來源！")
            return
    except Exception as e:
        update.effective_message.reply_text(f"錯誤：無法訪問網址 ({str(e)})")
        return
    finally:
        if 'response' in locals():
            response.close()

    user_page_url = new_url
    update.effective_message.reply_text(f"網址已設定為：{new_url}\n使用 /start 開始傳送圖片。")
    logger.info(f"網址設定為：{new_url}，由 {update.effective_chat.id} 執行")

def start(update: Update, context: CallbackContext):
    global running, task, run_event, TARGET_CHAT_IDS
    
    # 抓取當前下指令的聊天室/頻道 ID
    current_chat_id = update.effective_chat.id
    TARGET_CHAT_IDS = [current_chat_id]
    
    if running:
        update.effective_message.reply_text("Bot 已經在運行！")
        return

    logger.info(f"收到 /start 指令，目標已鎖定為 {current_chat_id}")
    running = True
    run_event.set()
    save_running_state(running)
    update.effective_message.reply_text("Bot 已啟動，將每分鐘傳送最新圖片至此。使用 /stop 停止。")
    if task is None or task.done():
        asyncio.run_coroutine_threadsafe(start_send_images(), loop)
    logger.info("啟動圖片傳送任務")
    global last_task_check
    last_task_check = time.time()

def resume(update: Update, context: CallbackContext):
    global running, task, run_event
    if running:
        update.effective_message.reply_text("Bot 已經在運行！")
        return

    logger.info(f"收到 /resume 指令，由 {update.effective_chat.id} 執行")
    running = True
    run_event.set()
    save_running_state(running)
    update.effective_message.reply_text("Bot 已恢復，將每分鐘傳送圖片至設定目標。")
    if task is None or task.done():
        asyncio.run_coroutine_threadsafe(start_send_images(), loop)
    logger.info("恢復圖片傳送任務")
    global last_task_check
    last_task_check = time.time()

def stop(update: Update, context: CallbackContext):
    global running, task, run_event
    if not running:
        update.effective_message.reply_text("Bot 已經停止！")
        return

    logger.info(f"收到 /stop 指令，由 {update.effective_chat.id} 執行")
    running = False
    run_event.clear()
    save_running_state(running)
    if task and not task.done():
        task.cancel()
        logger.info("任務已取消")
    update.effective_message.reply_text("Bot 已停止。使用 /resume 恢復。")
    logger.info("停止圖片傳送任務")

async def start_send_images():
    global task
    if task is None or task.done():
        task = loop.create_task(send_images())
        logger.info("創建新的 send_images 任務")

async def send_images():
    global user_page_url, running, last_task_check, last_image_url
    logger.info("開始圖片傳送循環")
    while True:
        try:
            start_time = time.time()
            logger.info(f"圖片傳送迴圈開始時間：{start_time}")
            await run_event.wait()
            if not running:
                logger.info("圖片傳送任務因 running=False 而暫停")
                break
            logger.info("進入圖片傳送迴圈")
            last_task_check = time.time()
            
            current_url, location = await get_latest_image_url(user_page_url)
            if current_url != last_image_url:
                last_image_url = current_url
                logger.info(f"圖片網址已更新：{current_url}")
            else:
                logger.info(f"圖片網址未變：{current_url}")
                
            if "?" in current_url:
                request_url = f"{current_url}&_t={int(time.time() * 1000)}"
            else:
                request_url = f"{current_url}?_t={int(time.time() * 1000)}"
                
            logger.info(f"請求圖片網址：{request_url}")
            request_start = time.time()
            response = session.get(request_url, timeout=5)
            logger.info(f"圖片請求耗時：{time.time() - request_start:.2f}s")
            
            if response.status_code == 200:
                jst_time = datetime.now(ZoneInfo("Asia/Tokyo"))
                caption = f"{jst_time.strftime('%Y-%m-%d JST %H:%M')} {location} (Source: Fujiyama.TV)"
                logger.info(f"準備傳送圖片，caption：{caption}")
                telegram_start = time.time()
                url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
                
                # 緩存圖片內容供群發使用
                photo_data = response.content
                
                # 迴圈發送至所有目標 ID
                for target_id in TARGET_CHAT_IDS:
                    files = {"photo": photo_data}
                    data = {"chat_id": target_id, "caption": caption}
                    telegram_response = session.post(url, data=data, files=files, timeout=5)
                    
                    if telegram_response.status_code == 200:
                        logger.info(f"圖片已成功傳送給 ID: {target_id}")
                    else:
                        logger.error(f"傳送給 {target_id} 失敗，狀態碼：{telegram_response.status_code}，錯誤：{telegram_response.text}")
                    telegram_response.close()
                    
                logger.info(f"Telegram 全體傳送耗時：{time.time() - telegram_start:.2f}s")
                logger.info(f"圖片傳送流程完畢，網址：{current_url}，地點：{location}，時間：{caption}")
                response.close()
            else:
                logger.warning(f"無法下載圖片，網址：{request_url}，狀態碼：{response.status_code}")
                response.close()
                
            process = psutil.Process()
            mem_info = process.memory_info()
            cpu_percent = psutil.cpu_percent(interval=None)
            logger.info(f"內存使用：RSS={mem_info.rss / 1024 / 1024:.2f}MB, VMS={mem_info.vms / 1024 / 1024:.2f}MB, CPU={cpu_percent:.1f}%")
            
            if mem_info.rss / 1024 / 1024 > 400:
                logger.warning("內存使用接近限制，嘗試重置 session")
                reset_session()
            if time.time() - last_session_reset > 3600:
                logger.info("定時重置 session")
                reset_session()
                
            logger.info("即將進入 60 秒睡眠")
            await asyncio.sleep(60)
            logger.info("睡眠結束，繼續迴圈")
            
        except asyncio.CancelledError:
            logger.info("圖片傳送任務被取消")
            run_event.clear()
            break
        except Exception as e:
            logger.error(f"圖片傳送流程異常：{e}\n{traceback.format_exc()}")
            try:
                url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                # 錯誤訊息只傳給管理員
                data = {"chat_id": ADMIN_CHAT_ID, "text": f"圖片傳送異常：{str(e)}，將在 10 秒後重試"}
                session.post(url, json=data, timeout=5).close()
            except Exception as notify_e:
                logger.error(f"通知管理員失敗：{notify_e}")
            await asyncio.sleep(10)
        finally:
            logger.info(f"圖片傳送迴圈結束，運行時間：{time.time() - start_time:.2f}s，檢查 running 狀態")
            if not running:
                logger.info("因 running=False 退出 send_images")
                break

async def watchdog():
    global running, task, last_task_check
    logger.info("看門狗任務啟動")
    last_notify_time = 0
    while True:
        try:
            current_time = time.time()
            task_status = task is not None and not task.done()
            task_exception = task.exception() if task and task.done() else None
            process = psutil.Process()
            mem_info = process.memory_info()
            cpu_percent = psutil.cpu_percent(interval=None)
            logger.info(f"看門狗檢查：running={running}, task_exists={task is not None}, task_done={task.done() if task else True}, last_task_check={current_time - last_task_check:.2f}s, task_exception={task_exception}, memory_rss={mem_info.rss / 1024 / 1024:.2f}MB, cpu_percent={cpu_percent:.1f}%")
            if running and (not task_status or task_exception or (current_time - last_task_check > 90)):
                logger.warning(f"檢測到任務可能停止，last_task_check={last_task_check}, current_time={current_time}, task_exception={task_exception}")
                if task and not task.done():
                    task.cancel()
                running = True
                save_running_state(running)
                run_event.set()
                task = loop.create_task(send_images())
                last_task_check = time.time()
                logger.info("重啟圖片傳送任務")
                if current_time - last_notify_time > 300:
                    try:
                        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                        data = {"chat_id": ADMIN_CHAT_ID, "text": "系統通知：圖片傳送任務已重啟"}
                        session.post(url, json=data, timeout=5).close()
                        last_notify_time = current_time
                    except Exception as e:
                        logger.error(f"通知管理員失敗：{e}")
            elif not running and task_status:
                logger.warning("running=False 但任務仍在運行，取消任務")
                task.cancel()
                save_running_state(False)
            elif not running and (current_time - last_task_check > 600):
                if current_time - last_notify_time > 600:
                    logger.warning("任務長時間未運行，通知管理員")
                    try:
                        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                        data = {"chat_id": ADMIN_CHAT_ID, "text": "系統通知：Bot 已停止，請使用 /start 或 /resume 恢復"}
                        session.post(url, json=data, timeout=5).close()
                        last_notify_time = current_time
                    except Exception as e:
                        logger.error(f"通知管理員失敗：{e}")
            if mem_info.rss / 1024 / 1024 > 450:
                logger.warning("內存使用過高，可能導致服務終止")
                try:
                    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                    data = {"chat_id": ADMIN_CHAT_ID, "text": f"警告：內存使用過高 ({mem_info.rss / 1024 / 1024:.2f}MB)，可能導致服務終止"}
                    session.post(url, json=data, timeout=5).close()
                except Exception as e:
                    logger.error(f"通知管理員失敗：{e}")
            if cpu_percent > 90:
                logger.warning("CPU 使用率過高，可能導致服務終止")
                try:
                    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                    data = {"chat_id": ADMIN_CHAT_ID, "text": f"警告：CPU 使用率過高 ({cpu_percent:.1f}%)，可能導致服務終止"}
                    session.post(url, json=data, timeout=5).close()
                except Exception as e:
                    logger.error(f"通知管理員失敗：{e}")
            await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"看門狗異常：{e}\n{traceback.format_exc()}")
            await asyncio.sleep(10)

async def keep_alive():
    logger.info("Keep-alive 任務啟動")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            data = {
                "chat_id": ADMIN_CHAT_ID,
                "text": f"Bot is alive at {datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d JST %H:%M')}"
            }
            response = session.post(url, json=data, timeout=5)
            if response.status_code == 200:
                logger.info("Keep-alive 訊息發送成功")
            else:
                logger.error(f"Keep-alive 訊息發送失敗，狀態碼：{response.status_code}，回應：{response.text}")
            response.close()
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"Keep-alive 異常：{e}\n{traceback.format_exc()}")
            await asyncio.sleep(300)

def signal_handler(sig, frame):
    """處理服務終止信號"""
    logger.info(f"收到信號 {sig}，保存 running 狀態並關閉服務")
    save_running_state(running)
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        data = {"chat_id": ADMIN_CHAT_ID, "text": f"服務即將終止（信號 {sig}），將嘗試在重啟後恢復"}
        session.post(url, json=data, timeout=5).close()
    except Exception as e:
        logger.error(f"通知管理員失敗：{e}")
    sys.exit(0)

def initialize_bot():
    global updater, running, task, run_event, loop
    try:
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        run_event = asyncio.Event()
        running = load_running_state()
        logger.info(f"載入 running 狀態：{running}")
        env_vars = {k: "****" if k in ["TELEGRAM_BOT_TOKEN", "GITHUB_TOKEN", "GITHUB_ACCESS_TOKEN"] else v for k, v in os.environ.items()}
        logger.info(f"環境變數：{env_vars}")
        logger.info(f"系統資訊：Python {sys.version}, Platform {platform.platform()}, CPUs {psutil.cpu_count()}")
        if running:
            run_event.set()
            logger.info("檢測到 running=True，準備自動恢復圖片傳送")
            try:
                url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                data = {"chat_id": ADMIN_CHAT_ID, "text": f"服務已重啟，自動恢復圖片傳送，時間：{datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d JST %H:%M')}"}
                session.post(url, json=data, timeout=5).close()
            except Exception as e:
                logger.error(f"通知管理員失敗：{e}")
        updater = Updater(TOKEN, use_context=True)
        dispatcher = updater.dispatcher
        
        # 使用 Filters.all 確保能收到頻道指令
        dispatcher.add_handler(CommandHandler("seturl", seturl, filters=Filters.all))
        dispatcher.add_handler(CommandHandler("start", start, filters=Filters.all))
        dispatcher.add_handler(CommandHandler("resume", resume, filters=Filters.all))
        dispatcher.add_handler(CommandHandler("stop", stop, filters=Filters.all))
        
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/setMyCommands",
            json={
                "commands": [
                    {"command": "seturl", "description": "設置圖片頁面網址（例如 /seturl <網址>）"},
                    {"command": "start", "description": "開始每分鐘傳送最新圖片"},
                    {"command": "resume", "description": "恢復傳送圖片"},
                    {"command": "stop", "description": "停止傳送圖片"}
                ]
            }
        )
        if response.status_code == 200:
            logger.info("Telegram 命令選單已設置")
        else:
            logger.error(f"設置命令選單失敗：{response.text}")
        response.close()
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/setWebhook",
            json={"url": WEBHOOK_URL}
        )
        if response.status_code == 200:
            logger.info(f"Bot 正在運行，Webhook 已設定為 {WEBHOOK_URL}")
        else:
            logger.error(f"設置 Webhook 失敗：{response.text}")
        response.close()
        asyncio.run_coroutine_threadsafe(watchdog(), loop)
        asyncio.run_coroutine_threadsafe(keep_alive(), loop)
        logger.info("看門狗和 Keep-alive 任務已啟動")
        if running and (task is None or task.done()):
            task = loop.create_task(send_images())
            last_task_check = time.time()
            logger.info("初始化時自動啟動圖片傳送任務")
    except Exception as e:
        logger.error(f"初始化 Bot 失敗：{e}\n{traceback.format_exc()}")
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            data = {"chat_id": ADMIN_CHAT_ID, "text": f"初始化 Bot 失敗：{str(e)}"}
            session.post(url, json=data, timeout=5).close()
        except Exception as notify_e:
            logger.error(f"通知管理員失敗：{notify_e}")
        raise
    finally:
        for resp in [r for r in locals().values() if isinstance(r, requests.Response)]:
            resp.close()

def run_event_loop():
    global loop
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.info("事件循環啟動")
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("收到終止信號，關閉事件循環")
    except Exception as e:
        logger.error(f"事件循環異常停止：{e}\n{traceback.format_exc()}")
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            data = {"chat_id": ADMIN_CHAT_ID, "text": f"事件循環異常：{str(e)}"}
            session.post(url, json=data, timeout=5).close()
        except Exception as notify_e:
            logger.error(f"通知管理員失敗：{notify_e}")
    finally:
        logger.info("關閉事件循環")
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    global updater, last_task_check
    logger.info("收到 Webhook 請求")
    if updater is None:
        logger.warning("Updater 未初始化，正在初始化")
        initialize_bot()
    try:
        update = Update.de_json(request.get_json(), updater.bot)
        if update:
            updater.dispatcher.process_update(update)
            logger.info("Webhook 請求處理成功")
            last_task_check = time.time()
        else:
            logger.warning("無效的 Webhook 請求")
        return Response(status=200)
    except Exception as e:
        logger.error(f"處理 Webhook 請求失敗：{e}\n{traceback.format_exc()}")
        return Response(status=500)

@flask_app.route("/", methods=["GET", "HEAD"])
def health_check():
    logger.info("收到健康檢查請求")
    global running, task
    try:
        process = psutil.Process()
        mem_info = process.memory_info()
        cpu_percent = psutil.cpu_percent(interval=None)
        task_exception = task.exception() if task and task.done() else None
        status = {
            "running": running,
            "task_active": task is not None and not task.done(),
            "last_task_check": last_task_check,
            "timestamp": time.time(),
            "memory_rss_mb": mem_info.rss / 1024 / 1024,
            "memory_vms_mb": mem_info.vms / 1024 / 1024,
            "cpu_percent": cpu_percent,
            "task_exception": str(task_exception) if task_exception else None
        }
        logger.info(f"健康檢查狀態：{status}")
        return Response(f"Bot is running: {status}", status=200)
    except Exception as e:
        logger.error(f"健康檢查失敗：{e}\n{traceback.format_exc()}")
        return Response("Health check failed", status=500)

if __name__ == "__main__":
    try:
        logger.info("主程式啟動")
        threading.Thread(target=run_event_loop, daemon=True).start()
        initialize_bot()
        port = int(os.getenv("PORT", 8443))
        logger.info(f"啟啟 Flask 伺服器，端口：{port}")
        flask_app.run(host="0.0.0.0", port=port)
    except Exception as e:
        logger.error(f"主程式異常：{e}\n{traceback.format_exc()}")
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            data = {"chat_id": ADMIN_CHAT_ID, "text": f"主程式異常：{str(e)}"}
            session.post(url, json=data, timeout=5).close()
        except Exception as notify_e:
            logger.error(f"通知管理員失敗：{notify_e}")