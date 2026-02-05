"""
Автоматизация наполнения корзины на Ozon Fresh
Использует Playwright для браузерной автоматизации и PaddleOCR для распознавания текста
"""

import argparse
import asyncio
import io
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from paddleocr import PaddleOCR


# ============ КОНФИГУРАЦИЯ ============

@dataclass
class Config:
    OZON_URL: str = "https://www.ozon.ru"
    OZON_FRESH_URL: str = "https://www.ozon.ru/highlight/ozon-fresh/"
    SEARCH_TIMEOUT: int = 30000  # ms
    PAGE_LOAD_TIMEOUT: int = 60000  # ms
    MAX_PRODUCTS_TO_CHECK: int = 5
    DELIVERY_FILTERS: tuple = ("сегодня", "завтра")
    HEADLESS: bool = False  # False для отладки, True для production
    # Путь для сохранения сессии браузера (cookies, localStorage и т.д.)
    USER_DATA_DIR: str = "./ozon_browser_data"
    # Ждать ручного логина при первом запуске
    WAIT_FOR_LOGIN: bool = True
    # Файл со списком покупок (генерируется telegram_bot.py)
    SHOPPING_LIST_FILE: str = "./shopping_list.txt"


# ============ СПИСОК ПОКУПОК ============

def load_shopping_list() -> list[str]:
    """Загружает список покупок из файла или возвращает дефолтный"""
    filepath = Path(Config.SHOPPING_LIST_FILE)
    if filepath.exists():
        with open(filepath, 'r', encoding='utf-8') as f:
            items = [line.strip() for line in f if line.strip()]
            if items:
                print(f"📋 Загружен список из {filepath}: {len(items)} позиций")
                return items

    # Дефолтный список если файл не найден
    return ["сливочное масло 82,5%"]


pokupki = load_shopping_list()


# ============ МОДЕЛЬ ТОВАРА ============

@dataclass
class Product:
    """Информация о товаре"""
    name: str
    price: float
    delivery: str
    card_element: any  # Playwright locator карточки товара


# ============ ОСНОВНОЙ КЛАСС ============

