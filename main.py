import os
import logging
import json
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)
from models import init_db, init_default_data
from utils import (
    get_rate, update_rate, get_fee_for_amount, get_all_fee_ranges,
    add_fee_range, update_fee_range, delete_fee_range, calculate_transaction,
    get_profit_statistics, update_custom_formula, get_active_formula,
    clear_rate_cache
)
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from datetime import datetime, timedelta
import threading
import re

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get bot token and admin user IDs from environment variables
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_IDS = [int(id) for id in os.getenv('ADMIN_USER_IDS', '').split(',') if id]

# Initialize database with connection pooling
db_session = init_db()
init_default_data(db_session)

# Conversation states
(
    MAIN_MENU, BUY_USDT, SELL_USDT, ADMIN_MENU, SET_BUY_RATE, SET_SELL_RATE,
    MANAGE_FEES, ADD_FEE_MIN, ADD_FEE_MAX, ADD_FEE_AMOUNT, EDIT_FEE, DELETE_FEE,
    SET_CUSTOM_FORMULA, BUY_CURRENCY_SELECT, SELL_CURRENCY_SELECT, BUY_IDR, SELL_IDR,
    CALCULATOR
) = range(18)

# Callback data prefixes
CALLBACK_PREFIX = {
    'BUY': 'buy',
    'SELL': 'sell',
    'ADMIN': 'admin',
    'SET_BUY_RATE': 'set_buy_rate',
    'SET_SELL_RATE': 'set_sell_rate',
    'MANAGE_FEES': 'manage_fees',
    'ADD_FEE': 'add_fee',
    'EDIT_FEE': 'edit_fee',
    'DELETE_FEE': 'delete_fee',
    'STATS': 'stats',
    'SET_FORMULA': 'set_formula',
    'BACK': 'back',
    'CONFIRM': 'confirm',
    'CANCEL': 'cancel'
}

# Create a thread pool executor for CPU-bound tasks
thread_pool = ThreadPoolExecutor(
    max_workers=min(32, (os.cpu_count() or 1) * 4),
    thread_name_prefix="bot_worker"
)

# Thread-local storage for database sessions
thread_local = threading.local()

def get_db_session():
    """Get a database session for the current thread"""
    if not hasattr(thread_local, "session"):
        thread_local.session = init_db()
    return thread_local.session

async def run_in_threadpool(func, *args, **kwargs):
    """Run a function in thread pool with proper session management"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        thread_pool,
        partial(func, *args, **kwargs)
    )

async def cleanup_db_session():
    """Cleanup database session for the current thread"""
    if hasattr(thread_local, "session"):
        thread_local.session.close()
        del thread_local.session

# Add cleanup handler
async def cleanup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cleanup resources after each update"""
    await cleanup_db_session()

async def calculate_transaction_async(session, amount: float, transaction_type: str):
    """Async wrapper for calculate_transaction"""
    return await run_in_threadpool(calculate_transaction, session, amount, transaction_type)

async def get_all_fee_ranges_async(session):
    """Async wrapper for get_all_fee_ranges"""
    return await run_in_threadpool(get_all_fee_ranges, session)

async def add_fee_range_async(session, min_amount: float, max_amount: float, fee_amount: float):
    """Async wrapper for add_fee_range"""
    return await run_in_threadpool(add_fee_range, session, min_amount, max_amount, fee_amount)

async def delete_fee_range_async(session, fee_id: int):
    """Async wrapper for delete_fee_range"""
    return await run_in_threadpool(delete_fee_range, session, fee_id)

async def is_admin_async(user_id: int):
    """Async wrapper for is_admin"""
    return await run_in_threadpool(is_admin, user_id)

# Helper functions
def format_timestamp(timestamp):
    """Format timestamp to local time (UTC+7) in HH.MM.SS WIB format"""
    if not timestamp:
        return "Tidak tersedia"
    
    # Convert UTC to local time (UTC+7)
    local_time = timestamp + timedelta(hours=7)
    return local_time.strftime("%H.%M.%S WIB")

def format_currency(amount):
    """Format amount as currency"""
    return f"Rp {amount:,.0f}"

def is_admin(user_id):
    """Check if user is admin"""
    return user_id in ADMIN_IDS

