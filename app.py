"""
📱 تطبيق ويب للتداول على Binance من الهاتف
🔐 تسجيل دخول آمن + لوحة تحكم + بوت تداول كامل
"""

import os
import json
import time
import hashlib
import hmac
import threading
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, Optional, List

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_cors import CORS
import requests

# ==================== CONFIGURATION ====================
SECRET_PASSWORD = "2026y"  # كلمة المرور الرئيسية
SESSION_SECRET = os.urandom(24).hex()

# Binance API URLs - المحدثة
BINANCE_TESTNET_SPOT = "https://testnet.binance.vision"  # للسبوت تداول
BINANCE_MAINNET_SPOT = "https://api.binance.com"

# ==================== FLASK APP ====================
app = Flask(__name__,
           template_folder='templates',
           static_folder='static')
app.config['SECRET_KEY'] = SESSION_SECRET
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
CORS(app)

# ==================== DATABASE ====================
class Database:
    def __init__(self):
        self.file_path = "users.json"
        self.data = self.load_data()
    
    def load_data(self):
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {"users": {}, "trades": {}}
    
    def save_data(self):
        with open(self.file_path, 'w', encoding='utf-8') as f:
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
                "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
            }
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
    """مدير آمن لـ Binance API مع إصلاح مشكلة الاتصال"""
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        
        # تحديد URL الصحيح
        if testnet:
            self.base_url = BINANCE_TESTNET_SPOT
            print("🔧 استخدام Testnet API")
        else:
            self.base_url = BINANCE_MAINNET_SPOT
            print("🔧 استخدام Mainnet API")
        
        self.session = requests.Session()
        self.session.headers.update({
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        })
        self.session.timeout = 30
    
    def _sign(self, data: str) -> str:
        """توقيع البيانات"""
        try:
            signature = hmac.new(
                self.api_secret.encode('utf-8'),
                data.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            return signature
        except Exception as e:
            print(f"❌ خطأ في توقيع البيانات: {e}")
            return ""
    
    def test_connection(self) -> bool:
        """اختبار اتصال API - محسّن"""
        print(f"🔍 اختبار الاتصال بـ {self.base_url}")
        
        try:
            # محاولة 1: ping endpoint
            response = self.session.get(f"{self.base_url}/api/v3/ping", timeout=10)
            if response.status_code == 200:
                print("✅ الاتصال بـ /ping ناجح")
                return True
            
            # محاولة 2: time endpoint
            response = self.session.get(f"{self.base_url}/api/v3/time", timeout=10)
            if response.status_code == 200:
                print("✅ الاتصال بـ /time ناجح")
                return True
            
            print(f"❌ فشل الاتصال، كود الخطأ: {response.status_code}")
            print(f"   الرد: {response.text[:200]}")
            return False
            
        except requests.exceptions.ConnectionError as e:
            print(f"❌ خطأ في الاتصال بالشبكة: {e}")
            return False
        except requests.exceptions.Timeout as e:
            print(f"❌ انتهت مهلة الاتصال: {e}")
            return False
        except Exception as e:
            print(f"❌ خطأ غير متوقع: {e}")
            return False
    
    def test_api_authentication(self) -> Dict:
        """اختبار مصادقة API بشكل مفصّل"""
        result = {
            'success': False,
            'message': '',
            'connection': False,
            'authentication': False,
            'trading_enabled': False,
            'balance': 0.0,
            'server_time': None
        }
        
        try:
            # 1. اختبار الاتصال الأساسي
            print("🔍 اختبار الاتصال الأساسي...")
            if not self.test_connection():
                result['message'] = '❌ فشل الاتصال بـ Binance. تحقق من اتصال الإنترنت'
                return result
            
            result['connection'] = True
            
            # 2. الحصول على وقت الخادم
            print("🕐 الحصول على وقت الخادم...")
            server_time_response = self.session.get(f"{self.base_url}/api/v3/time", timeout=10)
            if server_time_response.status_code == 200:
                server_data = server_time_response.json()
                result['server_time'] = server_data.get('serverTime')
                print(f"✅ وقت الخادم: {result['server_time']}")
            else:
                print(f"⚠️ لا يمكن الحصول على وقت الخادم")
            
            # 3. اختبار المصادقة باستخدام account info
            print("🔐 اختبار المصادقة...")
            timestamp = int(time.time() * 1000)
            params = {'timestamp': timestamp, 'recvWindow': 5000}
            
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            signature = self._sign(query_string)
            
            if not signature:
                result['message'] = '❌ فشل في إنشاء التوقيع'
                return result
            
            params['signature'] = signature
            
            # إرسال طلب الحصول على معلومات الحساب
            account_response = self.session.get(
                f"{self.base_url}/api/v3/account",
                params=params,
                timeout=15
            )
            
            if account_response.status_code == 200:
                account_data = account_response.json()
                result['authentication'] = True
                
                # التحقق من إذن التداول
                if account_data.get('canTrade', False):
                    result['trading_enabled'] = True
                
                # الحصول على رصيد USDT
                for balance in account_data.get('balances', []):
                    if balance['asset'] == 'USDT':
                        result['balance'] = float(balance['free'])
                        break
                
                result['message'] = '✅ المصادقة ناجحة'
                result['success'] = True
                
                print(f"✅ الرصيد: {result['balance']} USDT")
                print(f"✅ يمكن التداول: {result['trading_enabled']}")
                
            elif account_response.status_code == 401:
                result['message'] = '❌ مفاتيح API غير صالحة أو منتهية الصلاحية'
                print(f"❌ خطأ 401: {account_response.text}")
            elif account_response.status_code == 400:
                error_data = account_response.json()
                result['message'] = f'❌ خطأ في الطلب: {error_data.get("msg", "طلب غير صالح")}'
                print(f"❌ خطأ 400: {error_data}")
            else:
                result['message'] = f'❌ خطأ غير متوقع: {account_response.status_code}'
                print(f"❌ خطأ {account_response.status_code}: {account_response.text}")
            
            return result
            
        except requests.exceptions.ConnectionError as e:
            result['message'] = f'❌ خطأ في الاتصال: {str(e)}'
            return result
        except Exception as e:
            result['message'] = f'❌ خطأ غير متوقع: {str(e)}'
            return result
    
    def get_account_info(self) -> Optional[Dict]:
        """الحصول على معلومات الحساب"""
        try:
            timestamp = int(time.time() * 1000)
            params = {'timestamp': timestamp, 'recvWindow': 5000}
            
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            signature = self._sign(query_string)
            
            if not signature:
                return None
            
            params['signature'] = signature
            
            response = self.session.get(
                f"{self.base_url}/api/v3/account",
                params=params,
                timeout=15
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"❌ خطأ في get_account_info: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ استثناء في get_account_info: {e}")
            return None
    
    def get_balance(self) -> float:
        """الحصول على رصيد USDT"""
        try:
            account_info = self.get_account_info()
            if account_info and 'balances' in account_info:
                for balance in account_info['balances']:
                    if balance['asset'] == 'USDT':
                        return float(balance['free'])
            return 0.0
        except Exception as e:
            print(f"❌ خطأ في get_balance: {e}")
            return 0.0
    
    def get_ticker_price(self, symbol: str) -> Optional[float]:
        """الحصول على السعر الحالي"""
        try:
            response = self.session.get(
                f"{self.base_url}/api/v3/ticker/price",
                params={'symbol': symbol},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return float(data['price'])
            else:
                print(f"❌ خطأ في get_ticker_price: {response.status_code}")
                return None
        except Exception as e:
            print(f"❌ استثناء في get_ticker_price: {e}")
            return None
    
    def get_klines(self, symbol: str, interval: str = '1h', limit: int = 100) -> List:
        """الحصول على بيانات الشموع"""
        try:
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            }
            response = self.session.get(
                f"{self.base_url}/api/v3/klines",
                params=params,
                timeout=15
            )
            if response.status_code == 200:
                return response.json()
            else:
                print(f"❌ خطأ في get_klines: {response.status_code}")
                return []
        except Exception as e:
            print(f"❌ استثناء في get_klines: {e}")
            return []
    
    def place_order(self, symbol: str, side: str, quantity: float, order_type: str = 'MARKET') -> Dict:
        """وضع أمر تداول"""
        try:
            timestamp = int(time.time() * 1000)
            
            params = {
                'symbol': symbol,
                'side': side.upper(),
                'type': order_type.upper(),
                'quantity': quantity,
                'timestamp': timestamp,
                'recvWindow': 60000
            }
            
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            signature = self._sign(query_string)
            
            if not signature:
                return {'error': 'Failed to generate signature'}
            
            params['signature'] = signature
            
            response = self.session.post(
                f"{self.base_url}/api/v3/order",
                params=params,
                timeout=15
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                error_msg = f"خطأ في وضع الأمر: {response.status_code} - {response.text}"
                print(f"❌ {error_msg}")
                return {'error': error_msg}
                
        except Exception as e:
            error_msg = f"استثناء في place_order: {str(e)}"
            print(f"❌ {error_msg}")
            return {'error': error_msg}

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
        self.risk_per_trade = 0.01
        self.min_confidence = 70
        self.max_positions = 1
        
        # حالة البوت
        self.active_positions = []
        self.trade_history = []
        self.balance = 0.0
        
        print(f"🤖 بوت تداول جديد للمستخدم {user_id}")
    
    def start(self):
        """بدء البوت"""
        if self.running:
            return {"status": "error", "message": "البوت يعمل بالفعل"}
        
        # اختبار اتصال API أولاً
        print("🔍 اختبار اتصال API...")
        api_test = self.binance.test_api_authentication()
        
        if not api_test['success']:
            return {"status": "error", "message": api_test['message']}
        
        if not api_test['trading_enabled']:
            return {"status": "error", "message": "الحساب ليس لديه إذن للتداول"}
        
        self.balance = api_test['balance']
        if self.balance < 10:
            return {"status": "error", "message": f"الرصيد غير كافي ({self.balance} USDT). يجب أن يكون ≥ 10 USDT"}
        
        self.running = True
        self.thread = threading.Thread(target=self._trading_loop, daemon=True)
        self.thread.start()
        
        return {
            "status": "success",
            "message": "✅ بدأ البوت بنجاح",
            "balance": self.balance,
            "details": f"البوت يعمل على {len(self.symbols)} عملات"
        }
    
    def stop(self):
        """إيقاف البوت"""
        if not self.running:
            return {"status": "error", "message": "البوت غير قيد التشغيل"}
        
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        
        self._close_all_positions()
        return {"status": "success", "message": "⏹️ توقف البوت بنجاح"}
    
    def get_status(self):
        """الحصول على حالة البوت"""
        return {
            "running": self.running,
            "balance": self.balance,
            "active_positions": len(self.active_positions),
            "total_trades": len(self.trade_history),
            "symbols": self.symbols
        }
    
    def _trading_loop(self):
        """حلقة التداول الرئيسية"""
        print("🔄 بدء حلقة التداول...")
        
        cycle_count = 0
        while self.running:
            try:
                cycle_count += 1
                print(f"\n📊 دورة التداول #{cycle_count}")
                
                # تحديث الرصيد
                new_balance = self.binance.get_balance()
                if new_balance != self.balance:
                    self.balance = new_balance
                    print(f"💰 الرصيد المحدث: {self.balance} USDT")
                
                # إدارة الصفقات النشطة
                self._manage_positions()
                
                # البحث عن فرص تداول جديدة
                if len(self.active_positions) < self.max_positions:
                    self._scan_opportunities()
                
                # انتظار 1 دقيقة فقط للتجربة
                print(f"⏳ الانتظار 60 ثانية للدورة التالية...")
                for i in range(60):
                    if not self.running:
                        break
                    time.sleep(1)
                
            except Exception as e:
                print(f"❌ خطأ في حلقة التداول: {e}")
                time.sleep(30)
    
    def _scan_opportunities(self):
        """البحث عن فرص تداول"""
        print("🔍 البحث عن فرص تداول...")
        
        for symbol in self.symbols:
            try:
                print(f"📈 تحليل {symbol}...")
                
                klines = self.binance.get_klines(symbol, self.timeframe, 50)
                if not klines:
                    print(f"  ⚠️ لا توجد بيانات لـ {symbol}")
                    continue
                
                analysis = self._analyze_symbol(symbol, klines)
                
                print(f"  📊 نتيجة التحليل: {analysis['score']}/100 - إشارة: {analysis['signal']}")
                
                if analysis['score'] >= self.min_confidence and analysis['signal'] == 'BUY':
                    print(f"  🎯 إشارة شراء لـ {symbol}!")
                    self._execute_trade(symbol, analysis)
                    break
                    
            except Exception as e:
                print(f"  ❌ خطأ في تحليل {symbol}: {e}")
                continue
    
    def _analyze_symbol(self, symbol: str, klines: list):
        """تحليل رمز العملة"""
        closes = [float(k[4]) for k in klines]
        
        if len(closes) < 20:
            return {'score': 0, 'signal': 'HOLD'}
        
        current_price = closes[-1]
        
        def sma(prices, period):
            if len(prices) < period:
                return sum(prices) / len(prices)
            return sum(prices[-period:]) / period
        
        sma_20 = sma(closes, 20)
        sma_50 = sma(closes, 50)
        
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
        
        score = 0
        
        if current_price > sma_20 > sma_50:
            score += 40
        
        if 30 < rsi < 40:
            score += 30
        elif 40 <= rsi < 70:
            score += 20
        else:
            score -= 10
        
        price_change = ((current_price - closes[-5]) / closes[-5]) * 100
        if 2 < price_change < 10:
            score += 20
        
        signal = 'BUY' if score >= self.min_confidence else 'HOLD'
        
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
            
            risk_amount = self.balance * self.risk_per_trade
            stop_loss_distance = current_price * 0.02
            quantity = risk_amount / stop_loss_distance
            
            if symbol == "BTCUSDT":
                quantity = round(quantity, 6)
            elif symbol == "ETHUSDT":
                quantity = round(quantity, 5)
            else:
                quantity = round(quantity, 4)
            
            if quantity <= 0:
                print(f"  ⚠️ الكمية غير صالحة: {quantity}")
                return
            
            print(f"  💰 كمية التداول: {quantity} {symbol.replace('USDT', '')}")
            
            order = self.binance.place_order(symbol, 'BUY', quantity)
            
            if 'error' in order:
                print(f"  ❌ فشل وضع الأمر: {order['error']}")
                return
            
            stop_loss = current_price * 0.98
            take_profit = current_price * 1.04
            
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
            
            db.add_trade(self.user_id, {
                **position,
                'type': 'ENTRY',
                'status': 'OPEN',
                'order_info': order
            })
            
            print(f"  ✅ صفقة جديدة: {symbol}")
            
        except Exception as e:
            print(f"  ❌ خطأ في تنفيذ الصفقة: {e}")
    
    def _manage_positions(self):
        """إدارة الصفقات النشطة"""
        if not self.active_positions:
            return
        
        print(f"📋 إدارة {len(self.active_positions)} صفقة نشطة...")
        
        for position in self.active_positions[:]:
            try:
                symbol = position['symbol']
                current_price = self.binance.get_ticker_price(symbol)
                
                if not current_price:
                    continue
                
                pnl = (current_price - position['entry_price']) * position['quantity']
                
                if current_price <= position['stop_loss']:
                    self._close_position(position, current_price, 'STOP_LOSS')
                elif current_price >= position['take_profit']:
                    self._close_position(position, current_price, 'TAKE_PROFIT')
                    
            except Exception as e:
                print(f"  ❌ خطأ في إدارة صفقة {position['symbol']}: {e}")
                continue
    
    def _close_position(self, position: dict, close_price: float, reason: str):
        """إغلاق الصفقة"""
        try:
            symbol = position['symbol']
            quantity = position['quantity']
            
            order = self.binance.place_order(symbol, 'SELL', quantity)
            
            if 'error' in order:
                print(f"  ❌ فشل إغلاق الصفقة: {order['error']}")
                return
            
            pnl = (close_price - position['entry_price']) * quantity
            
            closed_trade = {
                **position,
                'exit_price': close_price,
                'exit_time': datetime.now().isoformat(),
                'pnl': pnl,
                'close_reason': reason,
                'type': 'EXIT',
                'status': 'CLOSED',
                'order_info': order
            }
            
            db.add_trade(self.user_id, closed_trade)
            self.active_positions.remove(position)
            
            result = "ربح" if pnl >= 0 else "خسارة"
            print(f"  ✅ صفقة مغلقة: {symbol} - {result}")
            
        except Exception as e:
            print(f"  ❌ خطأ في إغلاق الصفقة: {e}")
    
    def _close_all_positions(self):
        """إغلاق جميع الصفقات"""
        if not self.active_positions:
            return
        
        print(f"🔒 إغلاق جميع الصفقات ({len(self.active_positions)})...")
        
        for position in self.active_positions[:]:
            try:
                current_price = self.binance.get_ticker_price(position['symbol'])
                if current_price:
                    self._close_position(position, current_price, 'MANUAL_CLOSE')
            except:
                continue

# ==================== HELPER FUNCTIONS ====================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ==================== FLASK ROUTES ====================
active_bots = {}

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
        username = request.form.get('username', 'trader').strip()
        
        if not api_key or not api_secret:
            return render_template('setup.html', error='يجب إدخال جميع الحقول')
        
        # اختبار الاتصال
        try:
            print(f"\n" + "="*50)
            print(f"🔍 بدء اختبار اتصال API...")
            print(f"📝 معلومات الإدخال:")
            print(f"   API Key: {api_key[:8]}...{api_key[-4:] if len(api_key) > 12 else ''}")
            print(f"   Testnet: {testnet}")
            print(f"   Username: {username}")
            
            binance = BinanceAPIManager(api_key, api_secret, testnet)
            
            print(f"🌐 الاتصال بـ: {binance.base_url}")
            
            api_test = binance.test_api_authentication()
            
            print(f"\n📊 نتائج الاختبار:")
            print(f"   النجاح: {api_test['success']}")
            print(f"   الرسالة: {api_test['message']}")
            print(f"   الاتصال: {api_test['connection']}")
            print(f"   المصادقة: {api_test['authentication']}")
            print(f"   التداول مفعل: {api_test['trading_enabled']}")
            print(f"   الرصيد: {api_test['balance']} USDT")
            print("="*50 + "\n")
            
            if not api_test['success']:
                error_msg = api_test['message']
                
                # اقتراح حلول للمشاكل الشائعة
                suggestions = ""
                if "401" in error_msg:
                    suggestions = "<br><br>💡 <strong>الحل المقترح:</strong><br>"
                    suggestions += "1. تأكد من أن المفاتيح صحيحة<br>"
                    suggestions += "2. تأكد من تفعيل 'Enable Trading' في إعدادات API<br>"
                    suggestions += "3. إذا كانت المفاتيح قديمة، أنشئ مفاتيح جديدة"
                elif "Connection" in error_msg:
                    suggestions = "<br><br>💡 <strong>الحل المقترح:</strong><br>"
                    suggestions += "1. تحقق من اتصال الإنترنت<br>"
                    suggestions += "2. جرب استخدام VPN<br>"
                    suggestions += "3. تأكد من أن الرابط صحيح"
                
                return render_template('setup.html', error=error_msg + suggestions)
            
            if not api_test['trading_enabled']:
                return render_template('setup.html', 
                    error='الحساب ليس لديه إذن للتداول. تأكد من تفعيل "Enable Trading" في إعدادات Binance API')
            
        except Exception as e:
            error_msg = f'خطأ في الاتصال: {str(e)}'
            print(f"❌ استثناء في setup: {error_msg}")
            return render_template('setup.html', error=error_msg)
        
        # حفظ المستخدم
        user_id = db.add_user(username, api_key, api_secret, testnet)
        session['user_id'] = user_id
        
        print(f"✅ تم حفظ المستخدم: {username} ({user_id})")
        
        return redirect(url_for('dashboard'))
    
    return render_template('setup.html')

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
    except Exception as e:
        print(f"⚠️ خطأ في تحديث الرصيد: {e}")
    
    # الحصول على حالة البوت
    bot_status = 'stopped'
    bot_info = {}
    if user_id in active_bots:
        bot = active_bots[user_id]['bot']
        status = bot.get_status()
        bot_status = 'running' if status['running'] else 'stopped'
        bot_info = status
    
    # الحصول على آخر الصفقات
    trades = db.get_trades(user_id, 10)
    
    return render_template('dashboard.html', 
                         user=user,
                         bot_status=bot_status,
                         bot_info=bot_info,
                         trades=trades)

@app.route('/api/start_bot', methods=['POST'])
@login_required
def start_bot():
    """بدء البوت"""
    user_id = session.get('user_id')
    user = db.get_user(user_id)
    
    if not user:
        return jsonify({'status': 'error', 'message': 'المستخدم غير موجود'})
    
    if user_id in active_bots:
        bot = active_bots[user_id]['bot']
        if bot.running:
            return jsonify({'status': 'error', 'message': 'البوت يعمل بالفعل'})
    
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
        error_msg = f'خطأ في بدء البوت: {str(e)}'
        print(f"❌ {error_msg}")
        return jsonify({'status': 'error', 'message': error_msg})

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
        error_msg = f'خطأ في إيقاف البوت: {str(e)}'
        print(f"❌ {error_msg}")
        return jsonify({'status': 'error', 'message': error_msg})

@app.route('/api/bot_status', methods=['GET'])
@login_required
def bot_status():
    """الحصول على حالة البوت"""
    user_id = session.get('user_id')
    
    if user_id not in active_bots:
        return jsonify({'status': 'stopped', 'message': 'البوت متوقف'})
    
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
        
        return jsonify({
            'balance': balance,
            'currency': 'USDT'
        })
    except Exception as e:
        print(f"⚠️ خطأ في get_balance: {e}")
        return jsonify({'balance': user.get('balance', 0)})

@app.route('/api/get_trades', methods=['GET'])
@login_required
def get_trades():
    """الحصول على الصفقات"""
    user_id = session.get('user_id')
    trades = db.get_trades(user_id, 20)
    return jsonify({'trades': trades})

@app.route('/api/test_api', methods=['POST'])
@login_required
def test_api():
    """اختبار API جديد"""
    try:
        data = request.get_json()
        api_key = data.get('api_key', '').strip()
        api_secret = data.get('api_secret', '').strip()
        testnet = data.get('testnet', True)
        
        if not api_key or not api_secret:
            return jsonify({'success': False, 'message': 'يجب إدخال جميع الحقول'})
        
        binance = BinanceAPIManager(api_key, api_secret, testnet)
        api_test = binance.test_api_authentication()
        
        return jsonify(api_test)
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'خطأ في الاختبار: {str(e)}'
        })

@app.route('/logout')
def logout():
    """تسجيل الخروج"""
    user_id = session.get('user_id')
    
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
    
    print("\n" + "="*60)
    print("🚀 تطبيق التداول على Binance")
    print("="*60)
    print("📱 افتح المتصفح واذهب إلى: http://localhost:5000")
    print("🔐 كلمة المرور: 2026y")
    print("="*60)
    
    print("\n📋 تعليمات مهمة لإصلاح مشكلة الاتصال:")
    print("1. تأكد من أنك تحصل على المفاتيح من المكان الصحيح:")
    print("   - Testnet: https://testnet.binance.vision")
    print("   - Mainnet: https://www.binance.com")
    print("")
    print("2. عند إنشاء API Keys، تأكد من:")
    print("   ✓ تفعيل 'Enable Trading'")
    print("   ✓ عدم تفعيل 'Restrict Access to Trusted IPs Only'")
    print("   ✓ حفظ Secret Key فوراً (لن تتمكن من رؤيته مرة أخرى)")
    print("")
    print("3. إذا استمرت المشكلة:")
    print("   - جرب استخدام VPN")
    print("   - تحقق من اتصال الإنترنت")
    print("   - أنشئ مفاتيح API جديدة")
    print("="*60 + "\n")
    
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
