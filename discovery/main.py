import time      # للوقت
import sqlite3   # للقاعدة
from feature_extractor import DataTranslator # استدعاء المترجم
from anomaly_detector import NetworkInspector # استدعاء المفتش

def save_to_db(info, res):
    conn = sqlite3.connect('project_data.db') # اتصال
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS security_logs 
                  (event_time TEXT, port INTEGER, status TEXT)''') # إنشاء جدول
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("INSERT INTO security_logs VALUES (?, ?, ?)", (now, info[0], res)) # حفظ
    conn.commit()
    conn.close()

def start_detection_engine(raw_input):
    trans = DataTranslator() # تشغيل المترجم
    insp = NetworkInspector() # تشغيل المفتش
    
    clean_features = trans.prepare_data(raw_input) # ترجمة
    final_status = insp.analyze_traffic(clean_features.values[0]) # فحص
    
    save_to_db(raw_input, final_status) # تسجيل
    print(f"[{final_status}] on Port {raw_input[0]}")
    return final_status

if __name__ == "__main__":
    # تجربة فحص يدوي
    test = [80, 1000, 5, 5, 500, 500, 100, 100, 10.0, 100.0, 0.1, 0.1, 0.1, 10.0]
    start_detection_engine(test)