"""
Модуль для работы с LLM провайдерами (Claude, GigaChat)
"""

import os
import time
import uuid
from abc import ABC, abstractmethod
from typing import Optional

import requests
from anthropic import Anthropic


class LLMClient(ABC):
    """Абстрактный базовый класс для LLM клиентов"""

    @abstractmethod
    def generate(self, system_prompt: str, user_message: str) -> str:
        """Генерирует ответ на основе системного промпта и сообщения пользователя"""
        pass


class ClaudeClient(LLMClient):
    """Клиент для Claude API"""

    def __init__(self, api_key: str):
        self.client = Anthropic(api_key=api_key)
        self.model = "claude-sonnet-4-20250514"

    def generate(self, system_prompt: str, user_message: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_message}
            ]
        )
        return response.content[0].text


class GigaChatClient(LLMClient):
    """Клиент для GigaChat API"""

    AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    API_URL = "https://gigachat.devices.sberbank.ru/api/v1"

    def __init__(self, auth_key: str):
        self.auth_key = auth_key
        self.access_token: Optional[str] = None
        self.token_expires_at: float = 0
        self.model = "GigaChat-2-Max"

    def _get_access_token(self) -> str:
        """Получает access token (кэширует на 25 минут)"""
        # Проверяем, не истёк ли токен (с запасом в 5 минут)
        if self.access_token and time.time() < self.token_expires_at - 300:
            return self.access_token

        # Запрашиваем новый токен
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
            'RqUID': str(uuid.uuid4()),
            'Authorization': f'Basic {self.auth_key}'
        }
        payload = {'scope': 'GIGACHAT_API_PERS'}

        response = requests.post(
            self.AUTH_URL,
            headers=headers,
            data=payload,
            verify=False  # Сбер использует свой сертификат
        )
        response.raise_for_status()

        data = response.json()
        self.access_token = data['access_token']
        # Токен действует 30 минут
        self.token_expires_at = time.time() + 30 * 60

        return self.access_token

    def generate(self, system_prompt: str, user_message: str) -> str:
        """Генерирует ответ через GigaChat API"""
        token = self._get_access_token()

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {token}'
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.7,
            "max_tokens": 1024
        }

        response = requests.post(
            f"{self.API_URL}/chat/completions",
            headers=headers,
            json=payload,
            verify=False
        )
        response.raise_for_status()

        data = response.json()
        return data['choices'][0]['message']['content']


def create_llm_client(provider: str = None) -> LLMClient:
    """
    Фабричный метод для создания LLM клиента

    Args:
        provider: "claude" или "gigachat". Если None - берётся из LLM_PROVIDER

    Returns:
        Экземпляр LLMClient
    """
    if provider is None:
        provider = os.getenv("LLM_PROVIDER", "claude").lower()

    if provider == "claude":
        api_key = os.getenv("CLAUDE_API_KEY")
        if not api_key:
            raise ValueError("CLAUDE_API_KEY не установлен в .env")
        return ClaudeClient(api_key)

    elif provider == "gigachat":
        auth_key = os.getenv("GIGACHAT_AUTH_KEY")
        if not auth_key:
            raise ValueError("GIGACHAT_AUTH_KEY не установлен в .env")
        return GigaChatClient(auth_key)

    else:
        raise ValueError(f"Неизвестный провайдер: {provider}. Используйте 'claude' или 'gigachat'")
