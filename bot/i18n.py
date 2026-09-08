"""Two languages, one dict. Bot picks language from Telegram's language_code."""

STR = {
    "en": {
        "menu": "Menu:",
        "b_panel": "🛠 Panel", "b_status": "📊 Status", "b_nodes": "💻 Devices", "b_key": "🔑 New key",
        "b_key_reuse": "🔑 Reusable key", "b_update": "⬆️ Updates", "b_help": "❓ Help",
        "b_upd_hs": "Update headscale → {v}", "b_upd_bot": "Update bot", "b_rollback": "↩ Roll back headscale to {v}",
        "b_back": "⬅ Back", "b_yes": "✅ Yes", "b_no": "❌ Cancel",
        "not_admin": "This bot is private. Ask the owner for access.",
        "claimed": "👑 You are the admin now. Preparing your first connection key…",
        "claim_used": "This link was already used.",
        "already_admin": "You are already an admin.",
        "welcome": "Welcome to your VPN 🎉\nThe key below adds your first device. Press 🛠 Panel for everything else.",
        "key_text": ("Connect to VPN\n\n"
                     "1) Install Tailscale: https://tailscale.com/download\n\n"
                     "2) Computer — run:\n{cmd}\n\n"
                     "3) Phone — in the Tailscale app: menu → Use alternate server\n"
                     "   server: {url}\n   key: {key}\n\n"
                     "Key is {kind}, valid until {exp}."),
        "kind_reuse": "reusable", "kind_once": "single-use",
        "status_ok": "🟢 headscale is reachable", "status_bad": "🔴 headscale is NOT reachable",
        "nodes_online": "online: {on} / {total}", "no_nodes": "No devices yet.",
        "help": ("/menu, /status, /nodes, /key [reuse], /update, /help\n"
                 "Panel — full control: devices, keys with QR, routes, exit nodes, users, files.\n"
                 "Files: send me a document → you get a sendme ticket; send a ticket → I fetch the file.\n"
                 "Node registration: paste the /register/… link from `tailscale up`."),
        "upd_title": "Versions",
        "upd_hs": "headscale: {cur} → latest {latest}", "upd_bot": "bot: {cur}",
        "upd_none": "Everything is up to date.",
        "upd_external": "headscale is managed outside HeadLauncher (attach mode) — update it as you usually do.",
        "upd_ask_hs": "Update headscale {cur} → {new}? The database is backed up first; clients reconnect automatically.",
        "upd_ask_bot": "Update the bot? It restarts in ~30 s.",
        "upd_ask_rb": "Roll back headscale to {v}?",
        "upd_started": "⏳ Started. I will report when done.",
        "upd_done": "✅ headscale is now {v}.", "upd_failed": "❌ Update failed:\n{err}\nPrevious version restored: {v}",
        "upd_bot_started": "⏳ Updating the bot — back in a moment.",
        "backup_done": "💾 Backup: {name}",
        "downloading": "⏳ Downloading…", "share_caption": "📤 {name} ({size})\nReceive:\n<code>sendme receive {ticket}</code>",
        "recv_started": "⏳ Receiving by ticket…", "recv_big": "📥 {name} ({size}) — over 50 MB, kept on the server: {path}",
        "recv_fail": "❌ Receive failed: {err}", "registered": "✅ Registered {name} — {ips}",
        "unknown": "I don't understand that. /help",
    },
    "ru": {
        "menu": "Меню:",
        "b_panel": "🛠 Панель", "b_status": "📊 Статус", "b_nodes": "💻 Устройства", "b_key": "🔑 Новый ключ",
        "b_key_reuse": "🔑 Многоразовый", "b_update": "⬆️ Обновления", "b_help": "❓ Помощь",
        "b_upd_hs": "Обновить headscale → {v}", "b_upd_bot": "Обновить бота", "b_rollback": "↩ Откатить headscale на {v}",
        "b_back": "⬅ Назад", "b_yes": "✅ Да", "b_no": "❌ Отмена",
        "not_admin": "Это приватный бот. Доступ выдаёт владелец.",
        "claimed": "👑 Теперь ты администратор. Готовлю первый ключ подключения…",
        "claim_used": "Эта ссылка уже использована.",
        "already_admin": "Ты уже администратор.",
        "welcome": "Твой VPN готов 🎉\nКлюч ниже добавляет первое устройство. Всё остальное — кнопка 🛠 Панель.",
        "key_text": ("Подключение к VPN\n\n"
                     "1) Установи Tailscale: https://tailscale.com/download\n\n"
                     "2) Компьютер — выполни:\n{cmd}\n\n"
                     "3) Телефон — в приложении Tailscale: меню → Use alternate server\n"
                     "   адрес: {url}\n   ключ: {key}\n\n"
                     "Ключ {kind}, действует до {exp}."),
        "kind_reuse": "многоразовый", "kind_once": "одноразовый",
        "status_ok": "🟢 headscale доступен", "status_bad": "🔴 headscale НЕДОСТУПЕН",
        "nodes_online": "online: {on} / {total}", "no_nodes": "Устройств пока нет.",
        "help": ("/menu, /status, /nodes, /key [reuse], /update, /help\n"
                 "Панель — полное управление: устройства, ключи с QR, маршруты, exit-node, пользователи, файлы.\n"
                 "Файлы: пришли документ — получишь тикет sendme; пришли тикет — скачаю файл.\n"
                 "Регистрация узла: пришли ссылку /register/… из `tailscale up`."),
        "upd_title": "Версии",
        "upd_hs": "headscale: {cur} → последняя {latest}", "upd_bot": "бот: {cur}",
        "upd_none": "Всё актуально.",
        "upd_external": "headscale установлен отдельно (режим attach) — обновляй его как обычно.",
        "upd_ask_hs": "Обновить headscale {cur} → {new}? Сначала бэкап базы; клиенты переподключатся сами.",
        "upd_ask_bot": "Обновить бота? Перезапуск ~30 с.",
        "upd_ask_rb": "Откатить headscale на {v}?",
        "upd_started": "⏳ Запущено. Сообщу, когда закончится.",
        "upd_done": "✅ headscale теперь {v}.", "upd_failed": "❌ Обновление не удалось:\n{err}\nВозвращена версия {v}",
        "upd_bot_started": "⏳ Обновляю бота — вернусь через минуту.",
        "backup_done": "💾 Бэкап: {name}",
        "downloading": "⏳ Скачиваю…", "share_caption": "📤 {name} ({size})\nПолучить:\n<code>sendme receive {ticket}</code>",
        "recv_started": "⏳ Принимаю по тикету…", "recv_big": "📥 {name} ({size}) — больше 50 МБ, лежит на сервере: {path}",
        "recv_fail": "❌ Приём не удался: {err}", "registered": "✅ Зарегистрирован {name} — {ips}",
        "unknown": "Не понял. /help",
    },
}


def t(lang: str, _k: str, **kw) -> str:
    d = STR.get(lang) or STR["en"]
    s = d.get(_k) or STR["en"].get(_k) or _k
    return s.format(**kw) if kw else s


def pick(code: str | None, default: str = "en") -> str:
    if code and code.lower().startswith("ru"):
        return "ru"
    if code:
        return "en"
    return default if default in STR else "en"
