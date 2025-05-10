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
chat_id = 48732810  # 固定聊天 ID，可改為動態

# Flask 應用
flask_app = Flask(__name__)

# Telegram Updater
updater = None

async def get_latest_image_url(page_url):
    """從指定頁面爬取圖片網址和地點名稱"""
    try:
        response = requests.get(page_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        # 提取圖片網址
        img_tag = soup.find("img", id="mov")
        image_url = img_tag["src"] if img_tag and img_tag.get("src") else DEFAULT_IMAGE_URL
        # 提取地點名稱
        location_tag = soup.find("span", class_="auto-style3")
        location = location_tag.text.strip().replace("/ ", "") if location_tag else DEFAULT_LOCATION
        logger.info(f"成功爬取圖片網址：{image_url}，地點：{location}（頁面：{page_url}）")
        return image_url, location
    except Exception as e:
        logger.error(f"爬取圖片網址或地點失敗（頁面：{page_url}）：{e}\n{traceback.format_exc()}")
        return DEFAULT_IMAGE_URL, DEFAULT_LOCATION

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
        response = requests.get(new_url, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        img_tag = soup.find("img", id="mov")
        if not img_tag or not img_tag.get("src"):
            update.message.reply_text("該網址無效，無法找到圖片（<img id='mov'>）！")
            return
    except Exception as e:
        update.message.reply_text(f"錯誤：無法訪問網址 ({str(e)})")
        return

    user_page_url = new_url
    update.message.reply_text(f"網址已設定為：{new_url}\n使用 /start 開始傳送圖片。")
    logger.info(f"網址設定為：{new_url}，聊天 ID：{update.effective_chat.id}")

def start(update: Update, context: CallbackContext):
    global running, task
    if running:
        update.message.reply_text("Bot 已經在運行！")
        return

    running = True
    update.message.reply_text("Bot 已啟動，將每分鐘傳送最新圖片。使用 /stop 停止。")
    # 在事件循環中安全啟動異步任務
    future = asyncio.run_coroutine_threadsafe(
        send_images(), loop
    )
    task = future
    logger.info(f"啟動圖片傳送任務，聊天 ID：{chat_id}")
    global last_task_check
    last_task_check = time.time()

def resume(update: Update, context: CallbackContext):
    global running, task
    if running:
        update.message.reply_text("Bot 已經在運行！")
        return

    running = True
    update.message.reply_text("Bot 已恢復，將每分鐘傳送最新圖片。")
    # 在事件循環中安全啟動異步任務
    future = asyncio.run_coroutine_threadsafe(
        send_images(), loop
    )
    task = future
    logger.info(f"恢復圖片傳送任務，聊天 ID：{chat_id}")
    global last_task_check
    last_task_check = time.time()

def stop(update: Update, context: CallbackContext):
    global running, task
    if not running:
        update.message.reply_text("Bot 已經停止！")
        return

    running = False
    if task:
        task.cancel()
        logger.info("任務已取消")
    update.message.reply_text("Bot 已停止。使用 /resume 恢復。")
    logger.info("停止圖片傳送任務")

async def send_images():
    global user_page_url, running, last_task_check
    logger.info(f"開始圖片傳送循環，聊天 ID：{chat_id}")
    while running:
        try:
            # 更新任務檢查時間
            last_task_check = time.time()
            # 獲取圖片網址和地點
            current_url, location = await get_latest_image_url(user_page_url)
            # 移除舊查詢參數，添加新時間戳
            request_url = f"{current_url.split('?')[0]}?{int(asyncio.get_event_loop().time() * 1000)}"
            logger.info(f"請求圖片網址：{request_url}")
            response = requests.get(request_url, timeout=10)
            if response.status_code == 200:
                jst_time = datetime.now(ZoneInfo("Asia/Tokyo"))
                caption = f"{jst_time.strftime('%Y-%m-%d JST %H:%M')} {location} (Source: 富士五湖TV)"
                logger.info(f"準備傳送圖片，聊天 ID：{chat_id}，caption：{caption}")
                # 使用 Telegram HTTP API 發送圖片
                url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
                files = {"photo": response.content}
                data = {"chat_id": chat_id, "caption": caption}
                telegram_response = requests.post(url, data=data, files=files, timeout=10)
                if telegram_response.status_code == 200:
                    logger.info(f"圖片傳送成功，網址：{current_url}，地點：{location}，時間：{caption}，聊天 ID：{chat_id}")
                else:
                    logger.error(f"圖片傳送失敗，狀態碼：{telegram_response.status_code}，回應：{telegram_response.text}")
            else:
                logger.warning(f"無法下載圖片，網址：{request_url}，狀態碼：{response.status_code}")
        except asyncio.CancelledError:
            logger.info(f"圖片傳送任務被取消，聊天 ID：{chat_id}")
            running = False
            break
        except Exception as e:
            logger.error(f"圖片傳送流程異常：{e}\n{traceback.format_exc()}")
        except BaseException as e:
            logger.error(f"圖片傳送流程系統級異常：{e}\n{traceback.format_exc()}")
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info(f"圖片傳送任務被取消（睡眠期間），聊天 ID：{chat_id}")
            running = False
            break
        except Exception as e:
            logger.error(f"睡眠期間異常：{e}\n{traceback.format_exc()}")
    logger.info(f"圖片傳送循環結束，聊天 ID：{chat_id}")

async def watchdog():
    """看門狗任務，檢查 send_images 是否運行"""
    global running, task, last_task_check
    logger.info("看門狗任務啟動")
    while True:
        try:
            current_time = time.time()
            if running and (current_time - last_task_check > 300):  # 放寬到 300 秒
                logger.warning(f"檢測到任務可能停止，last_task_check={last_task_check}, current_time={current_time}")
                if task and task.done():
                    logger.error("任務已終止，重新啟動")
                    running = False
                    task = None
                if not running:
                    running = True
                    future = asyncio.run_coroutine_threadsafe(send_images(), loop)
                    task = future
                    last_task_check = time.time()
                    logger.info(f"重啟圖片傳送任務，聊天 ID：{chat_id}")
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"看門狗異常：{e}\n{traceback.format_exc()}")
            await asyncio.sleep(30)

async def keep_alive():
    """定時發送健康檢查訊息，防止 Render 閒置"""
    logger.info("Keep-alive 任務啟動")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": f"Bot is alive at {datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d JST %H:%M')}"
            }
            response = requests.post(url, json=data, timeout=10)
            if response.status_code == 200:
                logger.info("Keep-alive 訊息發送成功")
            else:
                logger.error(f"Keep-alive 訊息發送失敗，狀態碼：{response.status_code}，回應：{response.text}")
            await asyncio.sleep(600)  # 每 10 分鐘
        except Exception as e:
            logger.error(f"Keep-alive 異常：{e}\n{traceback.format_exc()}")
            await asyncio.sleep(600)

def initialize_bot():
    global updater
    try:
        # 初始化 Updater
        updater = Updater(TOKEN, use_context=True)
        dispatcher = updater.dispatcher

        # 添加命令處理器
        dispatcher.add_handler(CommandHandler("seturl", seturl))
        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(CommandHandler("resume", resume))
        dispatcher.add_handler(CommandHandler("stop", stop))

        # 設置 Telegram 命令選單
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

        # 設置 Webhook
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/setWebhook",
            json={"url": WEBHOOK_URL}
        )
        if response.status_code == 200:
            logger.info(f"Bot 正在運行，Webhook 已設定為 {WEBHOOK_URL}")
        else:
            logger.error(f"設置 Webhook 失敗：{response.text}")

        # 啟動看門狗和 keep-alive 任務
        asyncio.run_coroutine_threadsafe(watchdog(), loop)
        asyncio.run_coroutine_threadsafe(keep_alive(), loop)
        logger.info("看門狗和 Keep-alive 任務已啟動")
    except Exception as e:
        logger.error(f"初始化 Bot 失敗：{e}\n{traceback.format_exc()}")
        raise

