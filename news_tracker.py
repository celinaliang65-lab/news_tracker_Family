import requests
import glob
import os
import json
import pandas as pd
import shioaji as sj
import time
import email.utils
from datetime import datetime, timedelta, timezone
from linebot import LineBotApi
from linebot.models import TextSendMessage, FlexSendMessage
import xml.etree.ElementTree as ET
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

LINE_ACCESS_TOKEN = os.environ.get('LINE_ACCESS_TOKEN', '').strip()
LINE_USER_ID = os.environ.get('LINE_USER_ID', '').strip()
SINOPAC_API_KEY = os.environ.get('SINOPAC_API_KEY', '').strip()
SINOPAC_SECRET_KEY = os.environ.get('SINOPAC_SECRET_KEY', '').strip()
GOOGLE_CREDENTIALS = os.environ.get('GOOGLE_CREDENTIALS', '').strip()
SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID', '').strip()


def get_gsheet_service():
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        return build("sheets", "v4", credentials=creds).spreadsheets()
    except Exception as e:
        print("gsheet service failed: " + str(e))
        return None

def read_sheet_as_df(service, sheet_name, id_col):
    result = service.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=sheet_name
    ).execute()
    rows = result.get("values", [])
    if not rows:
        return pd.DataFrame()
    headers = rows[0]
    data = rows[1:]
    data = [r + [""] * (len(headers) - len(r)) for r in data]
    df = pd.DataFrame(data, columns=headers)
    df = df[df[id_col].str.strip() != ""]
    return df

EXCLUDE_WORDS = ["爆料", "同學會", "達人", "無腦", "學堂", "康和", "券商分點", "存股"]
THIN_LINE = "─────────────"

def load_dividend_table():
    try:
        import re
        now_tw = datetime.now(timezone(timedelta(hours=8)))
        today = now_tw.strftime("%Y%m%d")
        end = (now_tw + timedelta(days=180)).strftime("%Y%m%d")
        url = "https://www.twse.com.tw/rwd/zh/exRight/TWT48U?response=json&strDate=" + today + "&endDate=" + end
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        rows = r.json().get("data", [])
        result = {}
        for row in rows:
            sid = str(row[1]).strip()
            ex_date_str = str(row[0]).strip()
            cash_raw = str(row[7]).strip()
            m = re.match(r"(\d+)年(\d+)月(\d+)日", ex_date_str)
            if not m:
                continue
            year = int(m.group(1)) + 1911
            ex_date = str(year) + "/" + m.group(2) + "/" + m.group(3)
            if "<p" in cash_raw or not cash_raw:
                cash = "待公告"
            else:
                try:
                    cash_val = float(cash_raw)
                    cash = "{:.2f}".format(cash_val) if cash_val > 0 else "待公告"
                except:
                    cash = "待公告"
            result[sid] = (ex_date, cash)
        print("dividend table loaded: " + str(len(result)) + " stocks")
        return result
    except Exception as e:
        print("dividend table failed: " + str(e))
        return {}

def get_last_trading_day():
    now_tw = datetime.now(timezone(timedelta(hours=8)))
    weekday = now_tw.weekday()
    if weekday == 5:
        now_tw -= timedelta(days=1)
    elif weekday == 6:
        now_tw -= timedelta(days=2)
    return now_tw

def get_price_twse(sid):
    try:
        last_day = get_last_trading_day()
        date_str = last_day.strftime("%Y%m%d")
        url = "https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=" + date_str + "&stockNo=" + sid
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        rows = data.get("data", [])
        if not rows:
            return None, None
        last = rows[-1]
        p = float(str(last[6]).replace(",", ""))
        chg = float(str(last[6]).replace(",", "")) - float(str(last[5]).replace(",", ""))
        return p, chg
    except Exception as e:
        print("TWSE failed " + sid + ": " + str(e))
        return None, None

