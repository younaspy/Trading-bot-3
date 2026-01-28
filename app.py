"""
📱 تطبيق ويب للتداول على Binance من الهاتف
🔐 تسجيل دخول + لوحة تحكم + بوت تداول مبسط
✅ إصلاحات: مزامنة وقت Binance + اختبار API حقيقي + رسائل أخطاء واضحة
"""

import os
import json
import time
import hashlib
import hmac
import threading
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, Optional, List, Tuple

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_cors import CORS
import requests

# ==================== CONFIGURATION ====================
SECRET_PASSWORD = "2026y"  # ⚠️ لاحقًا انقلها لـ ENV
SESSION_SECRET = os.urandom(24).hex()

BINANCE_TESTNET_SPOT = "https://testnet.binance.vision"
BINANCE_MAINNET_SPOT = "https://api.binance.com"

# ==================== FLASK APP ====================
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = SESSION_SECRET
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=24)
CORS(app)

# ==================== DATABASE ====================
class Database:
    def __init__(self):
        self.file_path = "users.json"
        self.data = self.load_data()

    def load_data(self):
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {"users": {}, "trades": {}}
        except Exception:
            return {"users": {}, "trades": {}}

    def save_data(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def add_user(self, username, api_key, api_secret, is_testnet=True):
        user_id = hashlib.sha256(username.encode()).hexdigest()[:16]
        self.data["users"][user_id] = {
            "username": username,
            "api_key": api_key,
            "api_secret": api_secret,
            "is_testnet": is_testnet,
            "created_at": datetime.now().isoformat(),
            "balance": 0.0,
            "last_login": datetime.now().isoformat(),
            "settings": {
                "risk_per_trade": 0.01,
                "max_positions": 1,
                "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT"],
            },
        }
        self.save_data()
        return user_id

    def get_user(self, user_id):
        return self.data["users"].get(user_id)

    def update_user(self, user_id, updates):
        if user_id in self.data["users"]:
            self.data["users"][user_id].update(updates)
            self.save_data()
            return True
        return False

    def add_trade(self, user_id, trade_data):
        if user_id not in self.data["trades"]:
            self.data["trades"][user_id] = []

        trade_data["id"] = hashlib.sha256(str(time.time()).encode()).hexdigest()[:12]
        trade_data["timestamp"] = datetime.now().isoformat()
        self.data["trades"][user_id].append(trade_data)

        if len(self.data["trades"][user_id]) > 100:
            self.data["trades"][user_id] = self.data["trades"][user_id][-100:]

        self.save_data()
        return trade_data["id"]

    def get_trades(self, user_id, limit=50):
        return self.data["trades"].get(user_id, [])[-limit:]


db = Database()

