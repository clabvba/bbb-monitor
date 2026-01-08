import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ========== 从环境变量里读取配置（GitHub Secrets 会传进来） ===========
# 支持多个 URL，用逗号分隔：URL1,URL2,URL3...
RAW_TARGET_URL = os.environ["TARGET_URL"]
COOKIE = os.environ.get("COOKIE", "")  # 形如 "a=1; b=2"
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
MODE = os.environ.get("MODE", "realtime")  # "realtime" / "daily"
ONLY_ON_CHANGE = os.environ.get("ONLY_ON_CHANGE", "false").lower() == "true"
LAST_STOCK_FILE = "last_stock.json"
# =============================================================

def parse_cookies(cookie_str: str):
    """
    把 "a=1; b=2" 这种字符串转成 dict 给 requests 用
    """
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
    """
    发 Telegram 消息（纯文本）
    """
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": text,
    }
    r = requests.post(url, data=data, timeout=10)
    r.raise_for_status()

def fetch_stock_from_url(url: str):
    """
    从单个 URL 解析库存，返回 dict
    """
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

    # 获取所有商品卡片（包含库存信息的部分）
    cards = soup.select("div.card.cartitem")

    for card in cards:
        # 获取产品名称
        name_tag = card.find("h4")
        if not name_tag:
            continue
        name = name_tag.get_text(strip=True)

        # 获取库存信息
        stock_tag = card.find("p", class_="card-text")
        if not stock_tag:
            continue
        stock_text = stock_tag.get_text(strip=True)

        # 提取库存数量：找出 "库存：" 后的数字
        digits = "".join(ch for ch in stock_text if ch.isdigit())
        if not digits:
            continue

        result[name] = int(digits)

    return result

def fetch_stock():
    """
    支持多个页面：把所有 URL 的库存合并到一个 dict
    """
    urls = [u.strip() for u in RAW_TARGET_URL.split(",") if u.strip()]

    total = {}
    for url in urls:
        part = fetch_stock_from_url(url)
        total.update(part)

    return total

def load_last_stock():
    """
    从 last_stock.json 读取上一次库存
    """
    if not os.path.exists(LAST_STOCK_FILE):
        return None
    try:
        with open(LAST_STOCK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def save_stock(stock_dict):
    """
    把当前库存写入 last_stock.json
    """
    with open(LAST_STOCK_FILE, "w", encoding="utf-8") as f:
        json.dump(stock_dict, f, ensure_ascii=False, indent=2)

def diff_stock(old, new):
    """
    对比新旧库存，返回发生变化的条目：
    { 名称: (旧值, 新值), ... }
    """
    changes = {}
    all_keys = sorted(set(old.keys()) | set(new.keys()))
    for k in all_keys:
        o = old.get(k)
        n = new.get(k)
        if o != n:
            changes[k] = (o, n)
    return changes

def build_full_message(stock_dict, mode: str) -> str:
    """
    输出完整库存列表，按产品类型和可用区域分组
    """
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [f"📊 {mode} 库存汇总", ""]

    # 定义产品类型
    product_types = ["中国人妻", "日本女优", "避孕套", "避孕药", "赞助商", "波多野结衣"]

    for product_type in product_types:
        lines.append(f"【{product_type}】")
        
        # 获取该类型的商品，并按区域展示
        for name, stock in stock_dict.items():
            if product_type in name:
                # 提取出区域信息
                regions = []
                for part in name.split("-")[2:]:
                    if part.isalnum():
                        regions.append(part)

                # 拼接区域信息
                region_str = ", ".join(regions)
                lines.append(f"{name}: {stock} 台 ({region_str})")
        
        lines.append("")  # 每个产品类型之间分隔一行

    lines.append(f"更新时间：{now_utc}")
    return "\n".join(lines)

def build_change_message(changes: dict, mode: str) -> str:
    """
    只输出发生变化的条目
    changes: { name: (old, new), ... }
    """
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

    # 第一次运行：没有历史数据，直接发完整库存，并写入 last_stock.json
    if last is None:
        save_stock(current)
        msg = build_full_message(current, MODE) + "\n\n(首次采集)"
        print("First run, sending full stock.")
        send_tg_message(msg)
        return

    # 有历史数据，对比变化
    changes = diff_stock(last, current)

    # 把最新库存写入文件（供下次对比）
    save_stock(current)

    if not changes:
        print("No stock changes.")
        if ONLY_ON_CHANGE:
            # 只在变化时推送：这里就不发消息
            return
        else:
            # 每次都推送：发完整库存
            msg = build_full_message(current, MODE)
            send_tg_message(msg)
            return

    # 有变化
    if ONLY_ON_CHANGE:
        msg = build_change_message(changes, MODE)
    else:
        msg = build_full_message(current, MODE)

    print("Stock changed, sending notification.")
    send_tg_message(msg)

if __name__ == "__main__":
    main()
