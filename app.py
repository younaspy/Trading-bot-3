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

# Binance API URLs - المحدثة مع دعم الشبكات المختلفة
BINANCE_TESTNET_SPOT = "https://testnet.binance.vision"  # للسبوت تداول
BINANCE_TESTNET_FUTURES = "https://testnet.binancefuture.com"  # للعقود الآجلة
BINANCE_MAINNET = "https://api.binance.com"
BINANCE_MAINNET_FUTURES = "https://fapi.binance.com"

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
    
    def add_user(self, username, api_key, api_secret, is_testnet=True, api_type="spot"):
        user_id = hashlib.sha256(username.encode()).hexdigest()[:16]
        
        self.data["users"][user_id] = {
            "username": username,
            "api_key": api_key,
            "api_secret": api_secret,
            "is_testnet": is_testnet,
            "api_type": api_type,  # spot أو futures
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
    """مدير آمن لـ Binance API مع معالجة محسنة للأخطاء"""
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True, api_type: str = "spot"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.api_type = api_type
        
        # تحديد URL بناءً على نوع API والشبكة
        if testnet:
            if api_type == "futures":
                self.base_url = BINANCE_TESTNET_FUTURES
            else:
                self.base_url = BINANCE_TESTNET_SPOT
        else:
            if api_type == "futures":
                self.base_url = BINANCE_MAINNET_FUTURES
            else:
                self.base_url = BINANCE_MAINNET
        
        self.session = requests.Session()
        self.session.headers.update({
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        })
        self.session.timeout = 30
        print(f"🔧 تهيئة Binance API Manager: {self.base_url}")
    
    def _sign(self, data: str) -> str:
        """توقيع البيانات باستخدام HMAC SHA256"""
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
    
    def _make_request(self, method: str, endpoint: str, params: dict = None, signed: bool = False) -> dict:
        """وظيفة مساعدة لعمل طلبات HTTP"""
        try:
            url = f"{self.base_url}{endpoint}"
            
            if params is None:
                params = {}
            
            # إضافة التوقيع إذا لزم الأمر
            if signed:
                params['timestamp'] = int(time.time() * 1000)
                params['recvWindow'] = 60000
                
                # إنشاء query string للتوقيع
                query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
                signature = self._sign(query_string)
                if signature:
                    params['signature'] = signature
                else:
                    return {'error': 'Failed to generate signature'}
            
            # إرسال الطلب
            if method.upper() == 'GET':
                response = self.session.get(url, params=params, timeout=15)
            elif method.upper() == 'POST':
                response = self.session.post(url, params=params, timeout=15)
            elif method.upper() == 'DELETE':
                response = self.session.delete(url, params=params, timeout=15)
            else:
                return {'error': f'Unsupported method: {method}'}
            
            # معالجة الرد
            if response.status_code == 200:
                try:
                    return response.json()
                except:
                    return {'message': 'Success'}
            else:
                error_msg = f"API Error {response.status_code}: {response.text}"
                print(f"❌ {error_msg}")
                return {'error': error_msg}
                
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Connection error: {e}"
            print(f"❌ {error_msg}")
            return {'error': error_msg}
        except requests.exceptions.Timeout as e:
            error_msg = f"Request timeout: {e}"
            print(f"❌ {error_msg}")
            return {'error': error_msg}
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            print(f"❌ {error_msg}")
            return {'error': error_msg}
    
    def test_connection(self) -> bool:
        """اختبار اتصال API"""
        try:
            # محاولة ping
            result = self._make_request('GET', '/api/v3/ping')
            if 'error' not in result:
                print(f"✅ اتصال ناجح بـ {self.base_url}")
                return True
            
            # محاولة الحصول على وقت الخادم
            result = self._make_request('GET', '/api/v3/time')
            if 'error' not in result:
                print(f"✅ اتصال ناجح عبر /api/v3/time")
                return True
            
            print(f"❌ فشل الاتصال بـ {self.base_url}")
            return False
            
        except Exception as e:
            print(f"❌ استثناء في test_connection: {e}")
            return False
    
    def get_account_info(self) -> Optional[Dict]:
        """الحصول على معلومات الحساب"""
        if self.api_type == "futures":
            endpoint = "/fapi/v2/account"
        else:
            endpoint = "/api/v3/account"
        
        result = self._make_request('GET', endpoint, signed=True)
        if 'error' not in result:
            return result
        return None
    
    def get_balance(self) -> float:
        """الحصول على رصيد USDT"""
        try:
            account_info = self.get_account_info()
            if account_info:
                if self.api_type == "futures":
                    # للعقود الآجلة
                    for asset in account_info.get('assets', []):
                        if asset.get('asset') == 'USDT':
                            return float(asset.get('availableBalance', 0))
                else:
                    # للسبوت تداول
                    for balance in account_info.get('balances', []):
                        if balance.get('asset') == 'USDT':
                            return float(balance.get('free', 0))
            return 0.0
        except Exception as e:
            print(f"❌ خطأ في get_balance: {e}")
            return 0.0
    
    def get_ticker_price(self, symbol: str) -> Optional[float]:
        """الحصول على السعر الحالي"""
        result = self._make_request('GET', '/api/v3/ticker/price', {'symbol': symbol})
        if 'error' not in result and 'price' in result:
            return float(result['price'])
        return None
    
    def get_klines(self, symbol: str, interval: str = '1h', limit: int = 100) -> List:
        """الحصول على بيانات الشموع"""
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }
        result = self._make_request('GET', '/api/v3/klines', params)
        if 'error' not in result:
            return result
        return []
    
    def place_order(self, symbol: str, side: str, quantity: float, order_type: str = 'MARKET') -> Dict:
        """وضع أمر تداول"""
        params = {
            'symbol': symbol,
            'side': side.upper(),
            'type': order_type.upper(),
            'quantity': quantity
        }
        
        if self.api_type == "futures":
            endpoint = "/fapi/v1/order"
            params['positionSide'] = 'BOTH'
        else:
            endpoint = "/api/v3/order"
        
        result = self._make_request('POST', endpoint, params, signed=True)
        return result
    
    def test_api_key(self) -> Dict:
        """اختبار شامل لمفاتيح API"""
        results = {
            'success': False,
            'connection': False,
            'authentication': False,
            'trading_enabled': False,
            'balance': 0.0,
            'message': '',
            'account_type': self.api_type,
            'network': 'Testnet' if self.testnet else 'Mainnet'
        }
        
        try:
            # 1. اختبار الاتصال الأساسي
            if not self.test_connection():
                results['message'] = '❌ فشل الاتصال بـ Binance API'
                return results
            
            results['connection'] = True
            
            # 2. اختبار المصادقة
            account_info = self.get_account_info()
            if not account_info:
                results['message'] = '❌ فشل المصادقة - تحقق من API Key و Secret'
                return results
            
            results['authentication'] = True
            
            # 3. التحقق من إذن التداول
            if self.api_type == "futures":
                can_trade = account_info.get('canTrade', False)
            else:
                can_trade = account_info.get('canTrade', False)
            
            if can_trade:
                results['trading_enabled'] = True
                results['message'] = '✅ يمكن التداول'
            else:
                results['message'] = '⚠️ الحساب ليس لديه إذن للتداول'
            
            # 4. الحصول على الرصيد
            balance = self.get_balance()
            results['balance'] = balance
            
            if results['message'] == '' or '✅' in results['message']:
                results['success'] = True
                if not results['message']:
                    results['message'] = '✅ جميع الاختبارات ناجحة'
            
            return results
            
        except Exception as e:
            results['message'] = f'❌ خطأ غير متوقع: {str(e)}'
            return results
    
    def get_server_time(self) -> Optional[int]:
        """الحصول على وقت الخادم"""
        result = self._make_request('GET', '/api/v3/time')
        if 'error' not in result and 'serverTime' in result:
            return result['serverTime']
        return None

