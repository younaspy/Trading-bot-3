"""
📱 تطبيق ويب للتداول على Binance من الهاتف
🔐 تسجيل دخول آمن + لوحة تحكم + بوت تداول كامل
"""

import os
import json
import time
import hashlib
import threading
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, Optional

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_cors import CORS
import requests

# ==================== CONFIGURATION ====================
SECRET_PASSWORD = "2026y"  # كلمة المرور الرئيسية
SESSION_SECRET = os.urandom(24).hex()

# Binance API URLs
BINANCE_TESTNET = "https://testnet.binance.vision"
BINANCE_MAINNET = "https://api.binance.com"

# ==================== FLASK APP ====================
app = Flask(__name__, 
           template_folder='templates',
           static_folder='static')
app.config['SECRET_KEY'] = SESSION_SECRET
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
CORS(app)

# ==================== DATABASE (Simplified JSON) ====================
class Database:
    def __init__(self):
        self.file_path = "users.json"
        self.data = self.load_data()
    
    def load_data(self):
        try:
            with open(self.file_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {"users": {}, "sessions": {}, "trades": {}}
    
    def save_data(self):
        with open(self.file_path, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def add_user(self, username, api_key, api_secret, is_testnet=True):
        user_id = hashlib.sha256(username.encode()).hexdigest()[:16]
        
        self.data["users"][user_id] = {
            "username": username,
            "api_key": api_key,
            "api_secret": api_secret,
            "is_testnet": is_testnet,
            "created_at": datetime.now().isoformat(),
            "balance": 0.0,
            "active_bots": {},
            "trade_history": []
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
        
        # Keep only last 100 trades
        if len(self.data["trades"][user_id]) > 100:
            self.data["trades"][user_id] = self.data["trades"][user_id][-100:]
        
        self.save_data()
        return trade_data["id"]
    
    def get_trades(self, user_id, limit=50):
        return self.data["trades"].get(user_id, [])[-limit:]

db = Database()

# ==================== BINANCE API MANAGER ====================
class BinanceAPIManager:
    """مدير آمن لـ Binance API"""
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = BINANCE_TESTNET if testnet else BINANCE_MAINNET
        self.session = requests.Session()
        self.session.headers.update({
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/json'
        })
    
    def test_connection(self):
        """اختبار اتصال API"""
        try:
            response = self.session.get(f"{self.base_url}/api/v3/ping")
            return response.status_code == 200
        except:
            return False
    
    def get_account_info(self):
        """الحصول على معلومات الحساب"""
        try:
            timestamp = int(time.time() * 1000)
            query_string = f"timestamp={timestamp}"
            signature = self._sign(query_string)
            
            params = {
                'timestamp': timestamp,
                'signature': signature
            }
            
            response = self.session.get(f"{self.base_url}/api/v3/account", params=params)
            return response.json()
        except Exception as e:
            print(f"Error getting account info: {e}")
            return None
    
    def get_balance(self):
        """الحصول على رصيد USDT"""
        try:
            account = self.get_account_info()
            if account and 'balances' in account:
                for balance in account['balances']:
                    if balance['asset'] == 'USDT':
                        return float(balance['free'])
            return 0.0
        except:
            return 0.0
    
    def get_ticker_price(self, symbol: str):
        """الحصول على السعر الحالي"""
        try:
            response = self.session.get(f"{self.base_url}/api/v3/ticker/price", params={'symbol': symbol})
            data = response.json()
            return float(data['price'])
        except:
            return None
    
    def get_klines(self, symbol: str, interval: str = '1h', limit: int = 100):
        """الحصول على بيانات الشموع"""
        try:
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            }
            response = self.session.get(f"{self.base_url}/api/v3/klines", params=params)
            return response.json()
        except:
            return []
    
    def place_order(self, symbol: str, side: str, quantity: float, order_type: str = 'MARKET'):
        """وضع أمر تداول"""
        try:
            timestamp = int(time.time() * 1000)
            
            params = {
                'symbol': symbol,
                'side': side,
                'type': order_type,
                'quantity': quantity,
                'timestamp': timestamp
            }
            
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            signature = self._sign(query_string)
            params['signature'] = signature
            
            response = self.session.post(f"{self.base_url}/api/v3/order", params=params)
            return response.json()
        except Exception as e:
            print(f"Error placing order: {e}")
            return {'error': str(e)}
    
    def _sign(self, data: str):
        """توقيع البيانات"""
        import hmac
        return hmac.new(
            self.api_secret.encode('utf-8'),
            data.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

# ==================== TRADING BOT ====================
class SimpleTradingBot:
    """بوت تداول مبسط وآمن"""
    
    def __init__(self, user_id: str, api_key: str, api_secret: str, testnet: bool = True):
        self.user_id = user_id
        self.binance = BinanceAPIManager(api_key, api_secret, testnet)
        self.running = False
        self.thread = None
        
        # إعدادات التداول
        self.symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']
        self.timeframe = '1h'
        self.risk_per_trade = 0.01  # 1% مخاطرة لكل صفقة
        self.min_confidence = 70    # الحد الأدنى للثقة
        self.max_positions = 1      # صفقة واحدة فقط
        
        # حالة البوت
        self.active_positions = []
        self.trade_history = []
        self.balance = 0.0
        self.equity = 0.0
        
        print(f"🤖 بوت تداول جديد للمستخدم {user_id}")
    
    def start(self):
        """بدء البوت"""
        if self.running:
            return {"status": "error", "message": "البوت يعمل بالفعل"}
        
        # اختبار الاتصال أولاً
        if not self.binance.test_connection():
            return {"status": "error", "message": "فشل الاتصال بـ Binance"}
        
        # تحديث الرصيد
        self.balance = self.binance.get_balance()
        if self.balance < 10:
            return {"status": "error", "message": "الرصيد غير كافي (يجب أن يكون ≥ 10 USDT)"}
        
        self.running = True
        self.thread = threading.Thread(target=self._trading_loop, daemon=True)
        self.thread.start()
        
        return {"status": "success", "message": "✅ بدأ البوت بنجاح", "balance": self.balance}
    
    def stop(self):
        """إيقاف البوت"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        
        # إغلاق جميع الصفقات
        self._close_all_positions()
        
        return {"status": "success", "message": "⏹️ توقف البوت"}
    
    def get_status(self):
        """الحصول على حالة البوت"""
        return {
            "running": self.running,
            "balance": self.balance,
            "equity": self.equity,
            "active_positions": len(self.active_positions),
            "total_trades": len(self.trade_history)
        }
    
    def _trading_loop(self):
        """حلقة التداول الرئيسية"""
        print("🔄 بدء حلقة التداول...")
        
        while self.running:
            try:
                # تحديث الرصيد
                self.balance = self.binance.get_balance()
                
                # إدارة الصفقات النشطة
                self._manage_positions()
                
                # البحث عن فرص تداول جديدة
                if len(self.active_positions) < self.max_positions:
                    self._scan_opportunities()
                
                # انتظار 5 دقائق قبل المسح التالي
                time.sleep(300)
                
            except Exception as e:
                print(f"❌ خطأ في حلقة التداول: {e}")
                time.sleep(60)  # انتظار دقيقة قبل إعادة المحاولة
    
    def _scan_opportunities(self):
        """البحث عن فرص تداول"""
        for symbol in self.symbols:
            try:
                # الحصول على بيانات السوق
                klines = self.binance.get_klines(symbol, self.timeframe, 100)
                if not klines:
                    continue
                
                # تحليل البيانات
                analysis = self._analyze_symbol(symbol, klines)
                
                if analysis['score'] >= self.min_confidence:
                    # تنفيذ الصفقة
                    self._execute_trade(symbol, analysis)
                    break  # صفقة واحدة فقط
                    
            except Exception as e:
                print(f"❌ خطأ في تحليل {symbol}: {e}")
                continue
    
    def _analyze_symbol(self, symbol: str, klines: list):
        """تحليل رمز العملة"""
        # استخراج الأسعار
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        
        if len(closes) < 20:
            return {'score': 0, 'signal': 'HOLD'}
        
        current_price = closes[-1]
        
        # حساب المتوسطات المتحركة البسيطة
        def sma(prices, period):
            if len(prices) < period:
                return sum(prices) / len(prices)
            return sum(prices[-period:]) / period
        
        sma_20 = sma(closes, 20)
        sma_50 = sma(closes, 50)
        
        # حساب RSI مبسط
        def calculate_rsi(prices, period=14):
            if len(prices) < period + 1:
                return 50
            
            gains = []
            losses = []
            
            for i in range(1, len(prices)):
                change = prices[i] - prices[i-1]
                if change > 0:
                    gains.append(change)
                    losses.append(0)
                else:
                    gains.append(0)
                    losses.append(abs(change))
            
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            
            if avg_loss == 0:
                return 100
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            return rsi
        
        rsi = calculate_rsi(closes)
        
        # حساب النتيجة
        score = 0
        
        # اتجاه الاتجاه
        if current_price > sma_20 > sma_50:
            score += 40  # اتجاه صعودي قوي
        
        # RSI
        if 30 < rsi < 40:
            score += 30  # في منطقة الشراء
        elif 40 <= rsi < 70:
            score += 20  # محايد
        else:
            score -= 10  # تجاوز الحد
        
        # قوة الحركة
        price_change = ((current_price - closes[-5]) / closes[-5]) * 100
        if 2 < price_change < 10:
            score += 20  # حركة إيجابية معتدلة
        
        signal = 'BUY' if score >= 70 else 'HOLD'
        
        return {
            'symbol': symbol,
            'score': min(score, 100),
            'signal': signal,
            'price': current_price,
            'sma_20': sma_20,
            'sma_50': sma_50,
            'rsi': rsi
        }
    
    def _execute_trade(self, symbol: str, analysis: dict):
        """تنفيذ صفقة"""
        try:
            current_price = analysis['price']
            
            # حساب حجم الصفقة
            risk_amount = self.balance * self.risk_per_trade
            stop_loss_distance = current_price * 0.02  # وقف خسارة 2%
            quantity = risk_amount / stop_loss_distance
            
            # تقريب الكمية
            quantity = round(quantity, 6)
            if quantity <= 0:
                return
            
            # وضع أمر الشراء
            order = self.binance.place_order(symbol, 'BUY', quantity)
            
            if 'error' in order:
                print(f"❌ فشل وضع الأمر: {order['error']}")
                return
            
            # حساب وقف الخسارة وجني الربح
            stop_loss = current_price * 0.98
            take_profit = current_price * 1.04  # نسبة ربح:خسارة 2:1
            
            # حفظ الصفقة
            position = {
                'id': order.get('orderId', str(int(time.time()))),
                'symbol': symbol,
                'side': 'BUY',
                'entry_price': current_price,
                'quantity': quantity,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'entry_time': datetime.now().isoformat(),
                'analysis_score': analysis['score']
            }
            
            self.active_positions.append(position)
            
            # تسجيل الصفقة في قاعدة البيانات
            db.add_trade(self.user_id, {
                **position,
                'type': 'ENTRY',
                'status': 'OPEN'
            })
            
            print(f"✅ صفقة جديدة: {symbol} - الكمية: {quantity} - السعر: ${current_price}")
            
        except Exception as e:
            print(f"❌ خطأ في تنفيذ الصفقة: {e}")
    
    def _manage_positions(self):
        """إدارة الصفقات النشطة"""
        for position in self.active_positions[:]:
            try:
                symbol = position['symbol']
                current_price = self.binance.get_ticker_price(symbol)
                
                if not current_price:
                    continue
                
                # حساب الربح/الخسارة الحالي
                if position['side'] == 'BUY':
                    pnl = (current_price - position['entry_price']) * position['quantity']
                    pnl_percent = (pnl / (position['entry_price'] * position['quantity'])) * 100
                    
                    # التحقق من وقف الخسارة
                    if current_price <= position['stop_loss']:
                        self._close_position(position, current_price, 'STOP_LOSS')
                    
                    # التحقق من جني الربح
                    elif current_price >= position['take_profit']:
                        self._close_position(position, current_price, 'TAKE_PROFIT')
                    
                    # إغلاق إذا مر وقت طويل (ساعتان)
                    entry_time = datetime.fromisoformat(position['entry_time'])
                    if (datetime.now() - entry_time).seconds > 7200:  # 2 ساعة
                        self._close_position(position, current_price, 'TIME_LIMIT')
                        
            except Exception as e:
                print(f"❌ خطأ في إدارة الصفقة: {e}")
                continue
    
    def _close_position(self, position: dict, close_price: float, reason: str):
        """إغلاق الصفقة"""
        try:
            symbol = position['symbol']
            quantity = position['quantity']
            
            # وضع أمر البيع
            order = self.binance.place_order(symbol, 'SELL', quantity)
            
            if 'error' in order:
                print(f"❌ فشل إغلاق الصفقة: {order['error']}")
                return
            
            # حساب الربح النهائي
            if position['side'] == 'BUY':
                pnl = (close_price - position['entry_price']) * quantity
            else:
                pnl = (position['entry_price'] - close_price) * quantity
            
            pnl_percent = (pnl / (position['entry_price'] * quantity)) * 100
            
            # تسجيل إغلاق الصفقة
            closed_trade = {
                **position,
                'exit_price': close_price,
                'exit_time': datetime.now().isoformat(),
                'pnl': pnl,
                'pnl_percent': pnl_percent,
                'close_reason': reason,
                'type': 'EXIT',
                'status': 'CLOSED'
            }
            
            db.add_trade(self.user_id, closed_trade)
            
            # إزالة من الصفقات النشطة
            self.active_positions.remove(position)
            
            print(f"🔒 صفقة مغلقة: {symbol} - الربح: ${pnl:.2f} - السبب: {reason}")
            
        except Exception as e:
            print(f"❌ خطأ في إغلاق الصفقة: {e}")
    
    def _close_all_positions(self):
        """إغلاق جميع الصفقات"""
        for position in self.active_positions[:]:
            try:
                current_price = self.binance.get_ticker_price(position['symbol'])
                if current_price:
                    self._close_position(position, current_price, 'MANUAL_CLOSE')
            except:
                continue

# ==================== FLASK ROUTES ====================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    """الصفحة الرئيسية"""
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """صفحة تسجيل الدخول"""
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        
        if password == SECRET_PASSWORD:
            session['user_id'] = 'guest_' + hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]
            session.permanent = True
            return redirect(url_for('setup'))
        
        return render_template('login.html', error='كلمة المرور غير صحيحة')
    
    return render_template('login.html')

@app.route('/setup', methods=['GET', 'POST'])
@login_required
def setup():
    """صفحة إعداد Binance API"""
    if request.method == 'POST':
        api_key = request.form.get('api_key', '').strip()
        api_secret = request.form.get('api_secret', '').strip()
        testnet = request.form.get('testnet', 'on') == 'on'
        username = request.form.get('username', 'user').strip()
        
        if not api_key or not api_secret:
            return render_template('setup.html', error='يجب إدخال جميع الحقول')
        
        # اختبار الاتصال
        try:
            binance = BinanceAPIManager(api_key, api_secret, testnet)
            if not binance.test_connection():
                return render_template('setup.html', error='فشل الاتصال بـ Binance. تأكد من المفاتيح')
        except:
            return render_template('setup.html', error='مفاتيح API غير صالحة')
        
        # حفظ المستخدم
        user_id = db.add_user(username, api_key, api_secret, testnet)
        session['user_id'] = user_id
        
        # حفظ مفاتيح API في الجلسة
        session['api_key'] = api_key
        session['api_secret'] = api_secret
        session['testnet'] = testnet
        
        return redirect(url_for('dashboard'))
    
    return render_template('setup.html')

# تخزين البوتات النشطة
active_bots = {}

@app.route('/dashboard')
@login_required
def dashboard():
    """لوحة التحكم الرئيسية"""
    user_id = session.get('user_id')
    user = db.get_user(user_id)
    
    if not user:
        return redirect(url_for('setup'))
    
    # تحديث الرصيد
    try:
        binance = BinanceAPIManager(user['api_key'], user['api_secret'], user['is_testnet'])
        user['balance'] = binance.get_balance()
        db.update_user(user_id, {'balance': user['balance']})
    except:
        pass
    
    # الحصول على حالة البوت
    bot_status = active_bots.get(user_id, {}).get('status', 'stopped')
    
    # الحصول على آخر الصفقات
    trades = db.get_trades(user_id, 10)
    
    return render_template('dashboard.html', 
                         user=user,
                         bot_status=bot_status,
                         trades=trades)

@app.route('/api/start_bot', methods=['POST'])
@login_required
def start_bot():
    """بدء البوت"""
    user_id = session.get('user_id')
    user = db.get_user(user_id)
    
    if not user:
        return jsonify({'status': 'error', 'message': 'المستخدم غير موجود'})
    
    # إذا كان البوت يعمل بالفعل
    if user_id in active_bots:
        return jsonify({'status': 'error', 'message': 'البوت يعمل بالفعل'})
    
    # إنشاء وتشغيل البوت
    try:
        bot = SimpleTradingBot(
            user_id=user_id,
            api_key=user['api_key'],
            api_secret=user['api_secret'],
            testnet=user['is_testnet']
        )
        
        result = bot.start()
        
        if result['status'] == 'success':
            active_bots[user_id] = {
                'bot': bot,
                'status': 'running',
                'started_at': datetime.now().isoformat()
            }
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'خطأ في بدء البوت: {str(e)}'})

@app.route('/api/stop_bot', methods=['POST'])
@login_required
def stop_bot():
    """إيقاف البوت"""
    user_id = session.get('user_id')
    
    if user_id not in active_bots:
        return jsonify({'status': 'error', 'message': 'لا يوجد بوت نشط'})
    
    try:
        bot = active_bots[user_id]['bot']
        result = bot.stop()
        
        del active_bots[user_id]
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'خطأ في إيقاف البوت: {str(e)}'})

@app.route('/api/bot_status', methods=['GET'])
@login_required
def bot_status():
    """الحصول على حالة البوت"""
    user_id = session.get('user_id')
    
    if user_id not in active_bots:
        return jsonify({'status': 'stopped'})
    
    bot = active_bots[user_id]['bot']
    status = bot.get_status()
    
    return jsonify({
        'status': 'running',
        'data': status
    })

@app.route('/api/get_balance', methods=['GET'])
@login_required
def get_balance():
    """الحصول على الرصيد"""
    user_id = session.get('user_id')
    user = db.get_user(user_id)
    
    if not user:
        return jsonify({'balance': 0})
    
    try:
        binance = BinanceAPIManager(user['api_key'], user['api_secret'], user['is_testnet'])
        balance = binance.get_balance()
        
        db.update_user(user_id, {'balance': balance})
        
        return jsonify({'balance': balance})
    except:
        return jsonify({'balance': user.get('balance', 0)})

@app.route('/api/get_trades', methods=['GET'])
@login_required
def get_trades():
    """الحصول على الصفقات"""
    user_id = session.get('user_id')
    trades = db.get_trades(user_id, 20)
    return jsonify({'trades': trades})

@app.route('/api/quick_buy', methods=['POST'])
@login_required
def quick_buy():
    """شراء سريع"""
    user_id = session.get('user_id')
    user = db.get_user(user_id)
    
    if not user:
        return jsonify({'status': 'error', 'message': 'المستخدم غير موجود'})
    
    data = request.get_json()
    symbol = data.get('symbol', 'BTCUSDT')
    amount = float(data.get('amount', 10))
    
    try:
        binance = BinanceAPIManager(user['api_key'], user['api_secret'], user['is_testnet'])
        
        # الحصول على السعر الحالي
        price = binance.get_ticker_price(symbol)
        if not price:
            return jsonify({'status': 'error', 'message': 'لا يمكن الحصول على السعر'})
        
        # حساب الكمية
        quantity = amount / price
        
        # وضع الأمر
        order = binance.place_order(symbol, 'BUY', quantity)
        
        if 'error' in order:
            return jsonify({'status': 'error', 'message': order['error']})
        
        # تسجيل الصفقة
        db.add_trade(user_id, {
            'symbol': symbol,
            'side': 'BUY',
            'type': 'MANUAL',
            'quantity': quantity,
            'price': price,
            'amount': amount,
            'timestamp': datetime.now().isoformat()
        })
        
        return jsonify({'status': 'success', 'message': 'تم الشراء بنجاح'})
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/quick_sell', methods=['POST'])
@login_required
def quick_sell():
    """بيع سريع"""
    user_id = session.get('user_id')
    user = db.get_user(user_id)
    
    if not user:
        return jsonify({'status': 'error', 'message': 'المستخدم غير موجود'})
    
    data = request.get_json()
    symbol = data.get('symbol', 'BTCUSDT')
    quantity = float(data.get('quantity', 0.001))
    
    try:
        binance = BinanceAPIManager(user['api_key'], user['api_secret'], user['is_testnet'])
        
        # وضع الأمر
        order = binance.place_order(symbol, 'SELL', quantity)
        
        if 'error' in order:
            return jsonify({'status': 'error', 'message': order['error']})
        
        # تسجيل الصفقة
        db.add_trade(user_id, {
            'symbol': symbol,
            'side': 'SELL',
            'type': 'MANUAL',
            'quantity': quantity,
            'timestamp': datetime.now().isoformat()
        })
        
        return jsonify({'status': 'success', 'message': 'تم البيع بنجاح'})
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/logout')
def logout():
    """تسجيل الخروج"""
    user_id = session.get('user_id')
    
    # إيقاف البوت إذا كان يعمل
    if user_id in active_bots:
        try:
            active_bots[user_id]['bot'].stop()
            del active_bots[user_id]
        except:
            pass
    
    session.clear()
    return redirect(url_for('index'))

# ==================== RUN APPLICATION ====================
if __name__ == '__main__':
    # إنشاء المجلدات المطلوبة
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    print("🌐 تطبيق التداول على Binance يعمل!")
    print("📱 افتح المتصفح واذهب إلى: http://localhost:5000")
    print("🔐 كلمة المرور: 2026y")
    
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)