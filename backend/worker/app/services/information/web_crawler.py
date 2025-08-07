# worker/app/services/information/web_crawler.py

import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict
from datetime import datetime

def fetch_chokai_weather_from_tenkijp(target_date: datetime.date) -> Optional[Dict[str, str]]:
    """
    tenki.jpの鳥海山ページから指定日の天気を取得する。
    URL: https://tenki.jp/mountain/famous100/2/9/115.html

    Args:
        target_date (datetime.date): 予報を取得したい日付オブジェクト。

    Returns:
        Optional[Dict[str, str]]: 天気情報。例: {"weather": "晴れ", "max_temp": "15℃", "min_temp": "7℃"}
    """
    URL = "https://tenki.jp/mountain/famous100/2/9/115.html"
    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # 'weather-day-wrap' クラスを持つ週間天気予報のセクションを探す
        weekly_forecast_section = soup.find('div', class_='weather-day-wrap')
        if not weekly_forecast_section:
            return None

        # 日付ごとの情報を取得
        days = weekly_forecast_section.find_all('div', class_='weather-day-item')
        target_date_str_md = target_date.strftime("%m/%d") # "MM/DD"形式

        for day in days:
            date_element = day.find('p', class_='date')
            if date_element and target_date_str_md in date_element.text:
                # 天気アイコンのaltテキストから天気情報を取得
                weather_img = day.find('img')
                weather_text = weather_img['alt'] if weather_img else "情報なし"
                
                # 気温情報を取得
                high_temp_element = day.find('p', class_='high-temp')
                low_temp_element = day.find('p', class_='low-temp')
                max_temp = high_temp_element.text.strip() if high_temp_element else "-"
                min_temp = low_temp_element.text.strip() if low_temp_element else "-"

                return {
                    "weather": weather_text,
                    "max_temp": f"{max_temp}℃",
                    "min_temp": f"{min_temp}℃",
                    "source": "tenki.jp"
                }
        
        # 指定日の情報が見つからなかった場合
        return None

    except requests.RequestException as e:
        print(f"Error fetching data from tenki.jp: {e}")
        return None