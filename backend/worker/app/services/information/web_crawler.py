# -*- coding: utf-8 -*-
"""
tenki.jp 鳥海山ページの山域天気をクローリングして、指定日の概況を抽出する。
- 入力: date_str="YYYY-MM-DD"
- 出力: {"date": "YYYY-MM-DD", "summary": "晴れ/曇り/雨", "source": "crawler", "note": None}

実装ノート:
- tenki.jp の構造は変更されることがあるため、複数の探索戦略を組み合わせる。
- ベストエフォートで「当日/指定日」の文字列とアイコン/テキストから天気語を推定。
- 失敗した場合は CrawlError を投げ、呼び出し側で API フォールバックへ。
"""

from __future__ import annotations
import os
import re
from typing import Dict, Optional
from datetime import datetime

import httpx
from bs4 import BeautifulSoup


class CrawlError(Exception):
    pass


# .env から鳥海山の山域天気ページURLを与える（例）
# TENKI_JP_CHOKAI_URL="https://tenki.jp/mountain/famous100/point-23.html"
TENKI_JP_CHOKAI_URL = os.getenv("TENKI_JP_CHOKAI_URL", "").strip()


def get_mountain_weather_chokai(date_str: str) -> Dict:
    if not TENKI_JP_CHOKAI_URL:
        raise CrawlError("TENKI_JP_CHOKAI_URL が設定されていません")

    # リクエスト
    timeout = httpx.Timeout(connect=5.0, read=8.0)
    headers = {
        "User-Agent": "guidanceLLM-crawler/1.0 (+https://example.com)",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }
    try:
        with httpx.Client(timeout=timeout, headers=headers) as client:
            resp = client.get(TENKI_JP_CHOKAI_URL)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        raise CrawlError(f"HTTPエラー: {e}")

    soup = BeautifulSoup(html, "html.parser")

    # 1) data-date 属性や日付見出しを持つブロックを優先的に探索
    normalized = _find_weather_label_by_date_block(soup, date_str)
    if not normalized:
        # 2) テーブル/リストから「今日/明日/◯日」の見出しを頼りに抽出
        normalized = _fallback_find_by_text_patterns(soup, date_str)

    if not normalized:
        raise CrawlError("対象日の山域天気が抽出できませんでした")

    return {"date": date_str, "summary": normalized, "source": "crawler", "note": None}


# ---------------- 内部ユーティリティ ---------------- #

def _normalize_to_sunny_cloudy_rain(text: str) -> Optional[str]:
    """
    アイコンの alt やテキストから 3値（晴れ/曇り/雨）を推定
    """
    t = text.strip()
    if not t:
        return None

    # 代表語
    if re.search(r"(快晴|晴|晴れ)", t):
        return "晴れ"
    if re.search(r"(曇|くもり|雲)", t):
        return "曇り"
    if re.search(r"(雨|雷|雪|みぞれ|霙|氷|吹雪|にわか|強雨)", t):
        return "雨"
    # 他は保守的に「曇り」
    return "曇り"


def _find_weather_label_by_date_block(soup: BeautifulSoup, date_str: str) -> Optional[str]:
    """
    data-date="YYYY-MM-DD" を持つ要素や、日付テキストに一致するブロックを見て
    近傍のアイコン/文言から天気を抽出
    """
    # 直接マッチ
    date_nodes = soup.select(f'[data-date="{date_str}"]')
    for node in date_nodes:
        # 近辺の img[alt], span, p などからテキストを拾う
        label = _extract_label_near(node)
        if label:
            return label

    # 日付テキストで探索（例: 2025年8月12日 / 08月12日 など）
    try:
        d = datetime.fromisoformat(date_str)
        patterns = [
            f"{d.year}年{d.month}月{d.day}日",
            f"{d.month}月{d.day}日",
            d.strftime("%m/%d"),
        ]
    except Exception:
        patterns = [date_str]

    text_nodes = []
    for pat in patterns:
        text_nodes.extend(soup.find_all(string=re.compile(re.escape(pat))))

    for t in text_nodes:
        label = _extract_label_near(t.parent if hasattr(t, "parent") else None)
        if label:
            return label

    return None


def _extract_label_near(node) -> Optional[str]:
    """
    任意のノード近傍から天気ラベルを抽出。
    - 先に img[alt] を優先
    - 次に同胞/親のテキスト
    """
    if not node:
        return None

    # 1) 直下/近傍のアイコンALT
    for img in node.find_all("img"):
        if img.has_attr("alt"):
            lab = _normalize_to_sunny_cloudy_rain(img["alt"])
            if lab:
                return lab

    # 2) 同階層のテキスト
    combined = " ".join(node.get_text(" ", strip=True)[:200].split())
    lab = _normalize_to_sunny_cloudy_rain(combined)
    if lab:
        return lab

    # 3) 親側も軽く探索
    parent = node.parent
    if parent:
        combined = " ".join(parent.get_text(" ", strip=True)[:200].split())
        lab = _normalize_to_sunny_cloudy_rain(combined)
        if lab:
            return lab

    return None


def _fallback_find_by_text_patterns(soup: BeautifulSoup, date_str: str) -> Optional[str]:
    """
    セクションの見出し（例: 今日/明日/◯日）とリストを総当りで確認する。
    ここでも img[alt] / テキスト一致で推定。
    """
    # よくある構造: li > img[alt] + span などを総当り
    for li in soup.select("li"):
        # アイコン優先
        img = li.find("img", alt=True)
        if img and img.get("alt"):
            lab = _normalize_to_sunny_cloudy_rain(img["alt"])
            if lab:
                return lab
        text = li.get_text(" ", strip=True)
        lab = _normalize_to_sunny_cloudy_rain(text)
        if lab:
            return lab

    # テーブル構造の場合
    for tr in soup.select("table tr"):
        text = tr.get_text(" ", strip=True)
        lab = _normalize_to_sunny_cloudy_rain(text)
        if lab:
            return lab

    # 見つからず
    return None
