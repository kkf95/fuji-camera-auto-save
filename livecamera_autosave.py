import asyncio
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
import os
from flask import Flask, request, Response

# 從環境變數獲取 Telegram Bot Token 和 Webhook URL
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
DEFAULT_URL = "https://cam.fujigoko.tv/livecam26/cam1_9892.jpg"

# 用於控制任務和儲存網址的全局變數
running = False
task = None
current_url = DEFAULT_URL

# Flask 應用
flask_app = Flask(__name__)

# Telegram Updater
updater = None

def seturl(update: Update, context: CallbackContext):
    global current_url
    if not context.args:
        update.message.reply_text("請提供圖片網址！用法：/seturl <圖片網址>")
        return

    new_url = context.args[0]
    if not new_url.startswith("http") or not new_url.lower().endswith((".jpg", ".jpeg", ".png")):
        update.message.reply_text("請提供有效的圖片網址（支援 JPG、JPEG 或 PNG 格式）！")
        return

    try:
        response = requests.get(f"{new_url}?{int(asyncio.get_event_loop().time() * 1000)}", timeout=5)
        if response.status_code != 200:
            update.message.reply_text("無法訪問該網址，請確認網址正確！")
            return
    except Exception as e:
        update.message.reply_text(f"錯誤：無法訪問網址 ({str(e)})")
        return

    current_url = new_url
    update.message.reply_text(f"圖片網址已設定為：{new_url}\n使用 /start 開始傳送圖片。")

def start(update: Update, context: CallbackContext):
    global running, task
    if running:
        update.message.reply_text("Bot 已經在運行！")
        return

    running = True
    update.message.reply_text(f"Bot 已啟動，將每分鐘傳送圖片（網址：{current_url}）。使用 /stop 停止。")
    task = asyncio.create_task(send_images(update.effective_chat.id, context.bot))

def resume(update: Update, context: CallbackContext):
    global running, task
    if running:
        update.message.reply_text("Bot 已經在運行！")
        return

    running = True
    update.message.reply_text(f"Bot 已恢復，將每分鐘傳送圖片（網址：{current_url}）。")
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
    global current_url
    while running:
        try:
            url = f"{current_url}?{int(asyncio.get_event_loop().time() * 1000)}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                jst_time = datetime.now(ZoneInfo("Asia/Tokyo"))
                caption = jst_time.strftime("%Y-%m-%d JST %H:%M")
                await bot.send_photo(chat_id=chat_id, photo=response.content, caption=caption)
                print(f"圖片已傳送，時間：{caption}")
            else:
                print(f"無法下載圖片，狀態碼：{response.status_code}")
        except Exception as e:
            print(f"錯誤: {e}")
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
        ("seturl", "設置圖片網址（例如 /seturl <網址>）"),
        ("start", "開始每分鐘傳送圖片"),
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
    update = Update.de_json(request.get_json(), updater.bot)
    updater.dispatcher.process_update(update)
    return Response(status=200)

# 啟動時初始化 Bot
@flask_app.before_first_request
def before_first_request():
    initialize_bot()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8443))
    flask_app.run(host="0.0.0.0", port=port)