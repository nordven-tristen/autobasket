"""
Telegram бот для формирования списка покупок
Принимает сообщения с рецептами или списками продуктов,
обрабатывает через LLM (Claude или GigaChat) и запускает автоматизацию Ozon
"""

import asyncio
import os
import yaml
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from llm_client import create_llm_client, LLMClient

# Загружаем переменные окружения
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "claude")

# Загружаем предпочтения
PREFERENCES_FILE = Path(__file__).parent / "preferences.yaml"


def load_preferences() -> dict:
    """Загружает предпочтения из YAML файла"""
    if PREFERENCES_FILE.exists():
        with open(PREFERENCES_FILE, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}


def get_system_prompt(preferences: dict) -> str:
    """Формирует системный промпт для LLM"""

    default_servings = preferences.get('default_servings', 3)
    favorite_brands = preferences.get('favorite_brands', {})
    product_prefs = preferences.get('product_preferences', {})
    exclusions = preferences.get('exclusions', [])

    # Формируем текст о любимых брендах
    brands_text = ""
    if favorite_brands:
        brands_list = [f"- {product}: {brand}" for product, brand in favorite_brands.items() if brand]
        if brands_list:
            brands_text = "Любимые производители:\n" + "\n".join(brands_list)

    # Формируем текст об исключениях
    exclusions_text = ""
    if exclusions:
        exclusions_text = f"\nИСКЛЮЧИТЬ из списка (аллергия/не покупаем): {', '.join(exclusions)}"

    return f"""Ты помощник для составления списка покупок на маркетплейсе Ozon Fresh.

ТВОЯ ЗАДАЧА:
1. Если пользователь прислал рецепт или название блюда - составь список ингредиентов на {default_servings} персон (если не указано иное)
2. Если пользователь прислал список продуктов - преобразуй в стандартные названия для маркетплейса

ПРАВИЛА ФОРМАТИРОВАНИЯ:
- Каждый продукт на новой строке
- Используй общепринятые названия как на маркетплейсах
- Добавляй характеристики: жирность, вес, количество
- НЕ добавляй нумерацию, тире или маркеры списка

ПРИМЕРЫ ПРЕОБРАЗОВАНИЙ:
- "масло" → "масло сливочное 82.5%"
- "молоко" → "молоко 3.2% 1л"
- "яйца" → "яйцо куриное С1 10 шт"
- "курица" → "филе куриное охлаждённое 500г"
- "сметана" → "сметана 20% 200г"
- "творог" → "творог 5% 200г"
- "помидоры" → "томаты 500г"
- "лук" → "лук репчатый 500г"

{brands_text}
{exclusions_text}

ФОРМАТ ОТВЕТА:
Выведи ТОЛЬКО список продуктов, каждый на новой строке. Без пояснений, без нумерации, без маркеров.
Если нужно указать количество - пиши в конце строки (например: "филе куриное 1 кг")
"""