class OzonAutomation:
    """Класс для автоматизации покупок на Ozon"""

    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.ocr = PaddleOCR(lang='ru')

    async def wait_for_manual_login(self):
        """
        Ожидает ручной авторизации пользователя.
        Проверяет наличие элементов, указывающих на авторизацию.
        """
        print("\n" + "=" * 60)
        print("ТРЕБУЕТСЯ АВТОРИЗАЦИЯ")
        print("=" * 60)
        print("Пожалуйста, войдите в свой аккаунт Ozon в открытом браузере.")
        print("После входа нажмите Enter в этом окне для продолжения...")
        print("=" * 60)

        # Ждем ввода пользователя в консоли
        await asyncio.get_event_loop().run_in_executor(None, input)

        print("\nПродолжаем работу...")
        await self.wait_for_page_load()

    async def check_if_logged_in(self) -> bool:
        """
        Проверяет, авторизован ли пользователь.
        Ищет элементы, которые видны только авторизованным пользователям.
        """
        try:
            # Ищем элементы, характерные для авторизованного пользователя
            # Например, иконку профиля или имя пользователя
            logged_in_selectors = [
                '[data-widget="userMenu"]',
                '[data-widget="headerIcon"] [href*="/my/main"]',
                'a[href*="/my/main"]',
            ]

            for selector in logged_in_selectors:
                element = self.page.locator(selector).first
                if await element.count() > 0:
                    return True

            return False
        except Exception:
            return False

    async def take_screenshot(self) -> np.ndarray:
        """Создает скриншот страницы и возвращает как numpy array для OCR"""
        screenshot_bytes = await self.page.screenshot()
        image = Image.open(io.BytesIO(screenshot_bytes))
        return np.array(image)

    def find_text_with_ocr(self, image: np.ndarray, target_text: str) -> Optional[tuple]:
        """
        Ищет текст на изображении через PaddleOCR

        Args:
            image: numpy array изображения
            target_text: искомый текст (регистронезависимый поиск)

        Returns:
            Координаты центра найденного текста (x, y) или None
        """
        results = self.ocr.predict(image)

        if not results:
            return None

        for result in results:
            # Новый API PaddleOCR возвращает объект с rec_texts, rec_scores, rec_polys
            texts = getattr(result, 'rec_texts', [])
            scores = getattr(result, 'rec_scores', [])
            polys = getattr(result, 'rec_polys', [])

            for i, (text, score, poly) in enumerate(zip(texts, scores, polys)):
                if target_text.lower() in text.lower() and score > 0.5:
                    # poly - это массив координат углов текста
                    # Вычисляем центр bounding box
                    x_center = (poly[0][0] + poly[2][0]) / 2
                    y_center = (poly[0][1] + poly[2][1]) / 2
                    print(f"  OCR нашел '{text}' (уверенность: {score:.2f}) в координатах ({int(x_center)}, {int(y_center)})")
                    return (int(x_center), int(y_center))

        return None

    async def click_by_ocr_text(self, target_text: str, retries: int = 3) -> bool:
        """
        Находит текст через OCR и кликает по нему

        Args:
            target_text: текст для поиска
            retries: количество попыток

        Returns:
            True если клик выполнен, False иначе
        """
        print(f"Ищем текст через OCR: '{target_text}'")

        for attempt in range(retries):
            screenshot = await self.take_screenshot()
            coords = self.find_text_with_ocr(screenshot, target_text)

            if coords:
                print(f"  Кликаем по координатам: {coords}")
                await self.random_delay()
                await self.page.mouse.click(coords[0], coords[1])
                return True

            print(f"  Попытка {attempt + 1}/{retries}: текст не найден, ждем...")
            await asyncio.sleep(2)

        print(f"  Текст '{target_text}' не найден после {retries} попыток")
        return False

    async def random_delay(self, min_ms: int = 1000, max_ms: int = 3000):
        """Рандомная задержка между действиями для имитации человека"""
        delay = random.randint(min_ms, max_ms) / 1000
        await asyncio.sleep(delay)

    async def wait_for_page_load(self):
        """Комплексное ожидание загрузки страницы"""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=Config.PAGE_LOAD_TIMEOUT)
        except Exception:
            pass  # Таймаут не критичен

        # Рандомная задержка для имитации человека
        await self.random_delay()

    async def navigate_to_ozon_fresh(self) -> bool:
        """
        Переход в раздел Ozon Fresh
        Используем прямой URL (быстрее чем OCR)
        """
        print("\nПереходим в Ozon Fresh...")
        print("  Используем прямой URL для Ozon Fresh")
        await self.page.goto(Config.OZON_FRESH_URL)
        await self.wait_for_page_load()
        return True

    async def search_product(self, product_name: str):
        """
        Поиск товара через поисковую строку

        Args:
            product_name: название товара для поиска
        """
        print(f"\nИщем товар: '{product_name}'")

        # Находим поисковую строку
        search_selectors = [
            'input[placeholder*="Искать"]',
            'input[placeholder*="искать"]',
            '[data-widget="searchBarDesktop"] input',
            'input[name="text"]',
            'form input[type="text"]'
        ]

        search_input = None
        for selector in search_selectors:
            try:
                element = self.page.locator(selector).first
                if await element.is_visible(timeout=2000):
                    search_input = element
                    print(f"  Найдена поисковая строка: {selector}")
                    break
            except Exception:
                continue

        if not search_input:
            print("  Не удалось найти поисковую строку, пробуем через OCR...")
            await self.click_by_ocr_text("Искать")
            search_input = self.page.locator('input:focus').first

        # Очищаем и вводим текст
        await self.random_delay()
        await search_input.clear()
        await self.random_delay(500, 1500)
        await search_input.fill(product_name)
        await self.random_delay()

        # Нажимаем Enter для поиска
        await search_input.press("Enter")
        print("  Поиск запущен, ожидаем результаты...")
        await self.wait_for_page_load()

    def extract_price(self, price_text: str) -> float:
        """
        Извлекает числовое значение цены из текста

        Args:
            price_text: текст с ценой (например "199 ₽" или "1 234,50 ₽")

        Returns:
            Числовое значение цены или inf если не удалось распарсить
        """
        if not price_text:
            return float('inf')

        # Убираем все кроме цифр, запятой и точки
        cleaned = re.sub(r'[^\d,.]', '', price_text)
        cleaned = cleaned.replace(',', '.')

        # Обрабатываем случай с разделителем тысяч (1.234.50 -> 1234.50)
        parts = cleaned.split('.')
        if len(parts) > 2:
            cleaned = ''.join(parts[:-1]) + '.' + parts[-1]

        try:
            return float(cleaned)
        except ValueError:
            return float('inf')

    async def parse_products(self) -> list[Product]:
        """
        Парсинг карточек товаров со страницы результатов поиска

        Returns:
            Список объектов Product (до MAX_PRODUCTS_TO_CHECK штук)
        """
        print("\nПарсим карточки товаров...")
        products = []

        # Основной селектор - карточки с data-index
        card_selectors = [
            'div[data-index]',
            '[data-widget="searchResultsV2"] div[data-index]',
            '.tile-root'
        ]

        product_cards = None
        for selector in card_selectors:
            locator = self.page.locator(selector)
            count = await locator.count()
            if count > 0:
                product_cards = locator
                print(f"  Найдены карточки по селектору: {selector} (всего: {count})")
                break

        if not product_cards:
            print("  Не удалось найти карточки товаров")
            return products

        count = min(await product_cards.count(), Config.MAX_PRODUCTS_TO_CHECK)
        print(f"  Обрабатываем первые {count} карточек...")

        for i in range(count):
            card = product_cards.nth(i)

            try:
                # Извлекаем название из ссылки на продукт
                name = ""
                name_link = card.locator('a[href*="/product/"]').first
                if await name_link.count() > 0:
                    # Пробуем получить текст из span внутри ссылки
                    name_span = name_link.locator('span').first
                    if await name_span.count() > 0:
                        name = await name_span.text_content() or ""
                    if not name:
                        name = await name_link.get_attribute('href') or ""
                        # Извлекаем имя из URL
                        if '/product/' in name:
                            name = name.split('/product/')[1].split('/')[0].replace('-', ' ')
                    name = name.strip()[:80]

                # Извлекаем цену - первый span с ₽
                price = float('inf')
                price_spans = card.locator('span:has-text("₽")')
                if await price_spans.count() > 0:
                    price_text = await price_spans.first.text_content() or ""
                    price = self.extract_price(price_text)

                # Извлекаем информацию о доставке из кнопки
                delivery = ""
                delivery_button = card.locator('button')
                if await delivery_button.count() > 0:
                    btn_text = await delivery_button.first.text_content() or ""
                    btn_text_lower = btn_text.lower()
                    if 'сегодня' in btn_text_lower or 'завтра' in btn_text_lower:
                        delivery = btn_text_lower

                if price < float('inf'):
                    product = Product(
                        name=name if name else f"Товар {i+1}",
                        price=price,
                        delivery=delivery,
                        card_element=card
                    )
                    products.append(product)
                    print(f"    [{i+1}] {name[:40] if name else 'Без названия'}... | {price}₽ | Доставка: {delivery or 'не указана'}")

            except Exception as e:
                print(f"    [{i+1}] Ошибка парсинга: {e}")
                continue

        return products

    def filter_by_delivery(self, products: list[Product]) -> list[Product]:
        """
        Фильтрует товары по доставке сегодня/завтра

        Args:
            products: список товаров

        Returns:
            Отфильтрованный список
        """
        filtered = []
        for product in products:
            for delivery_keyword in Config.DELIVERY_FILTERS:
                if delivery_keyword in product.delivery:
                    filtered.append(product)
                    break
        return filtered

    def find_cheapest(self, products: list[Product]) -> Optional[Product]:
        """
        Находит товар с минимальной ценой

        Args:
            products: список товаров

        Returns:
            Товар с минимальной ценой или None
        """
        if not products:
            return None
        return min(products, key=lambda p: p.price)

    async def add_to_cart(self, product: Product) -> bool:
        """
        Добавляет товар в корзину

        Args:
            product: товар для добавления

        Returns:
            True если успешно добавлен
        """
        print(f"\nДобавляем в корзину: {product.name[:50]}...")

        try:
            card = product.card_element

            # На Ozon кнопка доставки (Завтра/Сегодня) является кнопкой добавления в корзину
            # Сначала пробуем найти любую кнопку в карточке
            button = card.locator('button').first
            if await button.count() > 0 and await button.is_visible():
                btn_text = await button.text_content() or ""
                print(f"  Найдена кнопка: '{btn_text}'")
                await self.random_delay()
                await button.click()
                print("  Товар добавлен в корзину!")
                await self.random_delay()
                return True

            # Альтернативные селекторы
            button_selectors = [
                'button:has-text("Завтра")',
                'button:has-text("Сегодня")',
                'button:has-text("В корзину")',
                'button:has-text("Добавить")'
            ]

            for selector in button_selectors:
                button = card.locator(selector).first
                if await button.count() > 0 and await button.is_visible():
                    await self.random_delay()
                    await button.click()
                    print("  Товар добавлен в корзину!")
                    await self.random_delay()
                    return True

            # Fallback: пробуем кликнуть по карточке и найти кнопку
            print("  Кнопка не найдена в карточке, пробуем кликнуть по товару...")
            await self.random_delay()
            await card.click()
            await self.wait_for_page_load()

            # На странице товара ищем кнопку
            page_button = self.page.locator('button:has-text("В корзину")').first
            if await page_button.count() > 0:
                await self.random_delay()
                await page_button.click()
                print("  Товар добавлен в корзину со страницы товара!")
                await self.random_delay()
                return True

        except Exception as e:
            print(f"  Ошибка добавления в корзину: {e}")

        return False

    async def run(self, shopping_list: list[str]):
        """
        Основной метод обработки списка покупок

        Args:
            shopping_list: список товаров для покупки
        """
        print("=" * 60)
        print("АВТОМАТИЗАЦИЯ ПОКУПОК НА OZON FRESH")
        print("=" * 60)
        print(f"Список покупок: {shopping_list}")

        # Создаём директорию для данных браузера если её нет
        os.makedirs(Config.USER_DATA_DIR, exist_ok=True)

        async with async_playwright() as p:
            # Запускаем браузер с persistent context для сохранения сессии
            print("\nЗапускаем браузер...")
            print(f"  Данные сессии сохраняются в: {os.path.abspath(Config.USER_DATA_DIR)}")

            self.context = await p.chromium.launch_persistent_context(
                user_data_dir=Config.USER_DATA_DIR,
                headless=Config.HEADLESS,
                slow_mo=100,
                viewport={'width': 1920, 'height': 1080},
                locale='ru-RU',
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ]
            )

            # Получаем страницу из контекста
            if self.context.pages:
                self.page = self.context.pages[0]
            else:
                self.page = await self.context.new_page()

            try:
                # 1. Открываем Ozon
                print(f"\nОткрываем {Config.OZON_URL}...")
                await self.page.goto(Config.OZON_URL)
                await self.wait_for_page_load()
                print("  Страница загружена")

                # 2. Проверяем авторизацию
                if Config.WAIT_FOR_LOGIN:
                    is_logged_in = await self.check_if_logged_in()
                    if not is_logged_in:
                        await self.wait_for_manual_login()
                    else:
                        print("  Вы уже авторизованы!")

                # 3. Переходим в Ozon Fresh
                await self.navigate_to_ozon_fresh()

                # 3. Обрабатываем каждый товар из списка
                for item in shopping_list:
                    print("\n" + "=" * 60)
                    print(f"ОБРАБОТКА: {item}")
                    print("=" * 60)

                    # Поиск товара
                    await self.search_product(item)

                    # Парсинг результатов
                    products = await self.parse_products()

                    if not products:
                        print(f"\nТовары не найдены для: {item}")
                        continue

                    print(f"\nНайдено товаров: {len(products)}")

                    # Фильтрация по доставке
                    filtered = self.filter_by_delivery(products)
                    print(f"С доставкой сегодня/завтра: {len(filtered)}")

                    # Если нет товаров с быстрой доставкой, берем все
                    if not filtered:
                        print("Нет товаров с доставкой сегодня/завтра, используем все найденные")
                        filtered = products

                    # Выбор самого дешевого
                    cheapest = self.find_cheapest(filtered)

                    if cheapest:
                        print(f"\nВыбран самый дешевый: {cheapest.name[:50]}... - {cheapest.price}₽")

                        # Добавляем в корзину
                        success = await self.add_to_cart(cheapest)

                        if not success:
                            print("Не удалось добавить товар в корзину")
                    else:
                        print(f"Не найдено подходящих товаров для: {item}")

                    # Возвращаемся для поиска следующего товара
                    if shopping_list.index(item) < len(shopping_list) - 1:
                        await self.navigate_to_ozon_fresh()

                print("\n" + "=" * 60)
                print("ГОТОВО! Все товары обработаны.")
                print("=" * 60)

                # Даем время посмотреть результат
                print("\nБраузер останется открытым на 30 секунд...")
                print("(Сессия сохранена - при следующем запуске авторизация сохранится)")
                await asyncio.sleep(30)

            except Exception as e:
                print(f"\nКритическая ошибка: {e}")
                raise
            finally:
                await self.context.close()
                print("\nБраузер закрыт")


# ============ ТОЧКА ВХОДА ============

async def main():
    """Главная функция запуска"""
    automation = OzonAutomation()
    await automation.run(pokupki)


if __name__ == "__main__":
    asyncio.run(main())
