import asyncio
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
from bs4 import BeautifulSoup
import os
from flask import Flask, request, Response

# 從環境變數獲取 Telegram Bot Token 和 Webhook URL
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
CAMERA_PAGE_URL = "https://live.fujigoko.tv/?n=26&b=0"
DEFAULT_URL = "https://cam.fujigoko.tv/livecam26/cam1_9892.jpg"

# 用於控制任務的全局變數
running = False
task = None

# Flask 應用
flask_app = Flask(__name__)

# Telegram Updater
updater = None

async def get_latest_image_url():
    """從 live.fujigoko.tv 爬取最新的圖片網址"""
    try:
        response = requests.get(CAMERA_PAGE_URL, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        img_tag = soup.find("img", id="mov")
        if img_tag and img_tag.get("src"):
            return img_tag["src"]
        print("未找到圖片網址，使用預設網址")
        return DEFAULT_URL
    except Exception as e:
        print(f"爬取圖片網址失敗: {e}")
        return DEFAULT_URL

def start(update: Update, context: CallbackContext):
    global running, task
    if running:
        update.message.reply_text("Bot 已經在運行！")
        return

    running = True
    update.message.reply_text("Bot 已啟動，將每分鐘傳送最新圖片。使用 /stop 停止。")
    task = asyncio.create_task(send_images(update.effective_chat.id, context.bot))

def resume(update: Update, context: CallbackContext):
    global running, task
    if running:
        update.message.reply_text("Bot 已經在運行！")
        return

    running = True
    update.message.reply_text("Bot 已恢復，將每分鐘傳送最新圖片。")
    task = asyncio.create_task(send_images(update.effective_chat.id, context.bot))

def stop(update: Update, context: CallbackContext):
    global running, task
    if not running:
        update.message.reply_text("Bot 已經停止！")
        return

    running = False
    if task:
        task.cancel()
    update.message.reply_text("Bot 已停止。使用 /resume 恢復。")

async def send_images(chat_id: int, bot):
    while running:
        try:
            # 每次傳送前獲取最新圖片網址
            current_url = await get_latest_image_url()
            url = f"{current_url}?{int(asyncio.get_event_loop().time() * 1000)}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                jst_time = datetime.now(ZoneInfo("Asia/Tokyo"))
                caption = f"{jst_time.strftime('%Y-%m-%d JST %H:%M')} (Source: 富士五湖TV)"
                await bot.send_photo(chat_id=chat_id, photo=response.content, caption=caption)
                print(f"圖片已傳送，網址：{current_url}，時間：{caption}")
            else:
                print(f"無法下載圖片，網址：{current_url}，狀態碼：{response.status_code}")
        except Exception as e:
            print(f"傳送圖片失敗: {e}")
        await asyncio.sleep(60)

def initialize_bot():
    global updater
    # 初始化 Updater
    updater = Updater(TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    # 添加命令處理器
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("resume", resume))
    dispatcher.add_handler(CommandHandler("stop", stop))

    # 設置 Telegram 命令選單
    bot = Bot(TOKEN)
    bot.set_my_commands([
        ("start", "開始每分鐘傳送最新圖片"),
        ("resume", "恢復傳送圖片"),
        ("stop", "停止傳送圖片")
    ])
    print("Telegram 命令選單已設置")

    # 設置 Webhook
    updater.bot.set_webhook(WEBHOOK_URL)
    print(f"Bot 正在運行，Webhook 已設定為 {WEBHOOK_URL}")

# Flask Webhook 端點
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    global updater
    if updater is None:
        initialize_bot()
    update = Update.de_json(request.get_json(), updater.bot)
    updater.dispatcher.process_update(update)
    return Response(status=200)

if __name__ == "__main__":
    # 初始化 Bot
    initialize_bot()
    port = int(os.getenv("PORT", 8443))
    flask_app.run(host="0.0.0.0", port=port)