# ==================== TRADING BOT ====================
class SimpleTradingBot:
    """بوت تداول مبسط وآمن"""
    
    def __init__(self, user_id: str, api_key: str, api_secret: str, 
                 testnet: bool = True, api_type: str = "spot"):
        self.user_id = user_id
        self.binance = BinanceAPIManager(api_key, api_secret, testnet, api_type)
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
        print(f"🌐 الشبكة: {'Testnet' if testnet else 'Mainnet'}")
        print(f"📊 النوع: {api_type}")
    
    def start(self):
        """بدء البوت"""
        if self.running:
            return {"status": "error", "message": "البوت يعمل بالفعل"}
        
        # اختبار الاتصال أولاً
        print("🔍 اختبار اتصال API...")
        api_test = self.binance.test_api_key()
        
        if not api_test['success']:
            return {"status": "error", "message": api_test['message']}
        
        print(f"✅ اتصال ناجح! الرصيد: {api_test['balance']} USDT")
        
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
        
        # إغلاق جميع الصفقات
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
                
                # انتظار 5 دقائق قبل المسح التالي
                print(f"⏳ الانتظار 5 دقائق للدورة التالية...")
                for i in range(300):  # 300 ثانية = 5 دقائق
                    if not self.running:
                        break
                    time.sleep(1)
                
            except Exception as e:
                print(f"❌ خطأ في حلقة التداول: {e}")
                time.sleep(60)  # انتظار دقيقة قبل إعادة المحاولة
    
    def _scan_opportunities(self):
        """البحث عن فرص تداول"""
        print("🔍 البحث عن فرص تداول...")
        
        for symbol in self.symbols:
            try:
                print(f"📈 تحليل {symbol}...")
                
                # الحصول على بيانات السوق
                klines = self.binance.get_klines(symbol, self.timeframe, 100)
                if not klines:
                    print(f"  ⚠️ لا توجد بيانات لـ {symbol}")
                    continue
                
                # تحليل البيانات
                analysis = self._analyze_symbol(symbol, klines)
                
                print(f"  📊 نتيجة التحليل: {analysis['score']}/100 - إشارة: {analysis['signal']}")
                
                if analysis['score'] >= self.min_confidence and analysis['signal'] == 'BUY':
                    print(f"  🎯 إشارة شراء لـ {symbol}!")
                    self._execute_trade(symbol, analysis)
                    break  # صفقة واحدة فقط
                    
            except Exception as e:
                print(f"  ❌ خطأ في تحليل {symbol}: {e}")
                continue
    
    def _analyze_symbol(self, symbol: str, klines: list):
        """تحليل رمز العملة"""
        # استخراج الأسعار
        closes = [float(k[4]) for k in klines]
        
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
            trend = "📈 صعودي قوي"
        elif current_price > sma_20:
            score += 20  # اتجاه صعودي
            trend = "📈 صعودي"
        else:
            trend = "📉 هابط"
        
        # RSI
        if 30 < rsi < 40:
            score += 30  # في منطقة الشراء
            rsi_status = "🟢 منطقة شراء"
        elif 40 <= rsi < 70:
            score += 20  # محايد
            rsi_status = "🟡 محايد"
        elif rsi <= 30:
            score += 40  # ذروة بيع
            rsi_status = "🟢🟢 ذروة بيع"
        else:
            score -= 10  # ذروة شراء
            rsi_status = "🔴 ذروة شراء"
        
        # قوة الحركة
        price_change = ((current_price - closes[-5]) / closes[-5]) * 100
        if 2 < price_change < 10:
            score += 20  # حركة إيجابية معتدلة
            momentum = "🚀 إيجابية"
        elif price_change >= 10:
            momentum = "⚠️ قوية جداً"
        else:
            momentum = "⚖️ معتدلة"
        
        signal = 'BUY' if score >= self.min_confidence else 'HOLD'
        
        return {
            'symbol': symbol,
            'score': min(score, 100),
            'signal': signal,
            'price': current_price,
            'sma_20': sma_20,
            'sma_50': sma_50,
            'rsi': rsi,
            'trend': trend,
            'rsi_status': rsi_status,
            'momentum': momentum,
            'price_change': price_change
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
            print(f"  📊 المبلغ: ${quantity * current_price:.2f}")
            
            # وضع أمر الشراء
            print(f"  🛒 وضع أمر شراء...")
            order = self.binance.place_order(symbol, 'BUY', quantity)
            
            if 'error' in order:
                print(f"  ❌ فشل وضع الأمر: {order['error']}")
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
                'status': 'OPEN',
                'order_info': order
            })
            
            print(f"  ✅ صفقة جديدة: {symbol}")
            print(f"  📍 نقطة الدخول: ${current_price:.2f}")
            print(f"  🛑 وقف الخسارة: ${stop_loss:.2f}")
            print(f"  🎯 جني الربح: ${take_profit:.2f}")
            
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
                    print(f"  ⚠️ لا يمكن الحصول على سعر {symbol}")
                    continue
                
                # حساب الربح/الخسارة الحالي
                pnl = (current_price - position['entry_price']) * position['quantity']
                pnl_percent = (pnl / (position['entry_price'] * position['quantity'])) * 100
                
                status = f"ربح: ${pnl:.2f} ({pnl_percent:.1f}%)" if pnl >= 0 else f"خسارة: ${abs(pnl):.2f} ({abs(pnl_percent):.1f}%)"
                print(f"  {symbol}: ${current_price:.2f} | {status}")
                
                # التحقق من وقف الخسارة
                if current_price <= position['stop_loss']:
                    print(f"  🛑 تشغيل وقف الخسارة لـ {symbol}")
                    self._close_position(position, current_price, 'STOP_LOSS')
                
                # التحقق من جني الربح
                elif current_price >= position['take_profit']:
                    print(f"  🎯 تشغيل جني الربح لـ {symbol}")
                    self._close_position(position, current_price, 'TAKE_PROFIT')
                    
            except Exception as e:
                print(f"  ❌ خطأ في إدارة صفقة {position['symbol']}: {e}")
                continue
    
    def _close_position(self, position: dict, close_price: float, reason: str):
        """إغلاق الصفقة"""
        try:
            symbol = position['symbol']
            quantity = position['quantity']
            
            print(f"  🔒 إغلاق صفقة {symbol}...")
            
            # وضع أمر البيع
            order = self.binance.place_order(symbol, 'SELL', quantity)
            
            if 'error' in order:
                print(f"  ❌ فشل إغلاق الصفقة: {order['error']}")
                return
            
            # حساب الربح النهائي
            pnl = (close_price - position['entry_price']) * quantity
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
                'status': 'CLOSED',
                'order_info': order
            }
            
            db.add_trade(self.user_id, closed_trade)
            
            # إزالة من الصفقات النشطة
            self.active_positions.remove(position)
            
            result = "ربح" if pnl >= 0 else "خسارة"
            print(f"  ✅ صفقة مغلقة: {symbol}")
            print(f"  📊 النتيجة: {result} ${abs(pnl):.2f} ({pnl_percent:.1f}%)")
            print(f"  🎯 السبب: {reason}")
            
        except Exception as e:
            print(f"  ❌ خطأ في إغلاق الصفقة: {e}")
    
    def _close_all_positions(self):
        """إغلاق جميع الصفقات"""
        if not self.active_positions:
            print("📭 لا توجد صفقات نشطة للإغلاق")
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
        api_type = request.form.get('api_type', 'spot')
        username = request.form.get('username', 'trader').strip()
        
        if not api_key or not api_secret:
            return render_template('setup.html', error='يجب إدخال جميع الحقول')
        
        # اختبار الاتصال
        try:
            print(f"🔍 اختبار اتصال API...")
            binance = BinanceAPIManager(api_key, api_secret, testnet, api_type)
            api_test = binance.test_api_key()
            
            if not api_test['success']:
                error_msg = api_test['message']
                print(f"❌ فشل اختبار API: {error_msg}")
                return render_template('setup.html', error=error_msg)
            
            print(f"✅ اختبار API ناجح!")
            print(f"   الشبكة: {api_test['network']}")
            print(f"   النوع: {api_test['account_type']}")
            print(f"   الرصيد: {api_test['balance']} USDT")
            
        except Exception as e:
            error_msg = f'خطأ في الاتصال: {str(e)}'
            print(f"❌ استثناء في setup: {error_msg}")
            return render_template('setup.html', error=error_msg)
        
        # حفظ المستخدم
        user_id = db.add_user(username, api_key, api_secret, testnet, api_type)
        session['user_id'] = user_id
        
        # حفظ معلومات API في الجلسة
        session['api_key'] = api_key
        session['api_secret'] = api_secret
        session['testnet'] = testnet
        session['api_type'] = api_type
        
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
        binance = BinanceAPIManager(
            user['api_key'], 
            user['api_secret'], 
            user['is_testnet'],
            user.get('api_type', 'spot')
        )
        user['balance'] = binance.get_balance()
        db.update_user(user_id, {'balance': user['balance']})
        
        # تحديث آخر دخول
        db.update_user(user_id, {'last_login': datetime.now().isoformat()})
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
    
    # حساب الإحصائيات
    total_trades = len(trades)
    profitable_trades = sum(1 for t in trades if t.get('pnl', 0) > 0)
    total_profit = sum(t.get('pnl', 0) for t in trades if t.get('pnl'))
    
    return render_template('dashboard.html', 
                         user=user,
                         bot_status=bot_status,
                         bot_info=bot_info,
                         trades=trades,
                         total_trades=total_trades,
                         profitable_trades=profitable_trades,
                         total_profit=total_profit)

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
            testnet=user['is_testnet'],
            api_type=user.get('api_type', 'spot')
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
        binance = BinanceAPIManager(
            user['api_key'], 
            user['api_secret'], 
            user['is_testnet'],
            user.get('api_type', 'spot')
        )
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
        binance = BinanceAPIManager(
            user['api_key'], 
            user['api_secret'], 
            user['is_testnet'],
            user.get('api_type', 'spot')
        )
        
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
            'timestamp': datetime.now().isoformat(),
            'order_info': order
        })
        
        return jsonify({
            'status': 'success', 
            'message': 'تم الشراء بنجاح',
            'order': order
        })
        
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
        binance = BinanceAPIManager(
            user['api_key'], 
            user['api_secret'], 
            user['is_testnet'],
            user.get('api_type', 'spot')
        )
        
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
            'timestamp': datetime.now().isoformat(),
            'order_info': order
        })
        
        return jsonify({
            'status': 'success', 
            'message': 'تم البيع بنجاح',
            'order': order
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/test_connection', methods=['POST'])
@login_required
def api_test_connection():
    """اختبار اتصال API"""
    user_id = session.get('user_id')
    user = db.get_user(user_id)
    
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'})
    
    try:
        binance = BinanceAPIManager(
            user['api_key'], 
            user['api_secret'], 
            user['is_testnet'],
            user.get('api_type', 'spot')
        )
        api_test = binance.test_api_key()
        
        return jsonify(api_test)
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'خطأ في الاختبار: {str(e)}'
        })

