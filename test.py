import os
import sqlite3
import asyncio
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

DB_PATH = "posts.db"
CATEGORIES = [
    "computer services",
    "massage",
    "clean house",
    "clean storage area",
    "makeup",
    "nails",
]

# emojis per category for nicer UI
CAT_EMOJIS = {
    "computer services": "💻",
    "massage": "💆",
    "clean house": "🧹",
    "clean storage area": "📦",
    "makeup": "💄",
    "nails": "💅",
}

# Localization
LOCALES = ["en", "ru"]
CAT_TRANSLATIONS = {
    "en": {
        c: c.title() for c in CATEGORIES
    },
    "ru": {
        "computer services": "Компьютерные услуги",
        "massage": "Массаж",
        "clean house": "Уборка дома",
        "clean storage area": "Уборка склада",
        "makeup": "Макияж",
        "nails": "Маникюр/Педикюр",
    },
}

T = {
    "en": {
        "choose_lang": "🌐 Choose language / Выберите язык:",
        "choose_category": "📂 Choose a category:",
        "no_posts": "No posts yet in {cat}.",
        "posts_in": "📰 Posts in {cat}:",
        "create_post": "➕ Create Post",
        "back": "↩️ Back",
        "send_post_text": "Send the post text for {cat} (it will auto-delete after 2 hours).",
        "post_created": "✅ Post created (id {id}). It will be removed in 2 hours.",
        "post_not_found": "⚠️ Post not found or expired.",
        "only_creator": "⚠️ Only the creator can delete this post.",
        "post_deleted": "🗑️ Post deleted.",
        "profile_title": "Your profile:",
        "no_posts_user": "You have no active posts.",
        "wallet": "Wallet: {amount}₽",
        "posts_count_line": "{cat}: {count}",
        "post_line": "- {cat}: expires in {time_left}",
        "created_ago": "Created: {time_ago} ago",
        "expires_in": "Expires in: {time_left}",
        "all_posts": "All posts:",
        "successfully_listed": "✅ Successfully listed.",
        "categories": "Categories",
    },
    "ru": {
        "choose_lang": "🌐 Выберите язык / Choose language:",
        "choose_category": "📂 Выберите категорию:",
        "no_posts": "Пока нет объявлений в {cat}.",
        "posts_in": "📰 Объявления в {cat}:",
        "create_post": "➕ Создать объявление",
        "back": "↩️ Назад",
        "send_post_text": "Отправьте текст объявления для {cat} (будет удалено через 2 часа).",
        "post_created": "✅ Объявление создано (id {id}). Оно будет удалено через 2 часа.",
        "post_not_found": "⚠️ Объявление не найдено или уже истекло.",
        "only_creator": "⚠️ Только автор может удалить это объявление.",
        "post_deleted": "🗑️ Объявление удалено.",
        "profile_title": "Ваш профиль:",
        "no_posts_user": "У вас нет активных объявлений.",
        "wallet": "Кошелёк: {amount}₽",
        "posts_count_line": "{cat}: {count}",
        "post_line": "- {cat}: истекает через {time_left}",
        "created_ago": "Создано: {time_ago} назад",
        "expires_in": "Истекает через: {time_left}",
        "all_posts": "Все объявления:",
        "successfully_listed": "✅ Объявление успешно размещено.",
        "categories": "Категории",
    },
}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            text TEXT NOT NULL,
            creator_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            creator_username TEXT
        )
        """
    )
    # migrate: add creator_username column if missing
    cur.execute("PRAGMA table_info(posts)")
    cols = [r[1] for r in cur.fetchall()]
    if "creator_username" not in cols:
        try:
            cur.execute("ALTER TABLE posts ADD COLUMN creator_username TEXT")
        except Exception:
            pass

    # create users table for virtual wallet
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 100
        )
        """
    )
    conn.commit()
    conn.close()


