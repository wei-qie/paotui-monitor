"""
跑腿派单监控 - 支持本地运行和 GitHub Actions
"""
import json
import re
import time
import os
import smtplib
import email.mime.text
from datetime import datetime, timedelta, timezone
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================ 配置 ================
MIN_PRICE = 30
POLL_INTERVAL = 1200  # 20 分钟

# ================ 邮件配置 ================
EMAIL_CONFIG = {}
sender = os.environ.get("EMAIL_SENDER")
auth_code = os.environ.get("EMAIL_AUTH_CODE")
receiver = os.environ.get("EMAIL_RECEIVER")
if sender and auth_code and receiver:
    EMAIL_CONFIG = {
        "sender": sender,
        "auth_code": auth_code,
        "receiver": receiver,
        "smtp_server": "smtp.qq.com",
        "smtp_port": 465,
    }
    print("[邮件] 环境变量方式加载")
else:
    # 本地方式：从 config.json 加载
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            ec = cfg.get("email", {})
            if ec.get("sender") and ec.get("auth_code") and "未修改" not in str(ec.values()):
                EMAIL_CONFIG = ec
                print("[邮件] config.json 方式加载")
        except Exception:
            pass

if not EMAIL_CONFIG:
    print("[邮件] 未配置，邮件通知不可用")
# =======================================

# ================ 请求参数 ================
REQUEST_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Pt-Ah": "f5d1d021f7ab855e36e5aa8818879fd8",
    "X-Pt-Alias": "uestc",
    "X-Pt-Appid": "wxeb017646cfe6e972",
    "X-Pt-Nd": "94829651396644706780",
    "X-Pt-Od": "a1grS3BjaWRxTnFYaWM1NHNLT29mNFc1b3B5dlluMTloWldLYlplNmczcDlmb2FvdjVxM2xZV2Z0S0hFZk5xcmlMZDRoTDZLbDJLVnFJYXhpN21Lc1h0cW9MWEtoczZ0bForMGZjZVBxSVdPekh4OHZvZVhkNVRQaHFOKzNaV1ppYVI2M0xHcXhyT1daTnFzdkdpblo0dXBtV3UwZzFoa2RvZHJmQT09",
    "X-Pt-Platform": "windows",
    "X-Pt-Td": "1778665075",
    "X-Pt-Version": "3.0.6",
    "Referer": "https://servicewechat.com/wxeb017646cfe6e972/67/page-frame.html",
}
REQUEST_BODY = "task_status=&cur_page=1&campus_id=175201"
API_URL = "https://api.x.paotui.zanao.com/task/list"
# =======================================

# 已通知过的订单 ID 持久化
NOTIFIED_FILE = os.path.join(os.path.dirname(__file__), "notified_tasks.json")
notified_ids = set()
if os.path.exists(NOTIFIED_FILE):
    try:
        with open(NOTIFIED_FILE) as f:
            notified_ids = set(json.load(f))
        print(f"[去重] 已加载 {len(notified_ids)} 条历史通知记录")
    except Exception:
        pass

# Windows 桌面弹窗（仅本地模式可用）
HAS_WIN_UI = False
try:
    import ctypes
    HAS_WIN_UI = hasattr(ctypes, 'windll')
except Exception:
    pass


def save_notified(task_id):
    notified_ids.add(str(task_id))
    try:
        with open(NOTIFIED_FILE, "w") as f:
            json.dump(sorted(notified_ids), f)
    except Exception:
        pass