def run_event_loop():
    """在獨立線程中運行事件循環"""
    global loop
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.info("事件循環啟動")
        loop.run_forever()
    except Exception as e:
        logger.error(f"事件循環異常停止：{e}\n{traceback.format_exc()}")
    except BaseException as e:
        logger.error(f"事件循環系統級異常停止：{e}\n{traceback.format_exc()}")
    finally:
        logger.info("事件循環結束")
        loop.close()

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    global updater
    logger.info("收到 Webhook 請求")
    if updater is None:
        logger.warning("Updater 未初始化，正在初始化")
        initialize_bot()
    try:
        update = Update.de_json(request.get_json(), updater.bot)
        if update:
            updater.dispatcher.process_update(update)
            logger.info("Webhook 請求處理成功")
        else:
            logger.warning("無效的 Webhook 請求")
        global last_task_check
        last_task_check = time.time()  # 更新任務檢查時間
        return Response(status=200)
    except Exception as e:
        logger.error(f"處理 Webhook 請求失敗：{e}\n{traceback.format_exc()}")
        return Response(status=500)

@flask_app.route("/", methods=["GET"])
def health_check():
    """健康檢查端點"""
    logger.info("收到健康檢查請求")
    global running, task
    status = {
        "running": running,
        "task_active": task is not None and not task.done(),
        "last_task_check": last_task_check,
        "timestamp": time.time()
    }
    return Response(f"Bot is running: {status}", status=200)

if __name__ == "__main__":
    try:
        # 啟動事件循環線程
        threading.Thread(target=run_event_loop, daemon=True).start()
        # 初始化 Bot
        initialize_bot()
        port = int(os.getenv("PORT", 8443))
        logger.info(f"啟動 Flask 伺服器，端口：{port}")
        flask_app.run(host="0.0.0.0", port=port)
    except Exception as e:
        logger.error(f"主程式異常：{e}\n{traceback.format_exc()}")
    except BaseException as e:
        logger.error(f"主程式系統級異常：{e}\n{traceback.format_exc()}")