def create_post(category: str, text: str, creator_id: int, creator_username: str = None, expires_seconds: int = 7200):
    now = int(datetime.now(timezone.utc).timestamp())
    expires = now + expires_seconds
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO posts (category, text, creator_id, created_at, expires_at, creator_username) VALUES (?, ?, ?, ?, ?, ?)",
        (category, text, creator_id, now, expires, creator_username),
    )
    post_id = cur.lastrowid
    conn.commit()
    conn.close()
    return post_id


def ensure_user(uid: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, ?)", (uid, 100))
    conn.commit()
    conn.close()


def get_balance(uid: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (uid,))
    r = cur.fetchone()
    conn.close()
    if not r:
        return 0
    return int(r[0])


def charge_user(uid: int, amount: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (uid,))
    r = cur.fetchone()
    if not r:
        conn.close()
        return False
    bal = int(r[0])
    if bal < amount:
        conn.close()
        return False
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, uid))
    conn.commit()
    conn.close()
    return True


def get_posts(category: str):
    now = int(datetime.now(timezone.utc).timestamp())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, text, creator_id, created_at, expires_at FROM posts WHERE category = ? AND expires_at > ? ORDER BY created_at DESC",
        (category, now),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_post(post_id: int):
    now = int(datetime.now(timezone.utc).timestamp())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, category, text, creator_id, created_at, expires_at, creator_username FROM posts WHERE id = ? AND expires_at > ?",
        (post_id, now),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_all_posts():
    now = int(datetime.now(timezone.utc).timestamp())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, category, text, creator_id, created_at, expires_at, creator_username FROM posts WHERE expires_at > ? ORDER BY created_at DESC",
        (now,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def list_all_posts_markup(posts, lang: str = "en"):
    keyboard = []
    for p in posts:
        pid = p[0]
        # p: (id, category, text, creator_id, created_at, expires_at, creator_username)
        cat = p[1]
        text = p[2]
        # show only emoji (no category text) alongside a short preview
        emoji = CAT_EMOJIS.get(cat, "")
        label_text = text if len(text) <= 30 else text[:27] + "..."
        label = f"{emoji} {label_text}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"view:{pid}")])
    # bottom row: profile and categories
    keyboard.append([
        InlineKeyboardButton("👤 Profile", callback_data="profile"),
        InlineKeyboardButton(T.get(lang, T["en"])["categories"], callback_data="categories"),
    ])
    return InlineKeyboardMarkup(keyboard)


def list_posts_markup(category: str, posts, lang: str = "en"):
    """Build markup for posts within a single category. Shows emoji + preview for each post."""
    keyboard = []
    for p in posts:
        pid = p[0]
        # p: (id, text, creator_id, created_at, expires_at)
        text = p[1]
        emoji = CAT_EMOJIS.get(category, "")
        label_text = text if len(text) <= 30 else text[:27] + "..."
        label = f"{emoji} {label_text}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"view:{pid}")])
    # actions: create and back
    keyboard.append([InlineKeyboardButton(T.get(lang, T["en"])["create_post"], callback_data=f"create:{category}")])
    keyboard.append([InlineKeyboardButton(T.get(lang, T["en"])["back"], callback_data="back")])
    return InlineKeyboardMarkup(keyboard)


def delete_post_db(post_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()


async def cleanup_expired():
    now = int(datetime.now(timezone.utc).timestamp())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM posts WHERE expires_at <= ?", (now,))
    conn.commit()
    conn.close()


def build_categories_markup(lang: str = "en"):
    keyboard = []
    for c in CATEGORIES:
        label = CAT_TRANSLATIONS.get(lang, CAT_TRANSLATIONS["en"]).get(c, c.title())
        emoji = CAT_EMOJIS.get(c, "")
        keyboard.append([InlineKeyboardButton(f"{emoji} {label}", callback_data=f"cat:{c}")])
    # all posts, profile button and language switch
    keyboard.append([InlineKeyboardButton("📰 All Posts", callback_data="allposts")])
    keyboard.append([
        InlineKeyboardButton("👤 Profile", callback_data="profile"),
        InlineKeyboardButton("🌐 Рус/En", callback_data="switchlang"),
    ])
    return InlineKeyboardMarkup(keyboard)


def format_duration(seconds: int, lang: str = "en") -> str:
    seconds = max(0, int(seconds))
    mins = seconds // 60
    hrs = mins // 60
    mins = mins % 60
    if lang == "ru":
        if hrs and mins:
            return f"{hrs}ч {mins}м"
        if hrs:
            return f"{hrs}ч"
        return f"{mins}м"
    # default English
    if hrs and mins:
        return f"{hrs}h {mins}m"
    if hrs:
        return f"{hrs}h"
    return f"{mins}m"


async def clear_user_messages(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_data: dict):
    # delete previously recorded USER and BOT messages for a user/chat
    # delete user messages first
    user_msg_ids = user_data.get("user_messages", [])
    for mid in user_msg_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass
    user_data["user_messages"] = []
    # then delete bot messages recorded earlier
    bot_msg_ids = user_data.get("bot_messages", [])
    for mid in bot_msg_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass
    user_data["bot_messages"] = []


def record_bot_message(user_data: dict, message):
    user_data.setdefault("bot_messages", [])
    try:
        user_data["bot_messages"].append(message.message_id)
    except Exception:
        pass


def record_user_message(user_data: dict, message):
    user_data.setdefault("user_messages", [])
    try:
        user_data["user_messages"].append(message.message_id)
        # keep only last 5 user messages
        if len(user_data["user_messages"]) > 5:
            user_data["user_messages"] = user_data["user_messages"][-5:]
    except Exception:
        pass


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # show language selection (clear previous user messages)
    chat_id = update.effective_chat.id
    await clear_user_messages(context, chat_id, context.user_data)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("English", callback_data="lang:en"), InlineKeyboardButton("Русский", callback_data="lang:ru")]
    ])
    msg = await context.bot.send_message(chat_id=chat_id, text=T["en"]["choose_lang"], reply_markup=keyboard)
    # record this bot message so it can be cleared when the user selects a language
    record_bot_message(context.user_data, msg)
    return