class ShoppingListBot:
    """Telegram бот для формирования списка покупок"""

    def __init__(self):
        self.llm: LLMClient = create_llm_client(LLM_PROVIDER)
        self.preferences = load_preferences()
        self.system_prompt = get_system_prompt(self.preferences)
        print(f"🤖 Используется LLM провайдер: {LLM_PROVIDER}")

    async def process_with_llm(self, user_message: str) -> list[str]:
        """Обрабатывает сообщение через LLM"""

        # LLM клиент синхронный, запускаем в executor
        loop = asyncio.get_event_loop()
        content = await loop.run_in_executor(
            None,
            self.llm.generate,
            self.system_prompt,
            user_message
        )

        # Парсим ответ - каждая строка = один продукт
        products = [line.strip() for line in content.strip().split('\n') if line.strip()]

        return products

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        provider_name = "Claude" if LLM_PROVIDER == "claude" else "GigaChat"
        await update.message.reply_text(
            f"👋 Привет! Я помогу составить список покупок для Ozon Fresh.\n\n"
            f"🧠 Использую: {provider_name}\n\n"
            "📝 Отправь мне:\n"
            "• Название блюда (например: \"борщ\")\n"
            "• Рецепт с ингредиентами\n"
            "• Просто список продуктов\n\n"
            "🛒 Я преобразую всё в список для маркетплейса и добавлю в корзину!\n\n"
            "Команды:\n"
            "/help - справка\n"
            "/preferences - показать настройки\n"
            "/model - показать текущую модель"
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        await update.message.reply_text(
            "📖 Как пользоваться ботом:\n\n"
            "1️⃣ Отправь название блюда:\n"
            "   \"Карбонара на 4 персоны\"\n\n"
            "2️⃣ Или список продуктов:\n"
            "   \"молоко, яйца, масло, хлеб\"\n\n"
            "3️⃣ Или рецепт с ингредиентами:\n"
            "   \"Для блинов нужно: мука 200г, молоко 500мл, яйца 2шт\"\n\n"
            "🔧 Настройки в файле preferences.yaml:\n"
            "• Любимые производители\n"
            "• Количество персон по умолчанию\n"
            "• Исключения (аллергия)"
        )

    async def model_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает текущую модель"""
        if LLM_PROVIDER == "claude":
            model_info = "Claude Sonnet 4 (Anthropic)"
        else:
            model_info = "GigaChat-2-Max (Sber)"

        await update.message.reply_text(
            f"🧠 Текущий LLM провайдер:\n\n"
            f"**{model_info}**\n\n"
            f"Для смены модели измените LLM_PROVIDER в .env",
            parse_mode='Markdown'
        )

    async def preferences_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает текущие настройки"""
        prefs = self.preferences

        text = f"⚙️ Текущие настройки:\n\n"
        text += f"👥 Персон по умолчанию: {prefs.get('default_servings', 3)}\n\n"

        brands = prefs.get('favorite_brands', {})
        if brands:
            text += "🏷 Любимые бренды:\n"
            for product, brand in brands.items():
                if brand:
                    text += f"  • {product}: {brand}\n"

        exclusions = prefs.get('exclusions', [])
        if exclusions:
            text += f"\n🚫 Исключения: {', '.join(exclusions)}"

        await update.message.reply_text(text)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает текстовые сообщения"""
        user_message = update.message.text

        # Показываем что бот обрабатывает запрос
        provider_name = "Claude" if LLM_PROVIDER == "claude" else "GigaChat"
        processing_msg = await update.message.reply_text(f"🔄 Обрабатываю через {provider_name}...")

        try:
            # Получаем список продуктов от LLM
            products = await self.process_with_llm(user_message)

            if not products:
                await processing_msg.edit_text("❌ Не удалось распознать продукты. Попробуйте переформулировать.")
                return

            # Формируем ответ
            response = "🛒 Список покупок:\n\n"
            for i, product in enumerate(products, 1):
                response += f"{i}. {product}\n"

            response += f"\n✅ Всего: {len(products)} позиций"
            response += "\n\n🚀 Запускаю добавление в корзину Ozon..."

            await processing_msg.edit_text(response)

            # Сохраняем список в файл для get-ozon.py
            await self.save_shopping_list(products)

            await update.message.reply_text(
                "📋 Список сохранён в shopping_list.txt\n"
                "Для запуска автоматизации выполните:\n"
                "`python get-ozon.py`",
                parse_mode='Markdown'
            )

        except Exception as e:
            await processing_msg.edit_text(f"❌ Ошибка: {str(e)}")

    async def save_shopping_list(self, products: list[str]):
        """Сохраняет список покупок в файл"""
        filepath = Path(__file__).parent / "shopping_list.txt"
        with open(filepath, 'w', encoding='utf-8') as f:
            for product in products:
                f.write(f"{product}\n")

    def run(self):
        """Запускает бота"""
        if not TELEGRAM_BOT_TOKEN:
            print("❌ Ошибка: TELEGRAM_BOT_TOKEN не установлен в .env")
            return

        print("🤖 Запускаем Telegram бота...")

        # Создаём приложение
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        # Добавляем обработчики
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("preferences", self.preferences_command))
        app.add_handler(CommandHandler("model", self.model_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        # Запускаем бота
        print("✅ Бот запущен! Нажмите Ctrl+C для остановки.")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    bot = ShoppingListBot()
    bot.run()
