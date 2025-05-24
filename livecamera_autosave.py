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
DEFAULT_PAGE_URL = "https://live.fujigoko.tv/?n=26&b=0"
DEFAULT_IMAGE_URL = "https://cam.fujigoko.tv/livecam26/cam1_9892.jpg"
DEFAULT_LOCATION = "鳴沢村活き活き広場"

# 用於控制任務和儲存網址的全局變數
running = False
task = None
user_page_url = DEFAULT_PAGE_URL
loop = None
last_task_check = 0
chat_id = 48732810
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
            data = {"chat_id": chat_id, "text": f"Bot 已停止運行"}
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
        img_tag = soup.find("img", id="mov")
        image_url = img_tag["src"] if img_tag and img_tag.get("src") else DEFAULT_IMAGE_URL
        location_tag = soup.find("span", class_="auto-style3")
        location = location_tag.text.strip().replace("/ ", "") if location_tag else DEFAULT_LOCATION
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
        update.message.reply_text("請提供網址！用法：/seturl <網址>")
        return

    new_url = context.args[0]
    if not new_url.startswith("http"):
        update.message.reply_text("請提供有效的網址（以 http 或 https 開頭）！")
        return

    try:
        response = session.get(new_url, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        img_tag = soup.find("img", id="mov")
        if not img_tag or not img_tag.get("src"):
            update.message.reply_text("該網址無效，無法找到圖片（<img id='mov'>）！")
            return
    except Exception as e:
        update.message.reply_text(f"錯誤：無法訪問網址 ({str(e)})")
        return
    finally:
        if 'response' in locals():
            response.close()

    user_page_url = new_url
    update.message.reply_text(f"網址已設定為：{new_url}\n使用 /start 開始傳送圖片。")
    logger.info(f"網址設定為：{new_url}，聊天 ID：{update.effective_chat.id}")

def start(update: Update, context: CallbackContext):
    global running, task, run_event
    if running:
        update.message.reply_text("Bot 已經在運行！")
        return

    logger.info(f"收到 /start 指令，聊天 ID：{chat_id}")
    running = True
    run_event.set()
    save_running_state(running)
    update.message.reply_text("Bot 已啟動，將每分鐘傳送最新圖片。使用 /stop 停止。")
    if task is None or task.done():
        asyncio.run_coroutine_threadsafe(start_send_images(), loop)
    logger.info(f"啟動圖片傳送任務，聊天 ID：{chat_id}")
    global last_task_check
    last_task_check = time.time()

def resume(update: Update, context: CallbackContext):
    global running, task, run_event
    if running:
        update.message.reply_text("Bot 已經在運行！")
        return

    logger.info(f"收到 /resume 指令，聊天 ID：{chat_id}")
    running = True
    run_event.set()
    save_running_state(running)
    update.message.reply_text("Bot 已恢復，將每分鐘傳送圖片。")
    if task is None or task.done():
        asyncio.run_coroutine_threadsafe(start_send_images(), loop)
    logger.info(f"恢復圖片傳送任務，聊天 ID：{chat_id}")
    global last_task_check
    last_task_check = time.time()

def stop(update: Update, context: CallbackContext):
    global running, task, run_event
    if not running:
        update.message.reply_text("Bot 已經停止！")
        return

    logger.info(f"收到 /stop 指令，聊天 ID：{chat_id}")
    running = False
    run_event.clear()
    save_running_state(running)
    if task and not task.done():
        task.cancel()
        logger.info("任務已取消")
    update.message.reply_text("Bot 已停止。使用 /resume 恢復。")
    logger.info("停止圖片傳送任務")

async def start_send_images():
    global task
    if task is None or task.done():
        task = loop.create_task(send_images())
        logger.info("創建新的 send_images 任務")

async def send_images():
    global user_page_url, running, last_task_check, last_image_url
    logger.info(f"開始圖片傳送循環，聊天 ID：{chat_id}")
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
            request_url = f"{current_url.split('?')[0]}?{int(asyncio.get_event_loop().time() * 1000)}"
            logger.info(f"請求圖片網址：{request_url}")
            request_start = time.time()
            response = session.get(request_url, timeout=5)
            logger.info(f"圖片請求耗時：{time.time() - request_start:.2f}s")
            if response.status_code == 200:
                jst_time = datetime.now(ZoneInfo("Asia/Tokyo"))
                caption = f"{jst_time.strftime('%Y-%m-%d JST %H:%M')} {location} (Source: 富士五湖TV)"
                logger.info(f"準備傳送圖片，caption：{caption}")
                telegram_start = time.time()
                url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
                files = {"photo": response.content}
                data = {"chat_id": chat_id, "caption": caption}
                telegram_response = session.post(url, data=data, files=files, timeout=5)
                logger.info(f"Telegram 傳送耗時：{time.time() - telegram_start:.2f}s")
                if telegram_response.status_code == 200:
                    logger.info(f"圖片傳送成功，網址：{current_url}，地點：{location}，時間：{caption}")
                else:
                    logger.error(f"圖片傳送失敗，狀態碼：{telegram_response.status_code}，回應：{telegram_response.text}")
                response.close()
                telegram_response.close()
            else:
                logger.warning(f"無法下載圖片，網址：{request_url}，狀態碼：{response.status_code}")
                response.close()
            process = psutil.Process()
            mem_info = process.memory_info()
            logger.info(f"內存使用：RSS={mem_info.rss / 1024 / 1024:.2f}MB, VMS={mem_info.vms / 1024 / 1024:.2f}MB")
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
            logger.info(f"圖片傳送任務被取消，聊天 ID：{chat_id}")
            run_event.clear()
            break
        except Exception as e:
            logger.error(f"圖片傳送流程異常：{e}\n{traceback.format_exc()}")
            try:
                url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                data = {"chat_id": chat_id, "text": f"圖片傳送異常：{str(e)}，將在 10 秒後重試"}
                session.post(url, json=data, timeout=5).close()
            except Exception as notify_e:
                logger.error(f"通知用戶失敗：{notify_e}")
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
            logger.info(f"看門狗檢查：running={running}, task_exists={task is not None}, task_done={task.done() if task else True}, last_task_check={current_time - last_task_check:.2f}s, task_exception={task_exception}, memory_rss={mem_info.rss / 1024 / 1024:.2f}MB")
            if running and (not task_status or task_exception or (current_time - last_task_check > 90)):
                logger.warning(f"檢測到任務可能停止，last_task_check={last_task_check}, current_time={current_time}, task_exception={task_exception}")
                if task and not task.done():
                    task.cancel()
                running = True
                save_running_state(running)
                run_event.set()
                task = loop.create_task(send_images())
                last_task_check = time.time()
                logger.info(f"重啟圖片傳送任務，聊天 ID：{chat_id}")
                if current_time - last_notify_time > 300:
                    try:
                        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                        data = {"chat_id": chat_id, "text": "圖片傳送任務已重啟"}
                        session.post(url, json=data, timeout=5).close()
                        last_notify_time = current_time
                    except Exception as e:
                        logger.error(f"通知用戶失敗：{e}")
            elif not running and task_status:
                logger.warning("running=False 但任務仍在運行，取消任務")
                task.cancel()
                save_running_state(False)
            elif not running and (current_time - last_task_check > 600):
                if current_time - last_notify_time > 600:
                    logger.warning("任務長時間未運行，通知用戶")
                    try:
                        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                        data = {"chat_id": chat_id, "text": "Bot 已停止，請使用 /start 或 /resume 恢復"}
                        session.post(url, json=data, timeout=5).close()
                        last_notify_time = current_time
                    except Exception as e:
                        logger.error(f"通知用戶失敗：{e}")
            if mem_info.rss / 1024 / 1024 > 450:
                logger.warning("內存使用過高，可能導致服務終止")
                try:
                    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                    data = {"chat_id": chat_id, "text": f"警告：內存使用過高 ({mem_info.rss / 1024 / 1024:.2f}MB)，可能導致服務終止"}
                    session.post(url, json=data, timeout=5).close()
                except Exception as e:
                    logger.error(f"通知用戶失敗：{e}")
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
                "chat_id": chat_id,
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
        data = {"chat_id": chat_id, "text": "服務即將終止，將嘗試在重啟後恢復"}
        session.post(url, json=data, timeout=5).close()
    except Exception as e:
        logger.error(f"通知用戶失敗：{e}")
    sys.exit(0)

def initialize_bot():
    global updater, running, task, run_event, loop
    try:
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        run_event = asyncio.Event()
        running = load_running_state()
        logger.info(f"載入 running 狀態：{running}")
        if running:
            run_event.set()
            logger.info("檢測到 running=True，準備自動恢復圖片傳送")
            try:
                url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                data = {"chat_id": chat_id, "text": "服務已重啟，自動恢復圖片傳送"}
                session.post(url, json=data, timeout=5).close()
            except Exception as e:
                logger.error(f"通知用戶失敗：{e}")
        updater = Updater(TOKEN, use_context=True)
        dispatcher = updater.dispatcher
        dispatcher.add_handler(CommandHandler("seturl", seturl))
        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(CommandHandler("resume", resume))
        dispatcher.add_handler(CommandHandler("stop", stop))
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
            logger.info(f"初始化時自動啟動圖片傳送任務，聊天 ID：{chat_id}")
    except Exception as e:
        logger.error(f"初始化 Bot 失敗：{e}\n{traceback.format_exc()}")
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            data = {"chat_id": chat_id, "text": f"初始化 Bot 失敗：{str(e)}"}
            session.post(url, json=data, timeout=5).close()
        except Exception as notify_e:
            logger.error(f"通知用戶失敗：{notify_e}")
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
            data = {"chat_id": chat_id, "text": f"事件循環異常：{str(e)}"}
            session.post(url, json=data, timeout=5).close()
        except Exception as notify_e:
            logger.error(f"通知用戶失敗：{notify_e}")
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
        task_exception = task.exception() if task and task.done() else None
        status = {
            "running": running,
            "task_active": task is not None and not task.done(),
            "last_task_check": last_task_check,
            "timestamp": time.time(),
            "memory_rss_mb": mem_info.rss / 1024 / 1024,
            "memory_vms_mb": mem_info.vms / 1024 / 1024,
            "task_exception": str(task_exception) if task_exception else None
        }
        return Response(f"Bot is running: {status}", status=200)
    except Exception as e:
        logger.error(f"健康檢查失敗：{e}\n{traceback.format_exc()}")
        return Response("Health check failed", status=500)

if __name__ == "__main__":
    try:
        threading.Thread(target=run_event_loop, daemon=True).start()
        initialize_bot()
        port = int(os.getenv("PORT", 8443))
        logger.info(f"啟動 Flask 伺服器，端口：{port}")
        flask_app.run(host="0.0.0.0", port=port)
    except Exception as e:
        logger.error(f"主程式異常：{e}\n{traceback.format_exc()}")
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            data = {"chat_id": chat_id, "text": f"主程式異常：{str(e)}"}
            session.post(url, json=data, timeout=5).close()
        except Exception as notify_e:
            logger.error(f"通知用戶失敗：{notify_e}")