def get_main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    """Get main menu keyboard"""
    keyboard = [
        [KeyboardButton("💰 Beli USDT"), KeyboardButton("💵 Jual USDT")],
        [KeyboardButton("🧮 Kalkulator")],
    ]
    
    # Add admin button if user is admin
    if is_admin(user_id):
        keyboard.append([KeyboardButton("👑 Admin Panel")])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_welcome_inline_keyboard() -> InlineKeyboardMarkup:
    """Get welcome message inline keyboard with testimonial link"""
    keyboard = [
        [InlineKeyboardButton("📢 Testimoni", url="https://t.me/Testimooney")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_combined_welcome_keyboard(user_id: int) -> dict:
    """Get combined keyboard for welcome message"""
    return {
        'reply_markup': get_main_menu_keyboard(user_id),
        'inline_keyboard': get_welcome_inline_keyboard()
    }

def get_admin_menu_keyboard() -> ReplyKeyboardMarkup:
    """Get admin menu keyboard"""
    keyboard = [
        [KeyboardButton("📊 Set Rate Beli"), KeyboardButton("📊 Set Rate Jual")],
        [KeyboardButton("💰 Kelola Fee"), KeyboardButton("📝 Set Formula")],
        [KeyboardButton("🔙 Kembali ke Menu Utama")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_fee_menu_keyboard() -> ReplyKeyboardMarkup:
    """Get fee management keyboard"""
    keyboard = [
        [KeyboardButton("➕ Tambah Fee"), KeyboardButton("✏️ Edit Fee")],
        [KeyboardButton("❌ Hapus Fee"), KeyboardButton("🔙 Kembali")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_fee_list_keyboard():
    """Get the fee list keyboard"""
    fee_ranges = get_all_fee_ranges(db_session)
    keyboard = []
    
    for fee_range in fee_ranges:
        max_str = f"{format_currency(fee_range.max_amount)}" if fee_range.max_amount is not None else "unlimited"
        button_text = f"{format_currency(fee_range.min_amount)} - {max_str}: {format_currency(fee_range.fee_amount)}"
        callback_data = f"{CALLBACK_PREFIX['EDIT_FEE']}:{fee_range.id}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("Kembali", callback_data=CALLBACK_PREFIX['BACK'])])
    return InlineKeyboardMarkup(keyboard)

def get_fee_edit_keyboard(fee_id):
    """Get the fee edit keyboard"""
    keyboard = [
        [InlineKeyboardButton("Edit Fee", callback_data=f"{CALLBACK_PREFIX['EDIT_FEE']}:{fee_id}")],
        [InlineKeyboardButton("Hapus Fee", callback_data=f"{CALLBACK_PREFIX['DELETE_FEE']}:{fee_id}")],
        [InlineKeyboardButton("Kembali", callback_data=CALLBACK_PREFIX['MANAGE_FEES'])]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_confirm_keyboard():
    """Get the confirmation keyboard"""
    keyboard = [
        [
            InlineKeyboardButton("Ya", callback_data=CALLBACK_PREFIX['CONFIRM']),
            InlineKeyboardButton("Tidak", callback_data=CALLBACK_PREFIX['CANCEL'])
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard() -> ReplyKeyboardMarkup:
    """Get back button keyboard"""
    keyboard = [[KeyboardButton("🔙 Kembali")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_currency_selection_keyboard() -> ReplyKeyboardMarkup:
    """Get currency selection keyboard"""
    keyboard = [
        [KeyboardButton("💵 USDT"), KeyboardButton("💰 IDR")],
        [KeyboardButton("🔙 Kembali")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_contact_admin_keyboard() -> InlineKeyboardMarkup:
    """Get contact admin inline keyboard"""
    keyboard = [
        [InlineKeyboardButton("📞 Contact Admin", url="https://t.me/yanzost")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_result_keyboard() -> InlineKeyboardMarkup:
    """Get result keyboard with contact admin button"""
    keyboard = [
        [InlineKeyboardButton("📞 Contact Admin", url="https://t.me/yanzost")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Fungsi utilitas untuk parsing angka dari berbagai format
def parse_number(input_str):
    # Hilangkan spasi
    s = input_str.replace(' ', '')
    # Jika ada koma dan titik
    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'):
            # Koma sebagai desimal, titik sebagai ribuan
            s = s.replace('.', '').replace(',', '.')
        else:
            # Titik sebagai desimal, koma sebagai ribuan
            s = s.replace(',', '')
    # Hanya koma
    elif ',' in s:
        s = s.replace('.', '').replace(',', '.')
    # Hanya titik
    elif '.' in s:
        s = s.replace(',', '')
    # Hanya angka
    else:
        pass
    # Hilangkan karakter selain angka dan titik
    s = re.sub(r'[^0-9\.]', '', s)
    return float(s)

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    username = user.username or user.first_name
    
    # Get current rates
    buy_rate = get_rate(db_session, 'buy')
    sell_rate = get_rate(db_session, 'sell')
    
    # Format the timestamp
    def format_timestamp(timestamp):
        if not timestamp:
            return "Tidak diketahui"
        # Convert UTC to local time (assuming Indonesia timezone, UTC+7)
        local_time = timestamp + timedelta(hours=7)
        return local_time.strftime("%d %B %Y, %H:%M WIB")
    
    welcome_text = (
        f"👋 Hai, selamat datang *{username}* di layanan jual beli USDT!\n"
        f"Saya adalah bot otomatis untuk melakukan perhitungan nominal convert secara akurat.\n\n"
        f"*⌊ Rate Hari Ini ⌉*\n"
        f"*└ Terakhir Diperbarui: {format_timestamp(buy_rate['updated_at'])}*\n\n"
        f"▸ *Rate Beli USDT*: Rp {buy_rate['value']:,.0f} / 1 USDT\n"
        f"▸ *Rate Jual USDT*: Rp {sell_rate['value']:,.0f} / 1 USDT\n\n"
        f"📎 *Biaya Transaksi*:\n"
        f"Fee akan dihitung otomatis berdasarkan nominal rupiah transaksi kamu — mulai dari Rp 3.000 hingga Rp 25.000.\n\n"
        f"📢 *Lihat testimoni dari customer kami:* [Testimoni](https://t.me/Testimooney/706)"
    )
    
    # Send welcome message with the main menu keyboard
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard(user.id),
        parse_mode='Markdown'
    )
    
    return MAIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command"""
    await update.message.reply_text(
        "USDT Calculator Bot - Bantuan\n\n"
        "Bot ini membantu menghitung harga beli dan jual USDT.\n\n"
        "Perintah yang tersedia:\n"
        "/start - Mulai bot dan tampilkan menu utama\n"
        "/help - Tampilkan bantuan ini\n"
    )
    return MAIN_MENU

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /admin command"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("Maaf, Anda tidak memiliki akses ke panel admin.")
        return MAIN_MENU
    
    await update.message.reply_text(
        "Panel Admin\n\n"
        "Silakan pilih opsi di bawah ini:",
        reply_markup=get_admin_menu_keyboard()
    )
    return ADMIN_MENU

# Callback query handlers
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    user_id = update.effective_user.id
    
    # Main menu callbacks
    if callback_data == CALLBACK_PREFIX['BUY']:
        await query.edit_message_text(
            "Silakan masukkan jumlah USDT yang ingin Anda beli:"
        )
        return BUY_USDT
    
    elif callback_data == CALLBACK_PREFIX['SELL']:
        await query.edit_message_text(
            "Silakan masukkan jumlah USDT yang ingin Anda jual:"
        )
        return SELL_USDT
    
    elif callback_data == CALLBACK_PREFIX['ADMIN']:
        if not is_admin(user_id):
            await query.edit_message_text("Maaf, Anda tidak memiliki akses ke panel admin.")
            return MAIN_MENU
        
        await query.edit_message_text(
            "Panel Admin\n\n"
            "Silakan pilih opsi di bawah ini:",
            reply_markup=get_admin_menu_keyboard()
        )
        return ADMIN_MENU
    
    # Admin menu callbacks
    elif callback_data == CALLBACK_PREFIX['SET_BUY_RATE']:
        buy_rate = get_rate(db_session, 'buy')
        await query.edit_message_text(
            f"Rate Beli saat ini: {format_currency(buy_rate['value'])}\n\n"
            "Silakan masukkan rate beli baru:"
        )
        return SET_BUY_RATE
    
    elif callback_data == CALLBACK_PREFIX['SET_SELL_RATE']:
        sell_rate = get_rate(db_session, 'sell')
        await query.edit_message_text(
            f"Rate Jual saat ini: {format_currency(sell_rate['value'])}\n\n"
            "Silakan masukkan rate jual baru:"
        )
        return SET_SELL_RATE
    
    elif callback_data == CALLBACK_PREFIX['MANAGE_FEES']:
        await query.edit_message_text(
            "Kelola Fee Transaksi\n\n"
            "Silakan pilih opsi di bawah ini:",
            reply_markup=get_fee_menu_keyboard()
        )
        return MANAGE_FEES
    
    elif callback_data == CALLBACK_PREFIX['ADD_FEE']:
        await query.edit_message_text(
            "Tambah Fee Baru\n\n"
            "Silakan masukkan nilai minimum untuk rentang fee baru:"
        )
        return ADD_FEE_MIN
    
    elif callback_data == CALLBACK_PREFIX['STATS']:
        stats = get_profit_statistics(db_session)
        if not stats:
            await query.edit_message_text(
                "Gagal mengambil statistik keuntungan.",
                reply_markup=get_admin_menu_keyboard()
            )
            return ADMIN_MENU
        
        await query.edit_message_text(
            f"Statistik Keuntungan\n\n"
            f"Total Keuntungan: {format_currency(stats['total_profit'])}\n"
            f"Total Transaksi: {stats['total_transactions']}\n"
            f"Total Transaksi Beli: {stats['total_buy']}\n"
            f"Total Transaksi Jual: {stats['total_sell']}\n"
            f"Total USDT Dibeli: {stats['total_usdt_bought']:.2f}\n"
            f"Total USDT Dijual: {stats['total_usdt_sold']:.2f}\n\n",
            reply_markup=get_admin_menu_keyboard()
        )
        return ADMIN_MENU
    
    elif callback_data == CALLBACK_PREFIX['SET_FORMULA']:
        buy_formula = get_active_formula(db_session, 'buy')
        sell_formula = get_active_formula(db_session, 'sell')
        
        buy_formula_str = buy_formula.formula if buy_formula else "{usdt_amount} * {rate} + {fee}"
        sell_formula_str = sell_formula.formula if sell_formula else "{usdt_amount} * {rate} - {fee}"
        
        await query.edit_message_text(
            f"Set Rumus Kustom\n\n"
            f"Rumus Beli saat ini: {buy_formula_str}\n"
            f"Rumus Jual saat ini: {sell_formula_str}\n\n"
            f"Silakan pilih rumus yang ingin diubah:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Rumus Beli", callback_data=f"{CALLBACK_PREFIX['SET_FORMULA']}:buy"),
                    InlineKeyboardButton("Rumus Jual", callback_data=f"{CALLBACK_PREFIX['SET_FORMULA']}:sell")
                ],
                [InlineKeyboardButton("Kembali", callback_data=CALLBACK_PREFIX['BACK'])]
            ])
        )
        return SET_CUSTOM_FORMULA
    
    elif callback_data == CALLBACK_PREFIX['BACK']:
        await query.edit_message_text(
            "Menu Utama\n\n"
            "Silakan pilih opsi di bawah ini:",
            reply_markup=get_main_menu_keyboard(user_id)
        )
        return MAIN_MENU
    
    # Fee management callbacks
    elif callback_data.startswith(f"{CALLBACK_PREFIX['EDIT_FEE']}:"):
        fee_id = int(callback_data.split(':')[1])
        fee_ranges = get_all_fee_ranges(db_session)
        fee_range = next((fr for fr in fee_ranges if fr.id == fee_id), None)
        
        if not fee_range:
            await query.edit_message_text(
                "Fee range tidak ditemukan.",
                reply_markup=get_fee_menu_keyboard()
            )
            return MANAGE_FEES
        
        max_str = f"{format_currency(fee_range.max_amount)}" if fee_range.max_amount is not None else "unlimited"
        await query.edit_message_text(
            f"Edit Fee\n\n"
            f"Rentang: {format_currency(fee_range.min_amount)} - {max_str}\n"
            f"Fee: {format_currency(fee_range.fee_amount)}\n\n"
            f"Silakan pilih opsi di bawah ini:",
            reply_markup=get_fee_edit_keyboard(fee_id)
        )
        return EDIT_FEE
    
    elif callback_data.startswith(f"{CALLBACK_PREFIX['DELETE_FEE']}:"):
        fee_id = int(callback_data.split(':')[1])
        context.user_data['fee_id_to_delete'] = fee_id
        
        await query.edit_message_text(
            "Apakah Anda yakin ingin menghapus fee ini?",
            reply_markup=get_confirm_keyboard()
        )
        return DELETE_FEE
    
    elif callback_data == CALLBACK_PREFIX['CONFIRM']:
        if 'fee_id_to_delete' in context.user_data:
            fee_id = context.user_data['fee_id_to_delete']
            if delete_fee_range(db_session, fee_id):
                await query.edit_message_text(
                    "Fee berhasil dihapus.",
                    reply_markup=get_fee_menu_keyboard()
                )
            else:
                await query.edit_message_text(
                    "Gagal menghapus fee.",
                    reply_markup=get_fee_menu_keyboard()
                )
            del context.user_data['fee_id_to_delete']
            return MANAGE_FEES
    
    elif callback_data == CALLBACK_PREFIX['CANCEL']:
        if 'fee_id_to_delete' in context.user_data:
            del context.user_data['fee_id_to_delete']
        
        await query.edit_message_text(
            "Kelola Fee Transaksi\n\n"
            "Silakan pilih opsi di bawah ini:",
            reply_markup=get_fee_menu_keyboard()
        )
        return MANAGE_FEES
    
    # Custom formula callbacks
    elif callback_data.startswith(f"{CALLBACK_PREFIX['SET_FORMULA']}:"):
        formula_type = callback_data.split(':')[1]
        context.user_data['formula_type'] = formula_type
        
        formula = get_active_formula(db_session, formula_type)
        formula_str = formula.formula if formula else "{usdt_amount} * {rate} + {fee}" if formula_type == 'buy' else "{usdt_amount} * {rate} - {fee}"
        
        await query.edit_message_text(
            f"Set Rumus {formula_type.capitalize()}\n\n"
            f"Rumus saat ini: {formula_str}\n\n"
            f"Silakan masukkan rumus baru. Gunakan variabel {{usdt_amount}}, {{rate}}, dan {{fee}}.\n"
            f"Contoh: {{usdt_amount}} * {{rate}} + {{fee}}"
        )
        return SET_CUSTOM_FORMULA

# Message handlers
async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle main menu selection"""
    text = update.message.text
    user_id = update.effective_user.id

    # Clear previous state and messages
    # if 'last_message_id' in context.user_data:
    #     try:
    #         await context.bot.delete_message(
    #             chat_id=update.effective_chat.id,
    #             message_id=context.user_data['last_message_id']
    #         )
    #     except Exception:
    #         pass  # Ignore if message can't be deleted

    if text == "💰 Beli USDT":
        msg = await update.message.reply_text(
            "🛍️ *Beli USDT*\n\n"
            "Pilih mata uang yang ingin Anda gunakan:",
            reply_markup=get_currency_selection_keyboard(),
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return BUY_CURRENCY_SELECT
    elif text == "💵 Jual USDT":
        msg = await update.message.reply_text(
            "💱 *Jual USDT*\n\n"
            "Pilih mata uang yang ingin Anda gunakan:",
            reply_markup=get_currency_selection_keyboard(),
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return SELL_CURRENCY_SELECT
    elif text == "👑 Admin Panel" and is_admin(user_id):
        admin_text = (
            "👑 *Admin Panel* 👑\n\n"
            "🎛️ Kontrol penuh atas sistem kalkulator USDT\n"
            "⚙️ Atur rate, fee, dan formula sesuai kebutuhan\n"
            "📊 Pantau dan kelola transaksi dengan mudah\n\n"
            "Silakan pilih menu:"
        )
        msg = await update.message.reply_text(
            admin_text,
            reply_markup=get_admin_menu_keyboard(),
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return ADMIN_MENU
    else:
        msg = await update.message.reply_text(
            "❌ Pilihan tidak valid. Silakan pilih menu yang tersedia.",
            reply_markup=get_main_menu_keyboard(user_id)
        )
        # context.user_data['last_message_id'] = msg.message_id
        return MAIN_MENU

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin menu selection"""
    text = update.message.text
    user_id = update.effective_user.id

    # DEBUG LOG
    print(f"[DEBUG] handle_admin_menu: user_id={user_id}, text='{text}'")

    # Jika user klik '👑 Admin Panel' di dalam admin panel, tampilkan ulang menu admin
    if text == "👑 Admin Panel":
        admin_text = (
            "👑 *Admin Panel* 👑\n\n"
            "🎛️ Kontrol penuh atas sistem kalkulator USDT\n"
            "⚙️ Atur rate, fee, dan formula sesuai kebutuhan\n"
            "📊 Pantau dan kelola transaksi dengan mudah\n\n"
            "Silakan pilih menu:"
        )
        msg = await update.message.reply_text(
            admin_text,
            reply_markup=get_admin_menu_keyboard(),
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return ADMIN_MENU

    # Clear previous state and messages
    # if 'last_message_id' in context.user_data:
    #     try:
    #         await context.bot.delete_message(
    #             chat_id=update.effective_chat.id,
    #             message_id=context.user_data['last_message_id']
    #         )
    #     except Exception:
    #         pass  # Ignore if message can't be deleted

    # Check if user is trying to switch to another action
    if text in ["💰 Beli USDT", "💵 Jual USDT"]:
        return await handle_main_menu(update, context)

    if not is_admin(user_id):
        msg = await update.message.reply_text(
            "⛔ Anda tidak memiliki akses ke menu ini.",
            reply_markup=get_main_menu_keyboard(user_id)
        )
        # context.user_data['last_message_id'] = msg.message_id
        return MAIN_MENU
    
    # Get current rates
    buy_rate = get_rate(db_session, 'buy')
    sell_rate = get_rate(db_session, 'sell')
    
    # Format the timestamp
    buy_timestamp = format_timestamp(buy_rate['updated_at']) if buy_rate else "Tidak tersedia"
    sell_timestamp = format_timestamp(sell_rate['updated_at']) if sell_rate else "Tidak tersedia"
    
    if text == "📊 Set Rate Beli":
        msg = await update.message.reply_text(
            f"📈 *Set Rate Beli USDT*\n\n"
            f"Rate beli saat ini: {format_currency(buy_rate['value']) if buy_rate else 'Tidak tersedia'}\n"
            f"Terakhir diupdate: {buy_timestamp}\n\n"
            f"Masukkan rate beli USDT baru (dalam IDR):",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return SET_BUY_RATE
    elif text == "📊 Set Rate Jual":
        msg = await update.message.reply_text(
            f"📉 *Set Rate Jual USDT*\n\n"
            f"Rate jual saat ini: {format_currency(sell_rate['value']) if sell_rate else 'Tidak tersedia'}\n"
            f"Terakhir diupdate: {sell_timestamp}\n\n"
            f"Masukkan rate jual USDT baru (dalam IDR):",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return SET_SELL_RATE
    elif text == "💰 Kelola Fee":
        fee_text = (
            "💰 *Kelola Fee Transaksi*\n\n"
            "⚖️ Atur fee untuk berbagai range transaksi\n"
            "📊 Optimalkan keuntungan dengan fee yang tepat\n"
            "🔄 Kelola fee dengan mudah dan efisien\n\n"
            "Silakan pilih menu:"
        )
        msg = await update.message.reply_text(
            fee_text,
            reply_markup=get_fee_menu_keyboard(),
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return MANAGE_FEES
    elif text == "📝 Set Formula":
        msg = await update.message.reply_text(
            "📝 *Set Formula Kustom*\n\n"
            "Masukkan formula kustom (gunakan x sebagai variabel):",
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return SET_CUSTOM_FORMULA
    elif text == "🔙 Kembali ke Menu Utama":
        welcome_text = (
            "🌟 *Menu Utama* 🌟\n\n"
            "🤖 Bot ini akan membantu Anda menghitung harga beli dan jual USDT\n"
            "💱 Dapatkan perhitungan akurat dengan rate terbaik\n"
            "💎 Transaksi aman dan terpercaya\n\n"
            "Silakan pilih menu di bawah ini:"
        )
        msg = await update.message.reply_text(
            welcome_text,
            reply_markup=get_main_menu_keyboard(user_id),
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return MAIN_MENU
    else:
        msg = await update.message.reply_text(
            "❌ Pilihan tidak valid. Silakan pilih menu yang tersedia.",
            reply_markup=get_admin_menu_keyboard()
        )
        # context.user_data['last_message_id'] = msg.message_id
        return ADMIN_MENU

async def handle_fee_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle fee management menu selection"""
    text = update.message.text
    user_id = update.effective_user.id

    # Clear previous state and messages
    # if 'last_message_id' in context.user_data:
    #     try:
    #         await context.bot.delete_message(
    #             chat_id=update.effective_chat.id,
    #             message_id=context.user_data['last_message_id']
    #         )
    #     except Exception:
    #         pass  # Ignore if message can't be deleted

    # Check if user is trying to switch to another action
    if text in ["💰 Beli USDT", "💵 Jual USDT", "👑 Admin Panel"]:
        return await handle_main_menu(update, context)

    if not is_admin(user_id):
        msg = await update.message.reply_text(
            "⛔ Anda tidak memiliki akses ke menu ini.",
            reply_markup=get_main_menu_keyboard(user_id)
        )
        # context.user_data['last_message_id'] = msg.message_id
        return MAIN_MENU

    if text == "➕ Tambah Fee":
        msg = await update.message.reply_text(
            "➕ *Tambah Fee Baru*\n\n"
            "Masukkan nilai minimum untuk range fee:",
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return ADD_FEE_MIN
    elif text == "✏️ Edit Fee":
        fees = get_all_fee_ranges(db_session)
        if not fees:
            msg = await update.message.reply_text(
                "ℹ️ Belum ada fee yang tersedia.",
                reply_markup=get_admin_menu_keyboard()
            )
            # context.user_data['last_message_id'] = msg.message_id
            return ADMIN_MENU
        
        fee_list = "\n".join([
            f"{i+1}. Range: {format_currency(f.min_amount)} - {format_currency(f.max_amount)}, Fee: {format_currency(f.fee_amount)}"
            for i, f in enumerate(fees)
        ])
        msg = await update.message.reply_text(
            f"✏️ *Edit Fee*\n\n"
            f"Pilih nomor fee yang ingin diedit:\n\n"
            f"{fee_list}",
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return EDIT_FEE
    elif text == "❌ Hapus Fee":
        fees = get_all_fee_ranges(db_session)
        if not fees:
            msg = await update.message.reply_text(
                "ℹ️ Belum ada fee yang tersedia.",
                reply_markup=get_admin_menu_keyboard()
            )
            # context.user_data['last_message_id'] = msg.message_id
            return ADMIN_MENU
        
        fee_list = "\n".join([
            f"{i+1}. Range: {format_currency(f.min_amount)} - {format_currency(f.max_amount)}, Fee: {format_currency(f.fee_amount)}"
            for i, f in enumerate(fees)
        ])
        msg = await update.message.reply_text(
            f"❌ *Hapus Fee*\n\n"
            f"Pilih nomor fee yang ingin dihapus:\n\n"
            f"{fee_list}",
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return DELETE_FEE
    elif text == "🔙 Kembali":
        admin_text = (
            "👑 *Admin Panel* 👑\n\n"
            "🎛️ Kontrol penuh atas sistem kalkulator USDT\n"
            "⚙️ Atur rate, fee, dan formula sesuai kebutuhan\n"
            "📊 Pantau dan kelola transaksi dengan mudah\n\n"
            "Silakan pilih menu:"
        )
        msg = await update.message.reply_text(
            admin_text,
            reply_markup=get_admin_menu_keyboard(),
            parse_mode='Markdown'
        )
        # context.user_data['last_message_id'] = msg.message_id
        return ADMIN_MENU
    else:
        msg = await update.message.reply_text(
            "❌ Pilihan tidak valid. Silakan pilih menu yang tersedia.",
            reply_markup=get_fee_menu_keyboard()
        )
        # context.user_data['last_message_id'] = msg.message_id
        return MANAGE_FEES

async def handle_buy_currency_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle buy currency selection"""
    text = update.message.text
    user_id = update.effective_user.id

    # Clear previous state and messages
    # if 'last_message_id' in context.user_data:
    #     try:
    #         await context.bot.delete_message(
    #             chat_id=update.effective_chat.id,
    #             message_id=context.user_data['last_message_id']
    #         )
    #     except Exception:
    #         pass  # Ignore if message can't be deleted

    # Check if user is trying to switch to another action
    if text in ["💰 Beli USDT", "💵 Jual USDT", "👑 Admin Panel"]:
        return await handle_main_menu(update, context)
    
    # Check if user wants to go back
    if text == "🔙 Kembali":
        await start(update, context)
        return MAIN_MENU

    if text == "💵 USDT":
        # Ambil rate beli realtime
        buy_rate = get_rate(db_session, 'buy')
        rate_str = format_currency(buy_rate['value']) if buy_rate else 'Tidak tersedia'
        msg = await update.message.reply_text(
            f"🛍️ *Beli USDT*\n\n"
            f"▸ Rate Beli USDT: Rp {rate_str} / 1 USDT\n"
            f"Masukkan jumlah USDT yang ingin dibeli:",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
        return BUY_USDT
    elif text == "💰 IDR":
        # Ambil rate beli realtime
        buy_rate = get_rate(db_session, 'buy')
        rate_str = format_currency(buy_rate['value']) if buy_rate else 'Tidak tersedia'
        msg = await update.message.reply_text(
            f"🛍️ *Beli USDT*\n\n"
            f"▸ Rate Beli USDT: Rp {rate_str} / 1 USDT\n"
            f"Masukkan jumlah IDR yang ingin Anda belikan USDT:",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
        return BUY_IDR
    else:
        msg = await update.message.reply_text(
            "❌ Pilihan tidak valid. Silakan pilih mata uang yang tersedia:",
            reply_markup=get_currency_selection_keyboard()
        )
        # context.user_data['last_message_id'] = msg.message_id
        return BUY_CURRENCY_SELECT

async def handle_sell_currency_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle sell currency selection"""
    text = update.message.text
    user_id = update.effective_user.id

    # Clear previous state and messages
    # if 'last_message_id' in context.user_data:
    #     try:
    #         await context.bot.delete_message(
    #             chat_id=update.effective_chat.id,
    #             message_id=context.user_data['last_message_id']
    #         )
    #     except Exception:
    #         pass  # Ignore if message can't be deleted

    # Check if user is trying to switch to another action
    if text in ["💰 Beli USDT", "💵 Jual USDT", "👑 Admin Panel"]:
        return await handle_main_menu(update, context)
    
    # Check if user wants to go back
    if text == "🔙 Kembali":
        await start(update, context)
        return MAIN_MENU

    if text == "💵 USDT":
        # Ambil rate jual realtime
        sell_rate = get_rate(db_session, 'sell')
        rate_str = format_currency(sell_rate['value']) if sell_rate else 'Tidak tersedia'
        msg = await update.message.reply_text(
            f"💱 *Jual USDT*\n\n"
            f"▸ Rate Jual USDT: Rp {rate_str} / 1 USDT\n"
            f"Masukkan jumlah USDT yang ingin dijual:",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
        return SELL_USDT
    elif text == "💰 IDR":
        # Ambil rate jual realtime
        sell_rate = get_rate(db_session, 'sell')
        rate_str = format_currency(sell_rate['value']) if sell_rate else 'Tidak tersedia'
        msg = await update.message.reply_text(
            f"💱 *Jual USDT*\n\n"
            f"▸ Rate Jual USDT: Rp {rate_str} / 1 USDT\n"
            f"Masukkan jumlah IDR yang ingin Anda dapatkan dari penjualan USDT:",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
        return SELL_IDR
    else:
        msg = await update.message.reply_text(
            "❌ Pilihan tidak valid. Silakan pilih mata uang yang tersedia:",
            reply_markup=get_currency_selection_keyboard()
        )
        # context.user_data['last_message_id'] = msg.message_id
        return SELL_CURRENCY_SELECT

async def handle_buy_usdt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle buy USDT input"""
    try:
        # Check if user is trying to switch to another action
        if update.message.text in ["💰 Beli USDT", "💵 Jual USDT", "👑 Admin Panel"]:
            return await handle_main_menu(update, context)
        
        # Check if user wants to go back
        if update.message.text == "🔙 Kembali":
            msg = await update.message.reply_text(
                "Pilih mata uang yang ingin Anda gunakan:",
                reply_markup=get_currency_selection_keyboard(),
                parse_mode='Markdown'
            )
            return BUY_CURRENCY_SELECT

        usdt_amount = parse_number(update.message.text)
        if usdt_amount <= 0:
            msg = await update.message.reply_text(
                "❌ Jumlah USDT harus lebih besar dari 0. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return BUY_USDT
        
        # Get the buy rate
        buy_rate = get_rate(db_session, 'buy')
        if not buy_rate:
            msg = await update.message.reply_text(
                "❌ Gagal mendapatkan rate beli. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return BUY_USDT
        
        # Calculate IDR amount
        idr_amount = usdt_amount * buy_rate['value']
        
        # Get the fee
        fee = get_fee_for_amount(db_session, idr_amount)
        
        # Calculate total amount
        total_amount = idr_amount + fee
        
        # Get user info
        user = update.effective_user
        user_id = user.id
        username = f"@{user.username}" if user.username else "Tidak ada"
        created_at = format_timestamp(datetime.utcnow())
        
        # Template hasil perhitungan beli USDT yang lebih menarik
        msg = await update.message.reply_text(
            f"╭────────────────────╮\n"
            f"│  *DETAIL PERHITUNGAN USDT*\n"
            f"╰────────────────────╯\n"
            f"╭────〔 *USER INFO* 〕───────╮\n"
            f"┊└ ID : `{user_id}`\n"
            f"┊└ Username : {username}\n"
            f"┊└ Created at : *{created_at}*\n"
            f"┊\n"
            f"╭────〔 *BELI USDT* 〕───────╮\n"
            f"┊ • Jumlah IDR : {format_currency(idr_amount)}\n"
            f"┊ • Jumlah USDT : {usdt_amount:.2f} USDT\n"
            f"┊ • Rate : {format_currency(buy_rate['value'])}\n"
            f"┊ • Fee : {format_currency(fee)}\n"
            f"╰──────────────────────╯\n"
            f"╰➤ Total Bayar : `{format_currency(total_amount)}`",
            reply_markup=get_result_keyboard(),
            parse_mode='Markdown'
        )
        
        return MAIN_MENU
    except ValueError:
        msg = await update.message.reply_text(
            "❌ Input tidak valid. Silakan masukkan angka:",
            reply_markup=get_back_keyboard()
        )
        return BUY_USDT

async def handle_sell_usdt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle sell USDT input"""
    try:
        # Check if user is trying to switch to another action
        if update.message.text in ["💰 Beli USDT", "💵 Jual USDT", "👑 Admin Panel"]:
            return await handle_main_menu(update, context)
        
        # Check if user wants to go back
        if update.message.text == "🔙 Kembali":
            msg = await update.message.reply_text(
                "Pilih mata uang yang ingin Anda gunakan:",
                reply_markup=get_currency_selection_keyboard(),
                parse_mode='Markdown'
            )
            return SELL_CURRENCY_SELECT

        usdt_amount = parse_number(update.message.text)
        if usdt_amount <= 0:
            msg = await update.message.reply_text(
                "❌ Jumlah USDT harus lebih besar dari 0. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return SELL_USDT
        
        # Get the sell rate
        sell_rate = get_rate(db_session, 'sell')
        if not sell_rate:
            msg = await update.message.reply_text(
                "❌ Gagal mendapatkan rate jual. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return SELL_USDT
        
        # Calculate IDR amount
        idr_amount = usdt_amount * sell_rate['value']
        
        # Get the fee
        fee = get_fee_for_amount(db_session, idr_amount)
        
        # Calculate total amount
        total_amount = idr_amount - fee
        
        # Get user info
        user = update.effective_user
        user_id = user.id
        username = f"@{user.username}" if user.username else "Tidak ada"
        created_at = format_timestamp(datetime.utcnow())
        
        # Template hasil perhitungan jual USDT yang lebih menarik
        msg = await update.message.reply_text(
            f"╭────────────────────╮\n"
            f"│  *DETAIL PERHITUNGAN USDT*\n"
            f"╰────────────────────╯\n"
            f"╭────〔 *USER INFO* 〕─────╮\n"
            f"┊└ ID : {user_id}\n"
            f"┊└ Username : {username}\n"
            f"┊└ Created at : *{created_at}*\n"
            f"┊\n"
            f"╭────〔 *JUAL USDT* 〕─────╮\n"
            f"┊ • Jumlah IDR : {format_currency(idr_amount)}\n"
            f"┊ • Jumlah USDT : {usdt_amount:.2f} USDT\n"
            f"┊ • Rate : {format_currency(sell_rate['value'])}\n"
            f"┊ • Fee : {format_currency(fee)}\n"
            f"╰───────────────────╯\n"
            f"╰➤ Total Terima : `{format_currency(total_amount)}`",
            reply_markup=get_result_keyboard(),
            parse_mode='Markdown'
        )

        return MAIN_MENU
    except ValueError:
        msg = await update.message.reply_text(
            "❌ Input tidak valid. Silakan masukkan angka:",
            reply_markup=get_back_keyboard()
        )
        return SELL_USDT

async def handle_buy_idr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle buy USDT with IDR input"""
    try:
        # Check if user is trying to switch to another action
        if update.message.text in ["💰 Beli USDT", "💵 Jual USDT", "👑 Admin Panel"]:
            return await handle_main_menu(update, context)
        
        # Check if user wants to go back
        if update.message.text == "🔙 Kembali":
            msg = await update.message.reply_text(
                "Pilih mata uang yang ingin Anda gunakan:",
                reply_markup=get_currency_selection_keyboard(),
                parse_mode='Markdown'
            )
            return BUY_CURRENCY_SELECT

        idr_amount = parse_number(update.message.text)
        if idr_amount <= 0:
            msg = await update.message.reply_text(
                "❌ Jumlah IDR harus lebih besar dari 0. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return BUY_IDR
        
        # Get the buy rate
        buy_rate = get_rate(db_session, 'buy')
        if not buy_rate:
            msg = await update.message.reply_text(
                "❌ Gagal mendapatkan rate beli. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return BUY_IDR
        
        # Calculate USDT amount from IDR
        usdt_amount = idr_amount / buy_rate['value']
        
        # Get the fee
        fee = get_fee_for_amount(db_session, idr_amount)
        
        # Calculate total amount
        total_amount = idr_amount + fee
        
        # Get user info
        user = update.effective_user
        user_id = user.id
        username = f"@{user.username}" if user.username else "Tidak ada"
        created_at = format_timestamp(datetime.utcnow())
        
        # Template hasil perhitungan beli USDT yang lebih menarik
        msg = await update.message.reply_text(
            f"╭────────────────────╮\n"
            f"│  *DETAIL PERHITUNGAN USDT*\n"
            f"╰────────────────────╯\n"
            f"╭────〔 *USER INFO* 〕───────╮\n"
            f"┊└ ID : `{user_id}`\n"
            f"┊└ Username : {username}\n"
            f"┊└ Created at : *{created_at}*\n"
            f"┊\n"
            f"╭────〔 *BELI USDT* 〕───────╮\n"
            f"┊ • Jumlah IDR : {format_currency(idr_amount)}\n"
            f"┊ • Jumlah USDT : {usdt_amount:.2f} USDT\n"
            f"┊ • Rate : {format_currency(buy_rate['value'])}\n"
            f"┊ • Fee : {format_currency(fee)}\n"
            f"╰──────────────────────╯\n"
            f"╰➤ Total Bayar : `{format_currency(total_amount)}`",
            reply_markup=get_result_keyboard(),
            parse_mode='Markdown'
        )
        
        return MAIN_MENU
    except ValueError:
        msg = await update.message.reply_text(
            "❌ Input tidak valid. Silakan masukkan angka:",
            reply_markup=get_back_keyboard()
        )
        return BUY_IDR

async def handle_sell_idr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle sell USDT with IDR input"""
    try:
        # Check if user is trying to switch to another action
        if update.message.text in ["💰 Beli USDT", "💵 Jual USDT", "👑 Admin Panel"]:
            return await handle_main_menu(update, context)
        
        # Check if user wants to go back
        if update.message.text == "🔙 Kembali":
            msg = await update.message.reply_text(
                "Pilih mata uang yang ingin Anda gunakan:",
                reply_markup=get_currency_selection_keyboard(),
                parse_mode='Markdown'
            )
            return SELL_CURRENCY_SELECT

        idr_amount = parse_number(update.message.text)
        if idr_amount <= 0:
            msg = await update.message.reply_text(
                "❌ Jumlah IDR harus lebih besar dari 0. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return SELL_IDR
        
        # Get the sell rate
        sell_rate = get_rate(db_session, 'sell')
        if not sell_rate:
            msg = await update.message.reply_text(
                "❌ Gagal mendapatkan rate jual. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return SELL_IDR
        
        # Calculate USDT amount from IDR
        usdt_amount = idr_amount / sell_rate['value']
        
        # Get the fee
        fee = get_fee_for_amount(db_session, idr_amount)
        
        # Calculate total amount
        total_amount = idr_amount - fee
        
        # Get user info
        user = update.effective_user
        user_id = user.id
        username = f"@{user.username}" if user.username else "Tidak ada"
        created_at = format_timestamp(datetime.utcnow())
        
        # Template hasil perhitungan jual USDT yang lebih menarik
        msg = await update.message.reply_text(
            f"╭────────────────────╮\n"
            f"│  *DETAIL PERHITUNGAN USDT*\n"
            f"╰────────────────────╯\n"
            f"╭────〔 *USER INFO* 〕─────╮\n"
            f"┊└ ID : {user_id}\n"
            f"┊└ Username : {username}\n"
            f"┊└ Created at : *{created_at}*\n"
            f"┊\n"
            f"╭────〔 *JUAL USDT* 〕─────╮\n"
            f"┊ • Jumlah IDR : {format_currency(idr_amount)}\n"
            f"┊ • Jumlah USDT : {usdt_amount:.2f} USDT\n"
            f"┊ • Rate : {format_currency(sell_rate['value'])}\n"
            f"┊ • Fee : {format_currency(fee)}\n"
            f"╰───────────────────╯\n"
            f"╰➤ Total Terima : `{format_currency(total_amount)}`",
            reply_markup=get_result_keyboard(),
            parse_mode='Markdown'
        )

        return MAIN_MENU
    except ValueError:
        msg = await update.message.reply_text(
            "❌ Input tidak valid. Silakan masukkan angka:",
            reply_markup=get_back_keyboard()
        )
        return SELL_IDR

async def handle_calculator_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "🔙 Kembali":
        await start(update, context)
        return MAIN_MENU
    try:
        # Ganti simbol × dan x dengan * untuk perkalian, dan ^ dengan ** untuk pangkat
        safe_expr = text.replace('×', '*').replace('x', '*').replace('^', '**')
        
        # Hanya izinkan karakter yang aman
        allowed = set('0123456789+-*/().% ')
        if not all(c in allowed or safe_expr[i:i+2] in ['//', '**'] for i, c in enumerate(safe_expr)):
            raise ValueError
            
        # Jalankan evaluasi dalam thread pool untuk operasi CPU-bound
        result = await run_in_threadpool(eval, safe_expr, {"__builtins__": None}, {})
        
        msg = await update.message.reply_text(
            f"*{text} = {result}*",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
    except Exception:
        msg = await update.message.reply_text(
            "*❌ Ekspresi tidak valid. Silakan masukkan ekspresi matematika yang benar.*",
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
    return CALCULATOR

async def handle_calculator_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tampilkan template kalkulator dan minta input ekspresi"""
    template = (
        "*⌊ KALKULATOR ⌉*\n"
        "╭────────────────────╮\n"
        "┊ *+* : Penjumlahan \n"
        "┊ *-* : Pengurangan \n"
        "┊ *×* : Perkalian \n"
        "┊ */* : Pembagian \n"
        "┊ *//* : Pembagian bulat \n"
        "┊ *%* : Modulus/sisa \n"
        "┊ *^* : Pangkat \n"
        "╰────────────────────╯\n\n"
        "*Masukkan angka dan operator yang ingin dihitung*"
    )
    msg = await update.message.reply_text(
        template,
        reply_markup=get_back_keyboard(),
        parse_mode='Markdown'
    )
    return CALCULATOR

async def handle_set_buy_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle setting buy rate"""
    if not is_admin(update.effective_user.id):
        msg = await update.message.reply_text(
            "⛔ Anda tidak memiliki akses ke menu ini.",
            reply_markup=get_main_menu_keyboard(update.effective_user.id)
        )
        return MAIN_MENU
    if update.message.text == "🔙 Kembali":
        msg = await update.message.reply_text(
            "👑 *Admin Panel*\n\nSilakan pilih menu di bawah ini:",
            reply_markup=get_admin_menu_keyboard(),
            parse_mode='Markdown'
        )
        return ADMIN_MENU
    try:
        new_rate = parse_number(update.message.text)
        if new_rate <= 0:
            msg = await update.message.reply_text(
                "❌ Rate harus lebih besar dari 0. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return SET_BUY_RATE
        success = update_rate(db_session, 'buy', new_rate)
        if success:
            clear_rate_cache()
        if not success:
            msg = await update.message.reply_text(
                "❌ Gagal mengupdate rate beli. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return SET_BUY_RATE
        updated_rate = get_rate(db_session, 'buy')
        timestamp = format_timestamp(updated_rate['updated_at']) if updated_rate else "Tidak tersedia"
        msg = await update.message.reply_text(
            f"✅ Rate beli berhasil diupdate!\n\nRate beli baru: {format_currency(new_rate)}\nTerakhir diupdate: {timestamp}",
            reply_markup=get_admin_menu_keyboard(),
            parse_mode='Markdown'
        )
        return ADMIN_MENU
    except Exception:
        msg = await update.message.reply_text(
            "❌ Input tidak valid. Silakan masukkan angka:",
            reply_markup=get_back_keyboard()
        )
        return SET_BUY_RATE

async def handle_set_sell_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle setting sell rate"""
    if not is_admin(update.effective_user.id):
        msg = await update.message.reply_text(
            "⛔ Anda tidak memiliki akses ke menu ini.",
            reply_markup=get_main_menu_keyboard(update.effective_user.id)
        )
        return MAIN_MENU
    if update.message.text == "🔙 Kembali":
        msg = await update.message.reply_text(
            "👑 *Admin Panel*\n\nSilakan pilih menu di bawah ini:",
            reply_markup=get_admin_menu_keyboard(),
            parse_mode='Markdown'
        )
        return ADMIN_MENU
    try:
        new_rate = parse_number(update.message.text)
        if new_rate <= 0:
            msg = await update.message.reply_text(
                "❌ Rate harus lebih besar dari 0. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return SET_SELL_RATE
        success = update_rate(db_session, 'sell', new_rate)
        if success:
            clear_rate_cache()
        if not success:
            msg = await update.message.reply_text(
                "❌ Gagal mengupdate rate jual. Silakan coba lagi:",
                reply_markup=get_back_keyboard()
            )
            return SET_SELL_RATE
        updated_rate = get_rate(db_session, 'sell')
        timestamp = format_timestamp(updated_rate['updated_at']) if updated_rate else "Tidak tersedia"
        msg = await update.message.reply_text(
            f"✅ Rate jual berhasil diupdate!\n\nRate jual baru: {format_currency(new_rate)}\nTerakhir diupdate: {timestamp}",
            reply_markup=get_admin_menu_keyboard(),
            parse_mode='Markdown'
        )
        return ADMIN_MENU
    except Exception:
        msg = await update.message.reply_text(
            "❌ Input tidak valid. Silakan masukkan angka:",
            reply_markup=get_back_keyboard()
        )
        return SET_SELL_RATE

async def handle_add_fee_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle add fee min input"""
    try:
        min_amount = parse_number(update.message.text)
        if min_amount < 0:
            await update.message.reply_text(
                "Nilai minimum harus lebih besar atau sama dengan 0. Silakan coba lagi:"
            )
            return ADD_FEE_MIN
        context.user_data['fee_min'] = min_amount
        await update.message.reply_text(
            "Silakan masukkan nilai maksimum untuk rentang fee (kosongkan untuk unlimited):"
        )
        return ADD_FEE_MAX
    except Exception:
        await update.message.reply_text(
            "Input tidak valid. Silakan masukkan angka:"
        )
        return ADD_FEE_MIN

async def handle_add_fee_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle add fee max input"""
    try:
        max_amount = None
        if update.message.text.strip():
            max_amount = parse_number(update.message.text)
            if max_amount <= context.user_data['fee_min']:
                await update.message.reply_text(
                    "Nilai maksimum harus lebih besar dari nilai minimum. Silakan coba lagi:"
                )
                return ADD_FEE_MAX
        context.user_data['fee_max'] = max_amount
        await update.message.reply_text(
            "Silakan masukkan jumlah fee untuk rentang ini:"
        )
        return ADD_FEE_AMOUNT
    except Exception:
        await update.message.reply_text(
            "Input tidak valid. Silakan masukkan angka:"
        )
        return ADD_FEE_MAX

async def handle_add_fee_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle add fee amount input"""
    try:
        fee_amount = parse_number(update.message.text)
        if fee_amount < 0:
            await update.message.reply_text(
                "Jumlah fee harus lebih besar atau sama dengan 0. Silakan coba lagi:"
            )
            return ADD_FEE_AMOUNT
        min_amount = context.user_data['fee_min']
        max_amount = context.user_data['fee_max']
        if add_fee_range(db_session, min_amount, max_amount, fee_amount):
            max_str = f"{format_currency(max_amount)}" if max_amount is not None else "unlimited"
            await update.message.reply_text(
                f"Fee berhasil ditambahkan untuk rentang {format_currency(min_amount)} - {max_str} dengan jumlah {format_currency(fee_amount)}.",
                reply_markup=get_fee_menu_keyboard()
            )
        else:
            await update.message.reply_text(
                "Gagal menambahkan fee. Rentang mungkin tumpang tindih dengan rentang yang sudah ada.",
                reply_markup=get_fee_menu_keyboard()
            )
        del context.user_data['fee_min']
        del context.user_data['fee_max']
        return MANAGE_FEES
    except Exception:
        await update.message.reply_text(
            "Input tidak valid. Silakan masukkan angka:"
        )
        return ADD_FEE_AMOUNT

async def handle_set_custom_formula(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle set custom formula input"""
    formula_str = update.message.text
    formula_type = context.user_data.get('formula_type')
    if not formula_type:
        await update.message.reply_text(
            "Terjadi kesalahan. Silakan coba lagi.",
            reply_markup=get_admin_menu_keyboard()
        )
        return ADMIN_MENU
    if update_custom_formula(db_session, formula_type, formula_str):
        await update.message.reply_text(
            f"Rumus {formula_type} berhasil diubah menjadi: {formula_str}",
            reply_markup=get_admin_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "Gagal mengubah rumus. Pastikan rumus valid dan mengandung variabel {usdt_amount}, {rate}, dan {fee}.",
            reply_markup=get_admin_menu_keyboard()
        )
    del context.user_data['formula_type']
    return ADMIN_MENU

async def handle_edit_fee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle fee editing"""
    try:
        fee_index = int(update.message.text) - 1
        fees = get_all_fee_ranges(db_session)
        if not 0 <= fee_index < len(fees):
            await update.message.reply_text(
                "Nomor fee tidak valid. Silakan pilih nomor yang tersedia:",
                reply_markup=get_fee_menu_keyboard()
            )
            return MANAGE_FEES
        fee = fees[fee_index]
        context.user_data['editing_fee_id'] = fee.id
        await update.message.reply_text(
            f"Edit Fee untuk range {format_currency(fee.min_amount)} - {format_currency(fee.max_amount)}\n\nMasukkan nilai minimum baru:"
        )
        return ADD_FEE_MIN
    except Exception:
        await update.message.reply_text(
            "Input tidak valid. Silakan masukkan nomor fee yang ingin diedit:",
            reply_markup=get_fee_menu_keyboard()
        )
        return MANAGE_FEES

async def handle_delete_fee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle fee deletion"""
    try:
        fee_index = int(update.message.text) - 1
        fees = get_all_fee_ranges(db_session)
        if not 0 <= fee_index < len(fees):
            await update.message.reply_text(
                "Nomor fee tidak valid. Silakan pilih nomor yang tersedia:",
                reply_markup=get_fee_menu_keyboard()
            )
            return MANAGE_FEES
        fee = fees[fee_index]
        if delete_fee_range(db_session, fee.id):
            await update.message.reply_text(
                f"Fee untuk range {format_currency(fee.min_amount)} - {format_currency(fee.max_amount)} berhasil dihapus.",
                reply_markup=get_fee_menu_keyboard()
            )
        else:
            await update.message.reply_text(
                "Gagal menghapus fee. Silakan coba lagi.",
                reply_markup=get_fee_menu_keyboard()
            )
        return MANAGE_FEES
    except Exception:
        await update.message.reply_text(
            "Input tidak valid. Silakan masukkan nomor fee yang ingin dihapus:",
            reply_markup=get_fee_menu_keyboard()
        )
        return MANAGE_FEES

def main():
    """Start the bot"""
    # Create the Application
    application = Application.builder().token(TOKEN).build()
    
    # Add cleanup handler
    application.add_handler(MessageHandler(filters.ALL, cleanup_handler), group=-1)
    
    # Add conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex('^💰 Beli USDT$'), handle_buy_currency_select),
                MessageHandler(filters.Regex('^💵 Jual USDT$'), handle_sell_currency_select),
                MessageHandler(filters.Regex('^👑 Admin Panel$'), handle_admin_menu),
                MessageHandler(filters.Regex('^🧮 Kalkulator$'), handle_calculator_menu),
            ],
            BUY_CURRENCY_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buy_currency_select)],
            SELL_CURRENCY_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sell_currency_select)],
            BUY_USDT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buy_usdt)],
            BUY_IDR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buy_idr)],
            SELL_USDT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sell_usdt)],
            SELL_IDR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sell_idr)],
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            SET_BUY_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_buy_rate)],
            SET_SELL_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_sell_rate)],
            MANAGE_FEES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_fee_menu)],
            ADD_FEE_MIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_fee_min)],
            ADD_FEE_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_fee_max)],
            ADD_FEE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_fee_amount)],
            EDIT_FEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_fee)],
            DELETE_FEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_delete_fee)],
            SET_CUSTOM_FORMULA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_custom_formula)],
            CALCULATOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_calculator_input)],
        },
        fallbacks=[CommandHandler('start', start)],
        name="conversation",
        persistent=False,
    )
    
    application.add_handler(conv_handler)
    
    # Start the Bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    
    # Cleanup
    thread_pool.shutdown(wait=True)

if __name__ == '__main__':
    main() 