# ==================== BINANCE API MANAGER ====================
class BinanceAPIManager:
    """
    ✅ إصلاحات أساسية:
    - مزامنة الوقت مع Binance لتجنب code=-1021
    - اختبار مفاتيح API الحقيقي عبر /account
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = (api_key or "").strip()
        self.api_secret = (api_secret or "").strip()
        self.base_url = BINANCE_TESTNET_SPOT if testnet else BINANCE_MAINNET_SPOT

        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-MBX-APIKEY": self.api_key,
                "Content-Type": "application/x-www-form-urlencoded",
            }
        )

        # فرق الوقت بين سيرفرنا و Binance (ms)
        self._time_offset_ms = 0

    # ---------- helpers ----------
    def _sign(self, data: str) -> str:
        return hmac.new(
            self.api_secret.encode("utf-8"),
            data.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _get_server_time_ms(self) -> int:
        r = self.session.get(f"{self.base_url}/api/v3/time", timeout=10)
        r.raise_for_status()
        return int(r.json()["serverTime"])

    def sync_time(self) -> bool:
        """مزامنة الوقت مع Binance"""
        try:
            server_ms = self._get_server_time_ms()
            local_ms = int(time.time() * 1000)
            self._time_offset_ms = server_ms - local_ms
            return True
        except Exception as e:
            print("TIME SYNC ERROR:", e)
            return False

    def _now_ms(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def _request(self, method: str, path: str, params: Optional[dict] = None, signed: bool = False, timeout: int = 15):
        """
        طلب موحّد يدعم:
        - signed: يضيف timestamp/recvWindow/signature بعد مزامنة الوقت
        """
        params = params or {}

        if signed:
            # مزامنة الوقت قبل التوقيع (حل مشكلة -1021)
            self.sync_time()
            params["timestamp"] = self._now_ms()
            params.setdefault("recvWindow", 5000)

            qs = "&".join([f"{k}={v}" for k, v in params.items()])
            params["signature"] = self._sign(qs)

        url = f"{self.base_url}{path}"
        if method.upper() == "GET":
            return self.session.get(url, params=params, timeout=timeout)
        else:
            return self.session.post(url, params=params, timeout=timeout)

    # ---------- diagnostics ----------
    def test_api_authentication(self) -> Dict:
        """
        يرجّع تفاصيل واضحة:
        - connection: هل Binance reachable؟
        - authentication: هل المفاتيح صحيحة؟
        - trading_enabled: هل canTrade True؟
        - balance: رصيد USDT
        """
        result = {
            "success": False,
            "message": "",
            "connection": False,
            "authentication": False,
            "trading_enabled": False,
            "balance": 0.0,
            "server_time": None,
        }

        # 1) اختبار اتصال بدون مفاتيح
        try:
            t = self._request("GET", "/api/v3/time", timeout=10)
            if t.status_code != 200:
                result["message"] = f"❌ فشل الوصول إلى Binance: HTTP {t.status_code}"
                return result
            result["connection"] = True
            result["server_time"] = t.json().get("serverTime")
        except Exception as e:
            result["message"] = f"❌ خطأ اتصال/شبكة: {e}"
            return result

        # 2) اختبار مفاتيح (موقّع)
        try:
            r = self._request("GET", "/api/v3/account", signed=True, timeout=15)
            data = {}
            try:
                data = r.json()
            except Exception:
                data = {"raw": r.text[:300]}

            if r.status_code == 200:
                result["authentication"] = True
                result["trading_enabled"] = bool(data.get("canTrade", False))
                for b in data.get("balances", []):
                    if b.get("asset") == "USDT":
                        result["balance"] = float(b.get("free", 0) or 0)
                        break
                result["success"] = True
                result["message"] = "✅ المصادقة ناجحة"
                return result

            # أخطاء Binance ترجع code/msg غالبًا
            code = data.get("code")
            msg = data.get("msg") or str(data)
            result["message"] = f"❌ Binance error (HTTP {r.status_code}) | code={code} | msg={msg}"
            return result

        except Exception as e:
            result["message"] = f"❌ خطأ غير متوقع أثناء المصادقة: {e}"
            return result

    # ---------- basic data ----------
    def get_account_info(self) -> Optional[Dict]:
        try:
            r = self._request("GET", "/api/v3/account", signed=True, timeout=15)
            if r.status_code == 200:
                return r.json()
            return None
        except Exception as e:
            print("get_account_info error:", e)
            return None

    def get_balance(self) -> float:
        info = self.get_account_info()
        if not info:
            return 0.0
        for b in info.get("balances", []):
            if b.get("asset") == "USDT":
                try:
                    return float(b.get("free", 0) or 0)
                except Exception:
                    return 0.0
        return 0.0

    def get_ticker_price(self, symbol: str) -> Optional[float]:
        try:
            r = self._request("GET", "/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
            if r.status_code == 200:
                return float(r.json()["price"])
            return None
        except Exception as e:
            print("get_ticker_price error:", e)
            return None

    def get_klines(self, symbol: str, interval: str = "1h", limit: int = 100) -> List:
        try:
            r = self._request(
                "GET",
                "/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
            return []
        except Exception as e:
            print("get_klines error:", e)
            return []

    def place_order(self, symbol: str, side: str, quantity: float, order_type: str = "MARKET") -> Dict:
        try:
            params = {
                "symbol": symbol,
                "side": side.upper(),
                "type": order_type.upper(),
                "quantity": quantity,
                "recvWindow": 60000,
            }
            r = self._request("POST", "/api/v3/order", params=params, signed=True, timeout=15)
            data = {}
            try:
                data = r.json()
            except Exception:
                data = {"raw": r.text[:300]}

            if r.status_code in (200, 201):
                return data

            # رجّع الخطأ بشكل واضح
            code = data.get("code")
            msg = data.get("msg") or str(data)
            return {"error": f"Binance error (HTTP {r.status_code}) | code={code} | msg={msg}"}
        except Exception as e:
            return {"error": f"place_order exception: {e}"}


# ==================== TRADING BOT ====================
class SimpleTradingBot:
    def __init__(self, user_id: str, api_key: str, api_secret: str, testnet: bool = True):
        self.user_id = user_id
        self.binance = BinanceAPIManager(api_key, api_secret, testnet)
        self.running = False
        self.thread = None

        self.symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        self.timeframe = "1h"
        self.risk_per_trade = 0.01
        self.min_confidence = 70
        self.max_positions = 1

        self.active_positions = []
        self.trade_history = []
        self.balance = 0.0

    def start(self):
        if self.running:
            return {"status": "error", "message": "البوت يعمل بالفعل"}

        api_test = self.binance.test_api_authentication()
        if not api_test["success"]:
            return {"status": "error", "message": api_test["message"]}

        if not api_test["trading_enabled"]:
            return {"status": "error", "message": "❌ حسابك لا يملك إذن التداول (canTrade=false)"}

        self.balance = api_test["balance"]
        if self.balance < 10:
            return {"status": "error", "message": f"الرصيد غير كافٍ ({self.balance} USDT). يجب ≥ 10"}

        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return {"status": "success", "message": "✅ بدأ البوت", "balance": self.balance}

    def stop(self):
        self.running = False
        return {"status": "success", "message": "⏹️ توقف البوت"}

    def get_status(self):
        return {"running": self.running, "balance": self.balance, "active_positions": len(self.active_positions)}

    def _loop(self):
        while self.running:
            try:
                self.balance = self.binance.get_balance()
            except Exception as e:
                print("bot loop error:", e)
            time.sleep(60)


# ==================== AUTH DECORATOR ====================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ==================== ROUTES ====================
active_bots = {}

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = (request.form.get("password") or "").strip()
        if password == SECRET_PASSWORD:
            session["user_id"] = "guest_" + hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]
            session.permanent = True
            return redirect(url_for("setup"))
        return render_template("login.html", error="كلمة المرور غير صحيحة")
    return render_template("login.html")


@app.route("/setup", methods=["GET", "POST"])
@login_required
def setup():
    if request.method == "POST":
        api_key = (request.form.get("api_key") or "").strip()
        api_secret = (request.form.get("api_secret") or "").strip()
        testnet = request.form.get("testnet", "on") == "on"
        username = (request.form.get("username") or "trader").strip()

        if not api_key or not api_secret:
            return render_template("setup.html", error="يجب إدخال جميع الحقول")

        # اختبار مفاتيح بشكل صحيح + عرض خطأ Binance الحقيقي
        binance = BinanceAPIManager(api_key, api_secret, testnet)
        api_test = binance.test_api_authentication()

        if not api_test["success"]:
            # اعرض السبب الحقيقي (code/msg)
            return render_template("setup.html", error=api_test["message"])

        if not api_test["trading_enabled"]:
            return render_template("setup.html", error='❌ فعّل "Enable Trading" في إعدادات API داخل Binance')

        user_id = db.add_user(username, api_key, api_secret, testnet)
        session["user_id"] = user_id
        return redirect(url_for("dashboard"))

    return render_template("setup.html")


@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session.get("user_id")
    user = db.get_user(user_id)
    if not user:
        return redirect(url_for("setup"))

    # تحديث الرصيد
    try:
        b = BinanceAPIManager(user["api_key"], user["api_secret"], user["is_testnet"])
        bal = b.get_balance()
        db.update_user(user_id, {"balance": bal})
        user["balance"] = bal
    except Exception as e:
        print("balance refresh error:", e)

    bot_status = "stopped"
    if user_id in active_bots and active_bots[user_id]["bot"].running:
        bot_status = "running"

    trades = db.get_trades(user_id, 10)
    return render_template("dashboard.html", user=user, bot_status=bot_status, trades=trades)


@app.route("/api/start_bot", methods=["POST"])
@login_required
def start_bot():
    user_id = session.get("user_id")
    user = db.get_user(user_id)
    if not user:
        return jsonify({"status": "error", "message": "المستخدم غير موجود"})

    if user_id in active_bots and active_bots[user_id]["bot"].running:
        return jsonify({"status": "error", "message": "البوت يعمل بالفعل"})

    bot = SimpleTradingBot(user_id, user["api_key"], user["api_secret"], user["is_testnet"])
    res = bot.start()
    if res["status"] == "success":
        active_bots[user_id] = {"bot": bot, "started_at": datetime.now().isoformat()}
    return jsonify(res)


@app.route("/api/stop_bot", methods=["POST"])
@login_required
def stop_bot():
    user_id = session.get("user_id")
    if user_id not in active_bots:
        return jsonify({"status": "error", "message": "لا يوجد بوت نشط"})
    res = active_bots[user_id]["bot"].stop()
    del active_bots[user_id]
    return jsonify(res)


@app.route("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id in active_bots:
        try:
            active_bots[user_id]["bot"].stop()
            del active_bots[user_id]
        except Exception:
            pass
    session.clear()
    return redirect(url_for("index"))


# ==================== LOCAL RUN ====================
if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    os.makedirs("static", exist_ok=True)
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
