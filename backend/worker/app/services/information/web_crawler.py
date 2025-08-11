# worker/app/services/information/web_crawler.py

import requests
from bs4 import BeautifulSoup, Tag
from typing import Optional, Dict
from datetime import date
import logging

logger = logging.getLogger(__name__)

def fetch_chokai_weather_from_tenkijp(target_date: date) -> Optional[Dict[str, str]]:
    """
    tenki.jpの鳥海山ページから指定日の天気を取得する。
    URL: https://tenki.jp/mountain/famous100/2/9/115.html
    """
    URL = "https://tenki.jp/mountain/famous100/2/9/115.html"
    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        weekly_forecast_section = soup.find('div', class_='weather-day-wrap')
        if not weekly_forecast_section:
            logger.warning("Could not find 'weather-day-wrap' section on tenki.jp.")
            return None

        days = weekly_forecast_section.find_all('div', class_='weather-day-item')
        target_date_str_md = target_date.strftime("%m/%d")

        for day in days:
            date_element = day.find('p', class_='date')
            if date_element and isinstance(date_element, Tag) and target_date_str_md in date_element.text:
                weather_img = day.find('img')
                weather_text = weather_img['alt'] if weather_img and isinstance(weather_img, Tag) else "情報なし"
                
                high_temp_element = day.find('p', class_='high-temp')
                low_temp_element = day.find('p', class_='low-temp')
                max_temp = high_temp_element.text.strip() if high_temp_element and isinstance(high_temp_element, Tag) else "-"
                min_temp = low_temp_element.text.strip() if low_temp_element and isinstance(low_temp_element, Tag) else "-"

                return {
                    "weather": weather_text,
                    "max_temp": f"{max_temp}℃",
                    "min_temp": f"{min_temp}℃",
                    "source": "tenki.jp"
                }
        
        logger.info(f"Weather forecast for {target_date_str_md} not found on tenki.jp.")
        return None

    except requests.RequestException as e:
        # ネットワークエラーやタイムアウトを捕捉
        logger.error(f"Error fetching data from tenki.jp: {e}", exc_info=True)
        return None
    except Exception as e:
        # HTML構造の変更など、予期せぬパースエラーを捕捉
        logger.error(f"An unexpected error occurred while parsing tenki.jp: {e}", exc_info=True)
        return None
