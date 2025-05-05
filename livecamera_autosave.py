import asyncio
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, ApplicationBuilder
import os
from fastapi import FastAPI, Request, Response

# 從環境變數獲取 Telegram Bot Token 和 Webhook URL
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
DEFAULT_URL = "https://cam.fujigoko.tv/livecam26/cam1_9892.jpg"

# 用於控制任務和儲存網址的全局變數
running = False
task = None
current_url = DEFAULT_URL

# FastAPI 應用
fastapi_app = FastAPI()

# Telegram Application
telegram_app = None

async def seturl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_url
    if not context.args:
        await update.message.reply_text("請提供圖片網址！用法：/seturl <圖片網址>")
        return

    new_url = context.args[0]
    if not new_url.startswith("http") or not new_url.lower().endswith((".jpg", ".jpeg", ".png")):
        await update.message.reply_text("請提供有效的圖片網址（支援 JPG、JPEG 或 PNG 格式）！")
        return

    try:
        response = requests.get(f"{new_url}?{int(asyncio.get_event_loop().time() * 1000)}", timeout=5)
        if response.status_code != 200:
            await update.message.reply_text("無法訪問該網址，請確認網址正確！")
            return
    except Exception as e:
        await update.message.reply_text(f"錯誤：無法訪問網址 ({str(e)})")
        return

    current_url = new_url
    await update.message.reply_text(f"圖片網址已設定為：{new_url}\n使用 /start 開始傳送圖片。")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global running, task
    if running:
        await update.message.reply_text("Bot 已經在運行！")
        return

    running = True
    await update.message.reply_text(f"Bot 已啟動，將每分鐘傳送圖片（網址：{current_url}）。使用 /stop 停止。")
    task = asyncio.create_task(send_images(update.effective_chat.id, context.bot))

async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global running, task
    if running:
        await update.message.reply_text("Bot 已經在運行！")
        return

    running = True
    await update.message.reply_text(f"Bot 已恢復，將每分鐘傳送圖片（網址：{current_url}）。")
    task = asyncio.create_task(send_images(update.effective_chat.id, context.bot))

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global running, task
    if not running:
        await update.message.reply_text("Bot 已經停止！")
        return

    running = False
    if task:
        task.cancel()
    await update.message.reply_text("Bot 已停止。使用 /resume 恢復。")

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

async def initialize_bot():
    global telegram_app
    # 初始化 Application
    telegram_app = ApplicationBuilder().token(TOKEN).build()

    # 添加命令處理器
    telegram_app.add_handler(CommandHandler("seturl", seturl))
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("resume", resume))
    telegram_app.add_handler(CommandHandler("stop", stop))

    # 設置 Telegram 命令選單
    commands = [
        BotCommand("seturl", "設置圖片網址（例如 /seturl <網址>）"),
        BotCommand("start", "開始每分鐘傳送圖片"),
        BotCommand("resume", "恢復傳送圖片"),
        BotCommand("stop", "停止傳送圖片")
    ]
    await telegram_app.bot.set_my_commands(commands)
    print("Telegram 命令選單已設置")

    # 設置 Webhook
    await telegram_app.bot.set_webhook(url=WEBHOOK_URL)
    print(f"Bot 正在運行，Webhook 已設定為 {WEBHOOK_URL}")

    # 初始化 Application
    await telegram_app.initialize()

# FastAPI Webhook 端點
@fastapi_app.post("/webhook")
async def webhook(request: Request):
    global telegram_app
    update = await request.json()
    await telegram_app.process_update(Update.de_json(update, telegram_app.bot))
    return Response(status_code=200)

# 啟動時初始化 Bot
@fastapi_app.on_event("startup")
async def on_startup():
    await initialize_bot()

# 關閉時清理
@fastapi_app.on_event("shutdown")
async def on_shutdown():
    global telegram_app
    if telegram_app:
        await telegram_app.shutdown()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8443))
    uvicorn.run(fastapi_app, host="0.0.0.0", port=port)