def get_price_tpex(sid):
    try:
        last_day = get_last_trading_day()
        roc_year = last_day.year - 1911
        ym = str(roc_year) + "/" + last_day.strftime("%m")
        url = "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php?l=zh-tw&d=" + ym + "&s=" + sid + "&o=json"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        rows = data.get("aaData", [])
        if not rows:
            return None, None
        last = rows[-1]
        p = float(str(last[6]).replace(",", ""))
        prev = float(str(last[5]).replace(",", "")) if last[5] != "--" else p
        chg = round(p - prev, 2)
        return p, chg
    except Exception as e:
        print("TPEX failed " + sid + ": " + str(e))
        return None, None

def get_price_shioaji(api, sid):
    try:
        try:
            c = api.Contracts.Stocks[sid]
        except:
            c = api.Contracts.Stocks.OTC[sid]
        if c is None:
            return None, None
        snap = api.snapshots([c])[0]
        p = float(snap.close) if snap.close else None
        try:
            chg = float(snap.change_price)
        except:
            chg = None
        return p, chg
    except Exception as e:
        print("shioaji failed " + sid + ": " + str(e))
        return None, None

def get_price(api, sid, exchange):
    p, chg = get_price_shioaji(api, sid)
    if p and p > 0:
        return p, chg
    if exchange == "OTC":
        p, chg = get_price_tpex(sid)
    else:
        p, chg = get_price_twse(sid)
    return p, chg

def get_news(stock_name, stock_id, max_news=2):
    try:
        query = stock_name + " " + stock_id
        url = "https://news.google.com/rss/search?q=" + query + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        root = ET.fromstring(r.content)
        items = root.findall(".//item")
        news_list = []
        now_tw = datetime.now(timezone(timedelta(hours=8)))
        for item in items:
            if len(news_list) >= max_news:
                break
            try:
                pub_date_str = item.find("pubDate").text
                pub_date = email.utils.parsedate_to_datetime(pub_date_str)
                age_hours = (now_tw - pub_date.astimezone(timezone(timedelta(hours=8)))).total_seconds() / 3600
                if age_hours > 48:
                    continue
            except:
                continue
            title = item.find("title").text
            if not title:
                continue
            if " - " in title:
                title = title.rsplit(" - ", 1)[0]
            title = title.strip()
            if any(word in title for word in EXCLUDE_WORDS):
                continue
            if len(title) > 25:
                title = title[:25] + "..."
            news_list.append(title)
        return news_list
    except Exception as e:
        print("news failed " + stock_id + ": " + str(e))
        return []

RANK_EMOJI = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩",
              "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳",
              "㉑", "㉒", "㉓", "㉔", "㉕", "㉖", "㉗", "㉘", "㉙", "㉚",
              "㉛", "㉜", "㉝", "㉞", "㉟", "㊱", "㊲", "㊳", "㊴", "㊵",
              "㊶", "㊷", "㊸", "㊹", "㊺", "㊻", "㊼", "㊽", "㊾", "㊿"]

# ── 大盤資訊 ──────────────────────────────────────

def get_market_info(api=None):
    try:
        if api:
            try:
                c = api.Contracts.Indices.TSE["TAIEX"]
                snap = api.snapshots([c])[0]
                close = float(snap.close) if snap.close else None
                chg = float(snap.change_price) if snap.change_price else None
                chg_pct = float(snap.change_rate) if snap.change_rate else None
                if close and close > 0:
                    print(f"market from shioaji: {close} {chg} {chg_pct}")
                    return close, chg, chg_pct
            except Exception as e:
                print(f"shioaji market failed: {e}")
        url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        msg = data.get("msgArray", [])
        if msg:
            item = msg[0]
            close_raw = str(item.get("z", "")).strip()
            prev_raw  = str(item.get("y", "")).strip()
            if close_raw and close_raw != "-" and prev_raw and prev_raw != "-":
                close = float(close_raw)
                prev  = float(prev_raw)
                chg   = round(close - prev, 2)
                chg_pct = round(chg / prev * 100, 2) if prev else 0
                print(f"market from twse mis: {close} {chg} {chg_pct}")
                return close, chg, chg_pct
        return None, None, None
    except Exception as e:
        print(f"market info failed: {e}")
        return None, None, None

