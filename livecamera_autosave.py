import asyncio
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
from bs4 import BeautifulSoup
import os
from flask import Flask, request, Response
import threading
import logging

# 設置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 從環境變數獲取 Telegram Bot Token 和 Webhook URL
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
DEFAULT_PAGE_URL = "https://live.fujigoko.tv/?n=26&b=0"
DEFAULT_IMAGE_URL = "https://cam.fujigoko.tv/livecam26/cam1_9892.jpg"

# 用於控制任務和儲存網址的全局變數
running = False
task = None
user_page_url = DEFAULT_PAGE_URL
loop = None

# Flask 應用
flask_app = Flask(__name__)

# Telegram Updater
updater = None

async def get_latest_image_url(page_url):
    """從指定頁面爬取最新的圖片網址"""
    try:
        response = requests.get(page_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        img_tag = soup.find("img", id="mov")
        if img_tag and img_tag.get("src"):
            logger.info(f"成功爬取圖片網址：{img_tag['src']}（頁面：{page_url}）")
            return img_tag["src"]
        logger.warning(f"未找到圖片網址（頁面：{page_url}），使用預設圖片網址")
        return DEFAULT_IMAGE_URL
    except Exception as e:
        logger.error(f"爬取圖片網址失敗（頁面：{page_url}）：{e}")
        return DEFAULT_IMAGE_URL

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
        send_images(update.effective_chat.id, context.bot), loop
    )
    task = future
    logger.info(f"啟動圖片傳送任務，聊天 ID：{update.effective_chat.id}")

def resume(update: Update, context: CallbackContext):
    global running, task
    if running:
        update.message.reply_text("Bot 已經在運行！")
        return

    running = True
    update.message.reply_text("Bot 已恢復，將每分鐘傳送最新圖片。")
    # 在事件循環中安全啟動異步任務
    future = asyncio.run_coroutine_threadsafe(
        send_images(update.effective_chat.id, context.bot), loop
    )
    task = future
    logger.info(f"恢復圖片傳送任務，聊天 ID：{update.effective_chat.id}")

def stop(update: Update, context: CallbackContext):
    global running, task
    if not running:
        update.message.reply_text("Bot 已經停止！")
        return

    running = False
    if task:
        task.cancel()
    update.message.reply_text("Bot 已停止。使用 /resume 恢復。")
    logger.info("停止圖片傳送任務")

async def send_images(chat_id: int, bot):
    global user_page_url
    while running:
        try:
            # 每次傳送前獲取最新圖片網址
            current_url = await get_latest_image_url(user_page_url)
            # 移除舊查詢參數，添加新時間戳
            request_url = f"{current_url.split('?')[0]}?{int(asyncio.get_event_loop().time() * 1000)}"
            logger.info(f"請求圖片網址：{request_url}")
            response = requests.get(request_url, timeout=10)
            if response.status_code == 200:
                jst_time = datetime.now(ZoneInfo("Asia/Tokyo"))
                caption = f"{jst_time.strftime('%Y-%m-%d JST %H:%M')} (Source: 富士五湖TV)"
                logger.info(f"準備傳送圖片，聊天 ID：{chat_id}，caption：{caption}")
                # 確保只 await 一次，且不對返回值再次 await
                message = await bot.send_photo(chat_id=chat_id, photo=response.content, caption=caption)
                logger.info(f"圖片傳送成功，網址：{current_url}，時間：{caption}，聊天 ID：{chat_id}")
            else:
                logger.warning(f"無法下載圖片，網址：{request_url}，狀態碼：{response.status_code}")
        except Exception as e:
            logger.error(f"傳送圖片失敗：{e}")
        await asyncio.sleep(60)

def initialize_bot():
    global updater
    # 初始化 Updater
    updater = Updater(TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    # 添加命令處理器
    dispatcher.add_handler(CommandHandler("seturl", seturl))
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("resume", resume))
    dispatcher.add_handler(CommandHandler("stop", stop))

    # 設置 Telegram 命令選單
    bot = Bot(TOKEN)
    bot.set_my_commands([
        ("seturl", "設置圖片頁面網址（例如 /seturl <網址>）"),
        ("start", "開始每分鐘傳送最新圖片"),
        ("resume", "恢復傳送圖片"),
        ("stop", "停止傳送圖片")
    ])
    logger.info("Telegram 命令選單已設置")

    # 設置 Webhook
    updater.bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Bot 正在運行，Webhook 已設定為 {WEBHOOK_URL}")

def run_event_loop():
    """在獨立線程中運行事件循環"""
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_forever()
    logger.info("事件循環已啟動")

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
        return Response(status=200)
    except Exception as e:
        logger.error(f"處理 Webhook 請求失敗：{e}")
        return Response(status=500)

@flask_app.route("/", methods=["GET"])
def health_check():
    """健康檢查端點"""
    logger.info("收到健康檢查請求")
    return Response("Bot is running", status=200)

if __name__ == "__main__":
    # 啟動事件循環線程
    threading.Thread(target=run_event_loop, daemon=True).start()
    # 初始化 Bot
    initialize_bot()
    port = int(os.getenv("PORT", 8443))
    logger.info(f"啟動 Flask 伺服器，端口：{port}")
    flask_app.run(host="0.0.0.0", port=port)