def send_email(subject, body):
    if not EMAIL_CONFIG:
        return False
    try:
        msg = email.mime.text.MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = EMAIL_CONFIG["sender"]
        msg["To"] = EMAIL_CONFIG["receiver"]
        with smtplib.SMTP_SSL(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"]) as s:
            s.login(EMAIL_CONFIG["sender"], EMAIL_CONFIG["auth_code"])
            s.send_message(msg)
        print("[邮件] 已发送")
        return True
    except Exception as e:
        print(f"[邮件] 发送失败: {e}")
        return False


def alert(task):
    title = task.get("title", "未知")
    price = task.get("pay_price", 0)
    campus = task.get("campus_name", "未知")
    tid = str(task.get("task_id", ""))

    if tid in notified_ids:
        return

    t = task.get("post_time", "")
    msg = f"标题: {title}\n价格: ¥{price}\n校区: {campus}\n发布时间: {t}\n单号: {tid}"

    # 本地模式：桌面弹窗
    if HAS_WIN_UI:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, f"发现高价单! ¥{price}", 0x40 | 0x1000)

    # 邮件
    send_email(f"[跑腿监控] 高价单 ¥{price}", msg)
    save_notified(tid)


def is_recent(post_time_str, max_minutes=30):
    """判断订单发布时间是否在最近 N 分钟内"""
    if not post_time_str:
        return False
    try:
        # API 返回格式: "05-28 09:01"（无年份）或 "2026-05-28 09:01:00"
        try:
            post = datetime.strptime(post_time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            post = datetime.strptime(post_time_str, "%m-%d %H:%M")
            post = post.replace(year=datetime.now().year)
        now = datetime.now(timezone(timedelta(hours=8)))
        post = post.replace(tzinfo=timezone(timedelta(hours=8)))
        return now - post <= timedelta(minutes=max_minutes)
    except ValueError:
        return False


def check_and_alert(task):
    try:
        price = float(task.get("pay_price", 0) or 0)
    except (ValueError, TypeError):
        return

    title = (task.get("title", "") or "")
    text = title + " " + (task.get("task_remark", "") or "")

    if task.get("task_status_text", "") != "待接单":
        return
    if price < MIN_PRICE:
        return
    if not is_recent(task.get("post_time", ""), 30):
        return

    # ========== 原筛选逻辑（已注释，临时启用全量提醒）==========
    # if price >= 65:
    #     print(f"[!!!] 高价单! ¥{price} - {title}")
    #     alert(task)
    #     return
    # if "跑步" in text or "km" in text.lower():
    #     return
    # keywords = ["代打卡", "打卡", "代签到", "签到"]
    # has_keyword = any(kw in text for kw in keywords)
    # has_count = bool(re.search(r'\d+次', text))
    # if not (has_keyword or has_count):
    #     return

    print(f"[!!!] 新单! ¥{price} - {title}")
    alert(task)


def fetch_tasks():
    try:
        resp = requests.post(
            API_URL,
            headers=REQUEST_HEADERS,
            data=REQUEST_BODY,
            verify=False,
            timeout=15,
            proxies={"http": "", "https": ""},
        )
        if resp.status_code != 200:
            print(f"[请求] 失败 HTTP {resp.status_code}")
            return None
        data = resp.json()
        tasks = data.get("data", {}).get("list", [])
        print(f"[请求] 获取到 {len(tasks)} 条订单")
        return tasks
    except Exception as e:
        print(f"[请求] 异常: {e}")
        return None


def run_once():
    """执行一次轮询（供 GitHub Actions 调用）"""
    tasks = fetch_tasks()
    if tasks:
        for t in tasks:
            check_and_alert(t)
        return True
    else:
        print("[!] 请求失败，token 可能已过期")
        return False


def main():
    print("=" * 45)
    print("  跑腿派单监控")
    if "GITHUB_ACTIONS" in os.environ:
        print("  模式: GitHub Actions (云端)")
    else:
        print("  模式: 本地运行")
    print(f"  每 {POLL_INTERVAL // 60} 分钟轮询")
    print("=" * 45)
    if EMAIL_CONFIG:
        print("[邮件] 已配置")
    print(f"[去重] {len(notified_ids)} 条历史通知")
    print()

    while True:
        run_once()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    if os.environ.get("RUN_ONCE") == "1":
        run_once()
    else:
        main()
