import asyncio 
import sqlite3 
import logging 
import os 
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile 
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes 
from telegram.constants import ParseMode 
from telegram.error import BadRequest, Forbidden 
import yt_dlp 

# --- خواندن تنظیمات از متغیرهای محیطی سرور --- 
try:
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    ADMIN_ID = int(os.environ.get("ADMIN_ID"))
    CHANNELS_STR = os.environ.get("REQUIRED_CHANNELS", "")
    REQUIRED_CHANNELS = [channel.strip() for channel in CHANNELS_STR.split(',') if channel.strip()]
except (TypeError, ValueError) as e:
    print("خطا: متغیرهای محیطی (TOKEN, ADMIN_ID, CHANNELS) به درستی تنظیم نشده‌اند.")
    exit()

# --- راه‌اندازی لاگ و پایگاه داده ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DB_FILE = "/var/data/users.db"  # مسیر قابل نوشتن در سرور Render

def setup_database():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, first_name TEXT)")
    conn.commit()
    conn.close()

def add_user_to_db(user_id: int, first_name: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, first_name) VALUES (?, ?)", (user_id, first_name))
    conn.commit()
    conn.close()

# ... (بقیه کد بدون تغییر باقی می‌ماند) ...
# (کد کامل توابع check_membership, start_command, handle_link و غیره در اینجا قرار می‌گیرد)
# (برای جلوگیری از طولانی شدن، همان کد قبلی در اینجا فرض می‌شود)

async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not REQUIRED_CHANNELS:
        return True
    
    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception:
            return False
    return True

async def show_join_channels_message(update: Update):
    buttons = [[InlineKeyboardButton(f"عضویت در کانال {i+1}", url=f"https://t.me/{channel.lstrip('@')}")] 
               for i, channel in enumerate(REQUIRED_CHANNELS)]
    buttons.append([InlineKeyboardButton("✅ بررسی عضویت", callback_data="check_join")])
    keyboard = InlineKeyboardMarkup(buttons)
    
    text = "❗️ **توجه**\n\nبرای استفاده از ربات، ابتدا در کانال‌های زیر عضو شوید و سپس روی دکمه 'بررسی عضویت' کلیک کنید."
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user_to_db(user.id, user.first_name)
    
    if not await check_membership(user.id, context):
        await show_join_channels_message(update)
        return
    
    await update.message.reply_text(f"سلام {user.first_name}! 👋\n\nلینک خود را برای دانلود ارسال کنید.")

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_membership(update.effective_user.id, context):
        await show_join_channels_message(update)
        return
    
    url = update.message.text
    msg = await update.message.reply_text("⏳ در حال بررسی لینک...")
    
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
            title = info_dict.get('title', 'فایل بدون عنوان')
            formats = info_dict.get('formats', [])
            
            buttons = []
            for f in formats:
                if (f.get('vcodec') != 'none' and f.get('acodec') != 'none') or (f.get('acodec') != 'none' and f.get('vcodec') == 'none'):
                    size_mb = (f.get('filesize') or f.get('filesize_approx') or 0) / (1024*1024)
                    size_str = f"{size_mb:.2f} MB" if size_mb > 0 else ""
                    icon = "🎬" if f.get('vcodec') != 'none' else "🎵"
                    label = f"{icon} {f.get('height', '') or f.get('abr', '')}{'p' if f.get('height') else 'k'} ({f.get('ext')}) {size_str}"
                    callback_data = f"dl_{f['format_id']}_{info_dict['id']}"
                    buttons.append([InlineKeyboardButton(label, callback_data=callback_data)])
            
            if not buttons:
                await msg.edit_text("کیفیتی برای دانلود یافت نشد.")
                return
            
            context.bot_data[info_dict['id']] = {'url': url, 'title': title}
            reply_markup = InlineKeyboardMarkup(buttons)
            await msg.edit_text(f"✅ **{title}**\n\nکیفیت مورد نظر را انتخاب کنید:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error processing link {url}: {e}")
        await msg.edit_text("خطا در پردازش لینک. لطفاً از معتبر بودن لینک مطمئن شوید.")

async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        _, format_id, video_id = query.data.split("_")
        video_info = context.bot_data.get(video_id)
        if not video_info:
            await query.edit_message_text("خطا: اطلاعات ویدیو یافت نشد. لطفاً دوباره تلاش کنید.")
            return
        
        await query.edit_message_text("📥 در حال دانلود...")
        
        output_filename = f"{video_id}.%(ext)s"
        ydl_opts = {'format': format_id, 'outtmpl': output_filename, 'noprogress': True}
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_info['url']])
            downloaded_file = ydl.prepare_filename(info_dict={'id': video_id, 'ext': ydl.extract_info(video_info['url'], download=False)['ext']})
        
        await query.edit_message_text("📤 در حال ارسال...")
        
        with open(downloaded_file, 'rb') as f:
            if downloaded_file.endswith(('.mp4', '.mkv', '.webm')):
                await context.bot.send_video(chat_id=query.from_user.id, video=f, caption=video_info['title'], supports_streaming=True)
            else:
                await context.bot.send_audio(chat_id=query.from_user.id, audio=f, caption=video_info['title'])
        
        await query.edit_message_text("✅ فایل با موفقیت ارسال شد.")
        os.remove(downloaded_file)
        del context.bot_data[video_id]
    except Exception as e:
        logger.error(f"Error in download_and_send: {e}")
        await query.edit_message_text("خطایی در ارسال فایل رخ داد.")

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer(text="در حال بررسی عضویت...")
    
    if await check_membership(query.from_user.id, context):
        await query.edit_message_text("✅ عضویت شما تایید شد! حالا لینک خود را ارسال کنید.")
    else:
        await query.answer(text="شما هنوز در تمام کانال‌ها عضو نشده‌اید!", show_alert=True)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("لطفاً پیام را بعد از دستور /broadcast بنویسید.")
        return
    
    conn, cursor = sqlite3.connect(DB_FILE), conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    
    sent_count, failed_count = 0, 0
    await update.message.reply_text(f"شروع ارسال پیام برای {len(users)} کاربر...")
    
    for user in users:
        try:
            await context.bot.send_message(chat_id=user[0], text=message, parse_mode=ParseMode.MARKDOWN)
            sent_count += 1
        except Exception:
            failed_count += 1
        await asyncio.sleep(0.1)
    
    await update.message.reply_text(f"ارسال تمام شد.\nموفق: {sent_count}\nناموفق: {failed_count}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    conn, cursor = sqlite3.connect(DB_FILE), conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    conn.close()
    
    await update.message.reply_text(f"📊 تعداد کل کاربران: {count}")

def main():
    setup_database()
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    application.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    application.add_handler(CallbackQueryHandler(download_and_send, pattern="^dl_"))
    
    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