def get_market_news(max_news=2):
    try:
        url = "https://news.google.com/rss/search?q=台股+大盤+今日&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        root = ET.fromstring(r.content)
        items = root.findall(".//item")
        news_list = []
        now_tw = datetime.now(timezone(timedelta(hours=8)))
        for item in items:
            if len(news_list) >= max_news:
                break
            try:
                pub_date_str = item.find("pubDate").text
                pub_date = email.utils.parsedate_to_datetime(pub_date_str)
                age_hours = (now_tw - pub_date.astimezone(timezone(timedelta(hours=8)))).total_seconds() / 3600
                if age_hours > 48:
                    continue
            except:
                continue
            title = item.find("title").text
            if not title:
                continue
            if " - " in title:
                title = title.rsplit(" - ", 1)[0]
            title = title.strip()
            if len(title) > 40:
                title = title[:40] + "..."
            news_list.append(title)
        return news_list
    except Exception as e:
        print(f"market news failed: {e}")
        return []

# ── Flex Message 合併排行卡片（勇敢灰褐） ─────────────────

def build_flex_leaderboard(hold_growth_list, watch_growth_list, now_tw, market_close=None, market_chg=None, market_chg_pct=None, market_news=None):
    COLOR_HEADER  = "#9e8c7a"
    COLOR_TITLE   = "#f5ede6"
    COLOR_SUB     = "#d4c4b8"
    COLOR_PCT_POS = "#6b4e38"
    COLOR_PCT_NEG = "#8b6355"
    COLOR_RANK    = "#aaaaaa"
    COLOR_NAME    = "#333333"
    COLOR_SECT    = "#9e8c7a"
    COLOR_FOOT    = "#b8a898"
    COLOR_DIV     = "#ede4dc"

    def make_rows(growth_list):
        rows = []
        for i, item in enumerate(growth_list):
            sign_str = "+" if item["growth"] >= 0 else "-"
            abs_pct = "{:.2f}".format(abs(item["growth"]))
            pct_text = sign_str + abs_pct + "%"
            pct_color = COLOR_PCT_POS if item["growth"] >= 0 else COLOR_PCT_NEG
            rank_str = RANK_EMOJI[i] if i < len(RANK_EMOJI) else str(i + 1)
            rows.append({
                "type": "box",
                "layout": "horizontal",
                "contents": [
                    {"type": "text", "text": rank_str, "size": "sm", "color": COLOR_RANK, "flex": 1},
                    {"type": "text", "text": pct_text, "size": "sm", "color": pct_color, "flex": 3, "weight": "bold"},
                    {"type": "text", "text": item["sid"] + " " + item["name"], "size": "sm", "color": COLOR_NAME, "flex": 5},
                ],
                "paddingTop": "3px",
                "paddingBottom": "3px",
            })
        return rows

    hold_rows = make_rows(hold_growth_list)
    watch_rows = make_rows(watch_growth_list)
    date_str = now_tw[:10]
    time_str = now_tw[11:] if len(now_tw) > 10 else ""

    return {
        "type": "bubble",
        "size": "giga",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": COLOR_HEADER,
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": "📊 漲幅排行", "size": "sm", "weight": "bold", "color": COLOR_TITLE, "flex": 0},
                        {"type": "text", "text": date_str, "size": "xs", "color": COLOR_SUB, "align": "end", "flex": 1, "gravity": "center"},
                    ]
                },
                {"type": "text", "text": "自 2/26 起成長幅度分析 (%)", "size": "xs", "color": COLOR_SUB, "margin": "sm"},
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#faf7f4",
            "paddingAll": "12px",
            "spacing": "none",
            "contents": [
                # ── 大盤資訊區塊 ──
                *([
                    {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#f5ede6",
                        "cornerRadius": "8px",
                        "paddingAll": "8px",
                        "margin": "none",
                        "contents": [
                            {
                                "type": "text",
                                "text": "📈 加權指數 {:.2f}".format(market_close),
                                "size": "xs",
                                "weight": "bold",
                                "color": "#6b4e38",
                            },
                            {
                                "type": "text",
                                "text": "{} {:+.2f}（{:+.2f}%）".format(
                                    "🔺" if market_chg >= 0 else "🔻",
                                    market_chg, market_chg_pct
                                ),
                                "size": "xs",
                                "color": "#d93025" if market_chg >= 0 else "#1a73e8",
                                "margin": "xs",
                            },
                            *([{"type": "text", "text": "・" + n, "size": "xxs", "color": "#8b6355", "margin": "xs", "wrap": True} for n in (market_news or [])]),
                        ]
                    },
                    {"type": "separator", "margin": "sm", "color": COLOR_DIV},
                ] if market_close is not None else []),
                {"type": "text", "text": "💼 持有股票", "size": "xs", "weight": "bold", "color": COLOR_SECT, "paddingBottom": "4px"},
                *hold_rows,
                {"type": "separator", "margin": "sm", "color": COLOR_DIV},
                {"type": "text", "text": "🔍 觀察清單", "size": "xs", "weight": "bold", "color": COLOR_SECT, "margin": "sm", "paddingBottom": "4px"},
                *watch_rows,
                {"type": "text", "text": "梁麗晴 · " + time_str, "size": "xxs", "color": COLOR_FOOT, "align": "end", "margin": "sm"},
            ]
        }
    }


