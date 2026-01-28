#!/usr/bin/env python3
"""
📱 تشغيل سهل لنظام التداول من الهاتف
"""

import os
import sys
import webbrowser
import socket
from threading import Timer

def check_port(port=5000):
    """التحقق إذا كان المنفذ مشغول"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('0.0.0.0', port))
        sock.close()
        return True
    except:
        return False

def main():
    """الدالة الرئيسية"""
    
    print("="*50)
    print("📱 نظام التداول على Binance من الهاتف")
    print("="*50)
    
    # التحقق من المكتبات
    try:
        import flask
        import requests
    except ImportError:
        print("❌ المكتبات غير مثبتة!")
        print("📦 جاري تثبيت المكتبات المطلوبة...")
        
        os.system("pip install flask requests")
        
        print("✅ تم تثبيت المكتبات بنجاح")
    
    # التحقق من المنفذ
    if not check_port(5000):
        print("❌ المنفذ 5000 مشغول!")
        print("🔌 أغلاق البرنامج الذي يستخدم المنفذ 5000 أولاً")
        input("اضغط Enter للمحاولة مرة أخرى...")
        
        if not check_port(5000):
            print("❌ لا يزال المنفذ مشغولاً. جرب منفذ آخر...")
            port = input("أدخل منفذاً جديداً (مثل 8080): ") or "8080"
            os.environ['PORT'] = port
        else:
            os.environ['PORT'] = "5000"
    else:
        os.environ['PORT'] = "5000"
    
    # تشغيل التطبيق
    print("\n🚀 جاري تشغيل النظام...")
    
    # فتح المتصفح بعد 3 ثواني
    def open_browser():
        port = os.environ.get('PORT', '5000')
        url = f"http://localhost:{port}"
        print(f"\n🌐 افتح المتصفح واذهب إلى: {url}")
        print("🔐 كلمة المرور: 2026y")
        webbrowser.open(url)
    
    Timer(3, open_browser).start()
    
    # تشغيل Flask app
    from app import app
    port = int(os.environ.get('PORT', 5000))
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False
    )

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 تم إيقاف النظام")
    except Exception as e:
        print(f"\n❌ خطأ: {e}")
        input("اضغط Enter للخروج...")