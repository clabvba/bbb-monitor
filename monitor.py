import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# 读取产品类型配置文件
def load_product_types():
    with open("product_types.json", "r", encoding="utf-8") as file:
        data = json.load(file)
    return data["product_types"]

# ========== 从环境变量里读取配置（GitHub Secrets 会传进来） ===========
RAW_TARGET_URL = os.environ["TARGET_URL"]
COOKIE = os.environ.get("COOKIE", "")  # 形如 "a=1; b=2"
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
MODE = os.environ.get("MODE", "realtime")  # "realtime" / "daily"
ONLY_ON_CHANGE = os.environ.get("ONLY_ON_CHANGE", "false").lower() == "true"
LAST_STOCK_FILE = "last_stock.json"
# =============================================================

def parse_cookies(cookie_str: str):
    cookies = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies

def send_tg_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": text,
    }
    r = requests.post(url, data=data, timeout=10)
    r.raise_for_status()

def fetch_stock_from_url(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0",
    }

    resp = requests.get(
        url,
        headers=headers,
        cookies=parse_cookies(COOKIE),
        timeout=20,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    result = {}
    cards = soup.select("div.card.cartitem")

    for card in cards:
        name_tag = card.find("h4")
        if not name_tag:
            continue
        name = name_tag.get_text(strip=True)

        stock_tag = card.find("p", class_="card-text")
        if not stock_tag:
            continue

        stock_text = stock_tag.get_text(strip=True)
        digits = "".join(ch for ch in stock_text if ch.isdigit())
        if not digits:
            continue

        result[name] = int(digits)

    return result

def fetch_stock():
    urls = [u.strip() for u in RAW_TARGET_URL.split(",") if u.strip()]
    total = {}
    for url in urls:
        part = fetch_stock_from_url(url)
        total.update(part)
    return total

def load_last_stock():
    if not os.path.exists(LAST_STOCK_FILE):
        return None
    try:
        with open(LAST_STOCK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def save_stock(stock_dict):
    with open(LAST_STOCK_FILE, "w", encoding="utf-8") as f:
        json.dump(stock_dict, f, ensure_ascii=False, indent=2)

def diff_stock(old, new):
    changes = {}
    all_keys = sorted(set(old.keys()) | set(new.keys()))
    for k in all_keys:
        o = old.get(k)
        n = new.get(k)
        if o != n:
            changes[k] = (o, n)
    return changes

def build_full_message(stock_dict, mode: str) -> str:
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [f"📊 {mode} 库存汇总", ""]

    # 从配置文件中动态加载产品类型
    product_types = load_product_types()

    for product_type in product_types:
        lines.append(f"【{product_type}】")
        for name, stock in stock_dict.items():
            if product_type in name:
                lines.append(f"{name}: {stock} 台")
        lines.append("")  # 每个产品类型之间分隔一行

    lines.append(f"更新时间：{now_utc}")
    return "\n".join(lines)

def build_change_message(changes: dict, mode: str) -> str:
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [f"🔔 {mode} 库存变动提醒", ""]
    for k, (old, new) in sorted(changes.items()):
        arrow = "↗️" if old < new else "↘️"
        lines.append(f"{k}: {old} -> {new} {arrow}")
    lines.append(f"更新时间：{now_utc}")
    return "\n".join(lines)

def main():
    try:
        current = fetch_stock()
    except Exception as e:
        msg = f"⚠️ 库存监控抓取失败：{e}"
        print(msg)
        send_tg_message(msg)
        return

    if not current:
        msg = "⚠️ 库存监控没有解析到任何库存，请检查页面结构或脚本。"
        print(msg)
        send_tg_message(msg)
        return

    last = load_last_stock()

    if last is None:
        save_stock(current)
        msg = build_full_message(current, MODE) + "\n\n(首次采集)"
        print("First run, sending full stock.")
        send_tg_message(msg)
        return

    changes = diff_stock(last, current)
    save_stock(current)

    if not changes:
        print("No stock changes.")
        if ONLY_ON_CHANGE:
            return
        else:
            msg = build_full_message(current, MODE)
            send_tg_message(msg)
            return

    if ONLY_ON_CHANGE:
        msg = build_change_message(changes, MODE)
    else:
        msg = build_full_message(current, MODE)

    print("Stock changed, sending notification.")
    send_tg_message(msg)

if __name__ == "__main__":
    main()