def job():
    print("connecting to Google Sheets...")
    service = get_gsheet_service()
    if not service:
        print("Google Sheets connection failed")
        return

    df_hold = read_sheet_as_df(service, "Python直接讀取", "代號")
    df_watch = read_sheet_as_df(service, "觀察清單", "代號")
    print("sheets loaded: hold=" + str(len(df_hold)) + " watch=" + str(len(df_watch)))

    now_tw = datetime.now(timezone(timedelta(hours=8))).strftime("%Y/%m/%d %H:%M")

    api = sj.Shioaji()
    try:
        api.login(api_key=SINOPAC_API_KEY, secret_key=SINOPAC_SECRET_KEY)
        time.sleep(3)
        print("shioaji logged in")
    except Exception as e:
        print("shioaji login failed: " + str(e))
        api = None

    div_table = load_dividend_table()

    # ── 第一步：持有股票抓股價和漲幅 ──
    hold_growth_list = []
    for _, row in df_hold.iterrows():
        sid = str(row['代號']).strip()
        name = str(row['名稱']).strip()
        exchange = str(row.get('交易所', 'TSE')).strip()
        try:
            cost_226 = float(str(row['2026/2/26收盤價']).replace(',', ''))
        except:
            continue
        print("fetching hold price: " + sid)
        p, _ = get_price(api, sid, exchange)
        if p and p > 0 and cost_226 > 0:
            growth = (p - cost_226) / cost_226 * 100
            hold_growth_list.append({"sid": sid, "name": name, "growth": growth})
    hold_growth_list.sort(key=lambda x: x["growth"], reverse=True)

    # ── 第二步：觀察清單抓股價和漲幅 ──
    watch_data = []
    watch_growth_list = []

    for _, row in df_watch.iterrows():
        sid = str(row['代號']).strip()
        name = str(row['名稱']).strip()
        exchange = str(row.get('交易所', 'TSE')).strip()
        cost_226 = float(str(row['2026/2/26收盤價']).replace(',', ''))
        print("fetching watch: " + sid + " (" + exchange + ")")

        p, chg = get_price(api, sid, exchange)
        news_list = get_news(name, sid)
        ex_date, cash = div_table.get(sid, (None, None))

        growth = None
        if p is not None and p > 0 and cost_226 > 0:
            growth = (p - cost_226) / cost_226 * 100
            watch_growth_list.append({"sid": sid, "name": name, "growth": growth})

        watch_data.append({
            "sid": sid, "name": name, "exchange": exchange,
            "cost_226": cost_226, "p": p, "chg": chg,
            "growth": growth, "news_list": news_list,
            "ex_date": ex_date, "cash": cash
        })
    watch_growth_list.sort(key=lambda x: x["growth"], reverse=True)

    # ── 第三步：Flex Message 合併排行推播 ──
    line_api = LineBotApi(LINE_ACCESS_TOKEN)
    market_close, market_chg, market_chg_pct = get_market_info(api)
    market_news = get_market_news()
    flex_body = build_flex_leaderboard(hold_growth_list, watch_growth_list, now_tw, market_close, market_chg, market_chg_pct, market_news)
    flex_msg = FlexSendMessage(alt_text="📊 自 2/26 起成長幅度分析 (%)", contents=flex_body)
    try:
        line_api.push_message(LINE_USER_ID, flex_msg)
        print("leaderboard flex sent!")
    except Exception as e:
        print("LINE flex failed: " + str(e))

    # ── 第四步：純文字報告（完全維持原本） ──
    report = "📰 持股新聞摘要\n      " + now_tw + "\n"
    report += "━━━━━━━━━━━━━\n"

    for _, row in df_hold.iterrows():
        sid = str(row['代號']).strip()
        name = str(row['名稱']).strip()
        print("fetching news: " + sid)
        news_list = get_news(name, sid)
        ex_date, cash = div_table.get(sid, (None, None))
        report += "\n" + sid + " " + name + "\n"
        if ex_date and cash:
            report += "  💰除息：" + ex_date + " 現金股利：" + str(cash) + "元\n"
        report += THIN_LINE + "\n"
        if news_list:
            for news in news_list:
                report += "  ・" + news + "\n"
        else:
            report += "  暫無最新新聞\n"

    report += "\n━━━━━━━━━━━━━\n"
    report += " 📊 觀察清單\n"
    report += "━━━━━━━━━━━━━\n"

    for d in watch_data:
        p = d["p"]
        chg = d["chg"]
        report += "\n" + d["sid"] + " " + d["name"] + "\n"

        if p is not None and p > 0:
            if chg is not None and chg > 0:
                day_str = "🔺 +" + "{:.2f}".format(chg)
            elif chg is not None and chg < 0:
                day_str = "🔽 " + "{:.2f}".format(chg)
            else:
                day_str = "➖ 0.00"
            report += "  現價:" + "{:.2f}".format(p) + "  " + day_str + "\n"

            if d["growth"] is not None:
                growth_sign = "+" if d["growth"] >= 0 else ""
                growth_arrow = "↑" if d["growth"] >= 0 else "↓"
                report += "  2/26:" + "{:.2f}".format(d["cost_226"]) + "  " + growth_arrow + " " + growth_sign + "{:.2f}".format(d["growth"]) + "%\n"
        else:
            report += "  價格取得失敗\n"

        if d["ex_date"] and d["cash"]:
            report += "  💰除息：" + d["ex_date"] + " 現金股利：" + str(d["cash"]) + "元\n"

        report += THIN_LINE + "\n"
        if d["news_list"]:
            for news in d["news_list"]:
                report += "  ・" + news + "\n"
        else:
            report += "  暫無最新新聞\n"

    report += "\n━━━━━━━━━━━━━"

    try:
        line_api.push_message(LINE_USER_ID, TextSendMessage(text=report))
        print("news sent!")
    except Exception as e:
        print("LINE failed: " + str(e))
    finally:
        if api:
            try:
                api.logout()
            except:
                pass

if __name__ == "__main__":
    job()
