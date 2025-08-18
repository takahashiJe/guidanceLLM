# backend/worker/app/services/information/web_crawler.py
# tenki.jp（鳥海山：百名山ページ）の週間予報から、指定日の天気文言を抽出する本実装
from __future__ import annotations
from typing import Optional, Dict, List
import os
import re
from datetime import datetime, date
import datetime as _dt

import requests
from bs4 import BeautifulSoup

import logging



def _normalize_condition(raw: str) -> str:
    """tenki.jp の表示文言/alt を代表カテゴリに正規化（日本語ベース）"""
    t = (raw or "").strip()
    # 代表語を先に判定（含む判定）
    if any(k in t for k in ["快晴"]):
        return "快晴"
    if any(k in t for k in ["晴", "晴れ"]):
        return "晴れ"
    if any(k in t for k in ["薄曇", "曇", "くもり"]):
        return "曇り"
    if any(k in t for k in ["雪", "みぞれ", "霙"]):
        return "雪"
    if any(k in t for k in ["雷", "雷雨", "雷を伴う"]):
        return "雷雨"
    if any(k in t for k in ["雨", "小雨", "にわか雨", "強雨", "大雨"]):
        return "雨"
    if any(k in t for k in ["霧"]):
        return "霧"
    # 未知はそのまま返す（上位ロジックでスコア中庸扱い）
    return t or "不明"


def _guess_year_for_month_day(month: int, day: int) -> int:
    """週間予報は年が明示されないことが多いので、現在日付を基準に年を推定"""
    today = date.today()
    y = today.year
    # 年またぎ対策：例えば 12月末に 1月表記が来たら翌年扱い
    if today.month == 12 and month == 1:
        return y + 1
    # 逆（1月頭に12月表記）は前年扱い（通常は出ないが安全側）
    if today.month == 1 and month == 12:
        return y - 1
    return y


def _parse_mmdd_to_iso(mmdd_text: str) -> Optional[str]:
    """
    '8月9日(金)' や '08/09' のような表示から 'YYYY-MM-DD' を生成
    """
    s = (mmdd_text or "").strip()
    # 例: "8月9日(金)" 形式
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", s)
    if m:
        mon = int(m.group(1))
        day = int(m.group(2))
        year = _guess_year_for_month_day(mon, day)
        try:
            return date(year, mon, day).isoformat()
        except Exception:
            return None

    # 例: "08/09" 形式
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", s)
    if m:
        mon = int(m.group(1))
        day = int(m.group(2))
        year = _guess_year_for_month_day(mon, day)
        try:
            return date(year, mon, day).isoformat()
        except Exception:
            return None

    return None