async def listusers_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # list users and balances
    chat_id = update.effective_chat.id
    await clear_user_messages(context, chat_id, context.user_data)
    # record the user's /listusers command
    try:
        record_user_message(context.user_data, update.message)
    except Exception:
        pass
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, balance FROM users ORDER BY user_id")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        msg = await context.bot.send_message(chat_id=chat_id, text="No users found.")
        record_bot_message(context.user_data, msg)
        return
    lines = [f"Users ({len(rows)}):"]
    for uid, bal in rows:
        lines.append(f"{uid}: {bal}₽")
    msg = await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))
    record_bot_message(context.user_data, msg)


async def topup_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /topup <userid> <amount> -- restricted to admin
    user = update.effective_user
    allowed = False
    admin_id = os.environ.get("ADMIN_ID")
    if admin_id and str(user.id) == str(admin_id):
        allowed = True
    if user.username and user.username.lower() == "kittiking":
        allowed = True
    if not allowed:
        msg = await update.message.reply_text("Not authorized.")
        record_user_message(context.user_data, update.message)
        record_bot_message(context.user_data, msg)
        return
    args = context.args
    if len(args) < 2:
        msg = await update.message.reply_text("Usage: /topup <userid> <amount>")
        record_user_message(context.user_data, update.message)
        record_bot_message(context.user_data, msg)
        return
    try:
        uid = int(args[0])
        amt = int(args[1])
    except Exception:
        msg = await update.message.reply_text("Invalid arguments. Provide numeric user id and amount.")
        record_user_message(context.user_data, update.message)
        record_bot_message(context.user_data, msg)
        return
    ensure_user(uid)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, uid))
    conn.commit()
    conn.close()
    msg = await update.message.reply_text(f"Topped up {amt}₽ to user {uid}.")
    record_user_message(context.user_data, update.message)
    record_bot_message(context.user_data, msg)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    lang = context.user_data.get("lang", "en")
    chat_id = query.message.chat_id
    # clear previous USER messages for cleaner UI
    await clear_user_messages(context, chat_id, context.user_data)

    if data == "back":
        msg = await context.bot.send_message(chat_id=chat_id, text=T.get(lang, T["en"])["choose_category"], reply_markup=build_categories_markup(lang))
        record_bot_message(context.user_data, msg)
        return

    if data == "switchlang":
        # toggle
        new = "ru" if lang == "en" else "en"
        context.user_data["lang"] = new
        posts = get_all_posts()
        if posts:
            msg = await context.bot.send_message(chat_id=chat_id, text=T.get(new, T["en"])["all_posts"], reply_markup=list_all_posts_markup(posts, new))
        else:
            msg = await context.bot.send_message(chat_id=chat_id, text=T.get(new, T["en"])["all_posts"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T.get(new, T["en"])["categories"], callback_data="categories")]]))
        record_bot_message(context.user_data, msg)
        return

    if data.startswith("lang:"):
        new = data.split(":", 1)[1]
        if new not in LOCALES:
            new = "en"
        context.user_data["lang"] = new
        posts = get_all_posts()
        if posts:
            msg = await context.bot.send_message(chat_id=chat_id, text=T.get(new, T["en"])["all_posts"], reply_markup=list_all_posts_markup(posts, new))
        else:
            msg = await context.bot.send_message(chat_id=chat_id, text=T.get(new, T["en"])["all_posts"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T.get(new, T["en"])["categories"], callback_data="categories")]]))
        record_bot_message(context.user_data, msg)
        return

    if data.startswith("cat:"):
        category = data.split(":", 1)[1]
        posts = get_posts(category)
        cat_label = CAT_TRANSLATIONS.get(lang, CAT_TRANSLATIONS["en"]).get(category, category.title())
        if posts:
            msg = await context.bot.send_message(chat_id=chat_id, text=T.get(lang, T["en"])["posts_in"].format(cat=cat_label), reply_markup=list_posts_markup(category, posts, lang))
            record_bot_message(context.user_data, msg)
        else:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(T.get(lang, T["en"])["create_post"], callback_data=f"create:{category}")],
                [InlineKeyboardButton(T.get(lang, T["en"])["back"], callback_data="back")],
            ])
            msg = await context.bot.send_message(chat_id=chat_id, text=T.get(lang, T["en"])["no_posts"].format(cat=cat_label), reply_markup=keyboard)
            record_bot_message(context.user_data, msg)
        return

    if data == "categories":
        # show categories list
        msg = await context.bot.send_message(chat_id=chat_id, text=T.get(lang, T["en"])["choose_category"], reply_markup=build_categories_markup(lang))
        record_bot_message(context.user_data, msg)
        return

    if data == "allposts":
        # show all posts
        posts = get_all_posts()
        if posts:
            msg = await context.bot.send_message(chat_id=chat_id, text=T.get(lang, T["en"])["all_posts"], reply_markup=list_all_posts_markup(posts, lang))
        else:
            msg = await context.bot.send_message(chat_id=chat_id, text=T.get(lang, T["en"])["all_posts"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T.get(lang, T["en"])["categories"], callback_data="categories")]]))
        record_bot_message(context.user_data, msg)
        return

    if data == "topup":
        # send admin contact for top-up
        admin_contact = os.environ.get("ADMIN_CONTACT", "@kittiking")
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"To top up your wallet contact: {admin_contact}")
        return

    if data.startswith("view:"):
        pid = int(data.split(":", 1)[1])
        row = get_post(pid)
        if not row:
            msg = await context.bot.send_message(chat_id=chat_id, text=T.get(lang, T["en"])["post_not_found"])
            record_bot_message(context.user_data, msg)
            return
        _, category, text, creator_id, created_at, expires_at, creator_username = row
        now = int(datetime.now(timezone.utc).timestamp())
        created_delta = now - created_at
        expires_delta = expires_at - now
        created_label = format_duration(created_delta, lang)
        expires_label = format_duration(expires_delta, lang)
        created = T.get(lang, T["en"])["created_ago"].format(time_ago=created_label)
        expires = T.get(lang, T["en"])["expires_in"].format(time_left=expires_label)
        # include category in the view
        cat_label = CAT_TRANSLATIONS.get(lang, CAT_TRANSLATIONS["en"]).get(category, category.title())
        cat_line = f"Category: {cat_label}"
        kb = []
        if user_id == creator_id:
            kb.append([InlineKeyboardButton("Delete Post", callback_data=f"delete:{pid}")])
        # add contact info if username present
        contact_line = None
        if creator_username:
            contact_line = f"Contact: https://t.me/{creator_username} (@{creator_username})"
        else:
            contact_line = "Contact: (no username)"
        kb.append([InlineKeyboardButton(T.get(lang, T["en"])["back"], callback_data=f"cat:{category}")])
        msg = await context.bot.send_message(chat_id=chat_id, text=f"{cat_line}\n\n{text}\n\n{created}\n{expires}\n{contact_line}", reply_markup=InlineKeyboardMarkup(kb))
        record_bot_message(context.user_data, msg)
        return

    if data.startswith("delete:"):
        pid = int(data.split(":", 1)[1])
        row = get_post(pid)
        if not row:
            msg = await context.bot.send_message(chat_id=chat_id, text=T.get(lang, T["en"])["post_not_found"])
            record_bot_message(context.user_data, msg)
            return
        _, category, text, creator_id, *_ = row
        if user_id != creator_id:
            await query.answer(T.get(lang, T["en"])["only_creator"], show_alert=True)
            return
        delete_post_db(pid)
        msg = await context.bot.send_message(chat_id=chat_id, text=T.get(lang, T["en"])["post_deleted"])
        record_bot_message(context.user_data, msg)
        return

    if data.startswith("create:"):
        category = data.split(":", 1)[1]
        # present price/duration options (previous messages already cleared)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏱️ 2h — 20₽", callback_data=f"create2:{category}"), InlineKeyboardButton("⏱️ 24h — 50₽", callback_data=f"create24:{category}")],
            [InlineKeyboardButton(T.get(lang, T["en"])["back"], callback_data=f"cat:{category}")],
        ])
        msg = await context.bot.send_message(chat_id=chat_id, text=f"{T.get(lang, T['en'])['create_post']} — choose duration and price:", reply_markup=keyboard)
        record_bot_message(context.user_data, msg)
        return

    if data.startswith("create2:") or data.startswith("create24:"):
        # user chose duration and price, now ask for post text
        parts = data.split(":", 1)
        kind = parts[0]
        category = parts[1]
        if kind == "create2":
            duration = 2 * 3600
            price = 20
        else:
            duration = 24 * 3600
            price = 50
        # previous messages already cleared
        # store pending creation details in user_data
        context.user_data["creating_cat"] = category
        context.user_data["creating_price"] = price
        context.user_data["creating_duration"] = duration
        cat_label = CAT_TRANSLATIONS.get(lang, CAT_TRANSLATIONS["en"]).get(category, category.title())
        msg = await context.bot.send_message(chat_id=chat_id, text=T.get(lang, T["en"])["send_post_text"].format(cat=cat_label))
        record_bot_message(context.user_data, msg)
        return

    if data == "profile":
        # show profile for user
        uid = user_id
        now = int(datetime.now(timezone.utc).timestamp())
        ensure_user(uid)
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT category, COUNT(*) FROM posts WHERE creator_id = ? AND expires_at > ? GROUP BY category",
            (uid, now),
        )
        counts = cur.fetchall()
        cur.execute(
            "SELECT category, expires_at FROM posts WHERE creator_id = ? AND expires_at > ? ORDER BY expires_at ASC",
            (uid, now),
        )
        posts = cur.fetchall()
        conn.close()

        bal = get_balance(uid)
        lines = [T.get(lang, T["en"])["profile_title"]]
        lines.append(f"User ID: {uid}")
        lines.append(T.get(lang, T["en"])["wallet"].format(amount=bal))
        if counts:
            for cat, cnt in counts:
                cat_label = CAT_TRANSLATIONS.get(lang, CAT_TRANSLATIONS["en"]).get(cat, cat.title())
                lines.append(T.get(lang, T["en"])["posts_count_line"].format(cat=cat_label, count=cnt))
        else:
            lines.append(T.get(lang, T["en"])["no_posts_user"])

        if posts:
            for cat, expires_at in posts:
                left = expires_at - now
                mins = max(0, int(left // 60))
                if mins >= 60:
                    hrs = mins // 60
                    mins = mins % 60
                    tl = f"{hrs}h {mins}m" if mins else f"{hrs}h"
                else:
                    tl = f"{mins}m"
                cat_label = CAT_TRANSLATIONS.get(lang, CAT_TRANSLATIONS["en"]).get(cat, cat.title())
                lines.append(T.get(lang, T["en"])["post_line"].format(cat=cat_label, time_left=tl))

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Top UP", callback_data="topup")],
            [InlineKeyboardButton(T.get(lang, T["en"])["back"], callback_data="back")],
        ])
        msg = await context.bot.send_message(chat_id=chat_id, text="\n".join(lines), reply_markup=keyboard)
        record_bot_message(context.user_data, msg)
        return


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "en")
    # record this user message so UI can be cleared on next action
    try:
        record_user_message(context.user_data, update.message)
    except Exception:
        pass
    if "creating_cat" in context.user_data:
        category = context.user_data.pop("creating_cat")
        text = update.message.text
        creator_id = update.message.from_user.id
        creator_username = update.message.from_user.username
        # determine duration and price (set earlier in create2/create24 flow)
        expires_seconds = context.user_data.pop("creating_duration", None) or 2 * 3600
        price = context.user_data.pop("creating_price", 0)
        # charge user if needed
        ensure_user(creator_id)
        if price and not charge_user(creator_id, price):
            await update.message.reply_text("Insufficient balance. Please top up your wallet.")
            return
        # create post with the selected expiration
        pid = create_post(category, text, creator_id, creator_username, expires_seconds=expires_seconds)
        # schedule deletion via application job queue
        context.application.job_queue.run_once(_job_delete_wrapper, when=expires_seconds, data=pid)

        # clear previous user and bot messages for cleaner UI
        chat_id = update.effective_chat.id
        await clear_user_messages(context, chat_id, context.user_data)

        created_msg = T.get(lang, T["en"])["post_created"].format(id=pid)
        success_msg = T.get(lang, T["en"])["successfully_listed"]
        msg = await context.bot.send_message(chat_id=chat_id, text=f"{created_msg}\n\n{success_msg}")
        record_bot_message(context.user_data, msg)

        # send Back button to return to category list
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(T.get(lang, T["en"])["back"], callback_data="back")]
        ])
        msg = await context.bot.send_message(chat_id=chat_id, text="✅", reply_markup=keyboard)
        record_bot_message(context.user_data, msg)
        return

    await update.message.reply_text(T.get(lang, T["en"])["choose_category"])


async def _job_delete_wrapper(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    pid = job.data
    delete_post_db(pid)


def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        print("Set the TELEGRAM_TOKEN environment variable and run this script.")
        return

    init_db()

    app = ApplicationBuilder().token(token).build()

    asyncio.get_event_loop().run_until_complete(cleanup_expired())

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("listusers", listusers_handler))
    app.add_handler(CommandHandler("topup", topup_command_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("Bot is starting. Press Ctrl-C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