@app.route('/api/get_server_info', methods=['GET'])
@login_required
def get_server_info():
    """الحصول على معلومات الخادم"""
    user_id = session.get('user_id')
    user = db.get_user(user_id)
    
    if not user:
        return jsonify({'error': 'المستخدم غير موجود'})
    
    try:
        binance = BinanceAPIManager(
            user['api_key'], 
            user['api_secret'], 
            user['is_testnet'],
            user.get('api_type', 'spot')
        )
        
        server_time = binance.get_server_time()
        
        return jsonify({
            'server_time': server_time,
            'local_time': int(time.time() * 1000),
            'time_diff': server_time - int(time.time() * 1000) if server_time else None,
            'network': 'Testnet' if user['is_testnet'] else 'Mainnet',
            'api_type': user.get('api_type', 'spot')
        })
        
    except Exception as e:
        return jsonify({'error': str(e)})

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
    
    print("=" * 60)
    print("🌐 تطبيق التداول على Binance")
    print("=" * 60)
    print("📱 افتح المتصفح واذهب إلى: http://localhost:5000")
    print("🔐 كلمة المرور: 2026y")
    print("=" * 60)
    print("\n📋 تعليمات:")
    print("1. افتح http://localhost:5000 في المتصفح")
    print("2. أدخل كلمة المرور: 2026y")
    print("3. احصل على مفاتيح API من:")
    print("   - Testnet: https://testnet.binance.vision")
    print("   - Mainnet: https://www.binance.com")
    print("4. أدخل المفاتيح في صفحة الإعداد")
    print("5. ابدأ التداول!")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