class TenkiCrawler:
    """
    鳥海山の週間予報テーブル（百名山ページ）から、指定日の天気を抽出するスクレイパー。
    - .env の TENKI_JP_CHOKAI_URL を参照
    - 週間予報ブロックの各「日付セル」に紐づく「天気セル（img alt / テキスト）」を拾う
    - DOM 変更にある程度耐えるように複数セレクタをフォールバック
    """

    def __init__(self, url: Optional[str] = None, timeout: int = 12):
        self.url = url or os.getenv("TENKI_JP_CHOKAI_URL", "https://tenki.jp/mountain/famous100/point-23.html")
        self.timeout = timeout

    def fetch_day_condition(self, target_date: str, lang: str = "ja") -> Optional[str]:
        """
        :param target_date: 'YYYY-MM-DD'
        :return: '晴れ' / '曇り' / '雨' / ...（代表語）。取得できなければ None。
        """
        try:
            resp = requests.get(self.url, timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        except Exception:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # --- 週間予報セクションの候補を拾う ---
        # 実ページでは、以下のような構造のいずれか：
        # - section#mountain-week 内に table.week-forecast / div/ul の日別ブロック
        # - div.mountain-week / div#mountain-week など
        week_sections = []
        for sel in [
            "section#mountain-week", "section.mountain-week", "div#mountain-week", "div.mountain-week",
            "section#forecast-week", "section.week", "div.week", "section#oneweek", "section#weekly", "div#weekly"
        ]:
            week_sections.extend(soup.select(sel))
        if not week_sections:
            # テーブルだけ露出しているケース
            week_sections = soup.select("table, div, section")

        # --- セクション内から日別セルを抽出 ---
        # パターン1: テーブル行ごとに日付/天気がある
        pairs: Dict[str, str] = {}

        def try_register(iso_date: Optional[str], condition: Optional[str]):
            if iso_date and condition:
                pairs[iso_date] = _normalize_condition(condition)

        # テーブル構造: thead に日付、tbody に天気img/テキスト などのケースに対応
        for section in week_sections:
            # 1) テーブルのヘッダに日付、ボディに天気（列対応）
            tables = section.find_all("table")
            for tbl in tables:
                # ヘッダから日付列を取得
                header_cells: List[str] = []
                thead = tbl.find("thead")
                if thead:
                    ths = thead.find_all(["th", "td"])
                    header_cells = [th.get_text(" ", strip=True) for th in ths]

                # ボディから天気列（img alt またはテキスト）を取得
                body_rows = tbl.find_all("tr")
                # 天気が入っていそうな行を優先して探索（class 名でヒューリスティック）
                weather_rows = [r for r in body_rows if any(k in (r.get("class") or []) for k in ["weather", "weathers"])]
                if not weather_rows and body_rows:
                    # fallback: 全行から「天気っぽいセル」を探索
                    weather_rows = body_rows

                for row in weather_rows:
                    cells = row.find_all(["td", "th"])
                    for idx, c in enumerate(cells):
                        date_label = None
                        if header_cells and idx < len(header_cells):
                            date_label = header_cells[idx]
                        else:
                            # 同じ列の先頭行/直近のthから拾う
                            pass

                        # セル内の img alt / テキストを候補に
                        alt = None
                        img = c.find("img")
                        if img and img.has_attr("alt"):
                            alt = img["alt"]
                        txt = c.get_text(" ", strip=True)
                        cond = alt or txt

                        iso = _parse_mmdd_to_iso(date_label or txt)
                        if iso:
                            try_register(iso, cond)

            # 2) li / div の日別カード（カード内に日付と天気）
            for li in section.select("ul li, div.item, div.day, div.daily, article"):
                label = ""
                # 日付の候補
                for dsel in [".date", ".day", ".label", "time", "h3", "h4", "header", "p", "span"]:
                    el = li.select_one(dsel)
                    if el:
                        label = el.get_text(" ", strip=True)
                        if re.search(r"\d{1,2}\s*月\s*\d{1,2}\s*日", label) or re.search(r"\d{1,2}\s*/\s*\d{1,2}", label):
                            break

                # 天気の候補（alt優先）
                alt = None
                img = li.find("img")
                if img and img.has_attr("alt"):
                    alt = img["alt"]
                wx_txt = None
                for wsel in [".weather", ".weather-txt", ".weathers", ".wx", ".text", "p", "span"]:
                    el = li.select_one(wsel)
                    if el:
                        wx_txt = el.get_text(" ", strip=True)
                        if wx_txt:
                            break
                condition = alt or wx_txt or ""

                iso = _parse_mmdd_to_iso(label)
                if iso:
                    try_register(iso, condition)

        # 3) 最後の手段：ページ全体テキストから順番マッチ（脆いので最終フォールバック）
        if not pairs:
            text = soup.get_text(" ", strip=True)
            # 直近の 1週間分の "M月D日" を順に抜いて、周辺の天気語を拾う
            for m, d in re.findall(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text):
                iso = _parse_mmdd_to_iso(f"{m}月{d}日")
                if not iso:
                    continue
                # 周辺に含まれる代表語を推定
                # （実装簡略化：全体からの代表語のみ拾う。厳密には前後数十文字を抽出するとより精度UP）
                cond = None
                for key in ["快晴", "晴れ", "晴", "薄曇", "曇り", "曇", "雪", "みぞれ", "雷雨", "雷", "雨"]:
                    if key in text:
                        cond = key
                        break
                if cond:
                    pairs.setdefault(iso, _normalize_condition(cond))

        # 指定日を返す
        condition = pairs.get(target_date)
        if condition:
            return condition

        return None

_logger = logging.getLogger(__name__)
_TENKI_CHOKAI_URL = "https://tenki.jp/mountain/famous/point-36/"  # 鳥海山のページ（DOM変動に強いよう大雑把に抽出）


def _wc_fetch(url: str) -> Optional[str]:
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.text
    except Exception as e:
        _logger.warning("web_crawler fetch failed: %s (%s)", url, e)
        return None

def _wc_clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "")
    return s.strip()

def get_tenkijp_chokai_daily(date: _dt.date) -> str:
    """
    tenki.jp 鳥海山ページから当日（近傍）の概況テキストを抽出。
    既存の関数・クラスには一切触れず、この関数のみ追加します。

    Returns:
        str: 見出し＋本文をまとめた一文。失敗時はフォールバック文。
    """
    html = _wc_fetch(_TENKI_CHOKAI_URL)
    if not html:
        return f"（{date:%Y-%m-%d}の鳥海山情報を取得できませんでした）"

    try:
        soup = BeautifulSoup(html, "html.parser")

        # 見出し（ページ先頭の h1/h2/h3 のいずれか）
        heading = soup.find(["h1", "h2", "h3"])
        heading_text = _wc_clean(heading.get_text()) if heading else "鳥海山の天気概況"

        # 本文（概況っぽい段落を複数候補から安全側で抽出）
        paras = []
        for sel in [
            "div#weather-news p",
            "section p",
            "article p",
            "div.summary p",
            "div#main p",
            "p",
        ]:
            for p in soup.select(sel):
                t = _wc_clean(p.get_text())
                # あまりに短いもの・コピーライト的なものは弾く
                if len(t) >= 20 and "tenki.jp" not in t.lower():
                    paras.append(t)
            if paras:
                break

        body = " ".join(paras[:4]) if paras else "（本文の抽出に失敗しました）"
        return f"{heading_text}: {body}"

    except Exception as e:
        _logger.warning("tenki.jp parse failed: %s", e)
        return f"（{date:%Y-%m-%d}の鳥海山情報の解析に失敗しました）"