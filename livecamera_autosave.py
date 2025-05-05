import asyncio
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import os

# 從環境變數獲取 Telegram Bot Token 和 Webhook URL
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
DEFAULT_URL = "https://cam.fujigoko.tv/livecam26/cam1_9892.jpg"

# 用於控制任務和儲存網址的全局變數
running = False
task = None
current_url = DEFAULT_URL

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

# 初始化 Application 並創建 ASGI 應用
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("seturl", seturl))
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("resume", resume))
application.add_handler(CommandHandler("stop", stop))

# 設置 Webhook
async def initialize_webhook():
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print(f"Bot 正在運行，Webhook 已設定為 {WEBHOOK_URL}")

# 創建 ASGI 應用
app = application.create_webhook_application()

# 在啟動時設置 Webhook
asyncio.run(initialize_webhook())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8443)))
