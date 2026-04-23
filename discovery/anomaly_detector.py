import joblib   # مكتبة تحميل الموديل المحفوظ
import pandas as pd # مكتبة التعامل مع الجداول

class NetworkInspector: # تأكد إن الاسم هنا NetworkInspector
    def __init__(self, model_filename='network_anomaly_model.pkl'):
        try:
            # تحميل العقل المدرب (Model)
            self.brain_model = joblib.load(model_filename)
            # القائمة الدقيقة للـ 14 ميزة
            self.required_columns = [
                'Destination Port', 'Flow Duration', 'Total Fwd Packets', 
                'Total Backward Packets', 'Total Length of Fwd Packets', 
                'Total Length of Bwd Packets', 'Fwd Packet Length Max', 
                'Bwd Packet Length Max', 'Flow Packets/s', 'Flow Bytes/s', 
                'Flow IAT Mean', 'Fwd IAT Mean', 'Bwd IAT Mean', 
                'Packet Length Variance'
            ]
            print("✅ AI Engine loaded successfully.")
        except Exception as e:
            print(f"❌ Error loading AI model: {e}")

    def analyze_traffic(self, packet_data):
        # تحويل البيانات لجدول وسؤال الموديل عن توقعه
        data_table = pd.DataFrame([packet_data], columns=self.required_columns)
        prediction_result = self.brain_model.predict(data_table)
        
        # -1 يعني هجوم (Anomaly)، 1 يعني طبيعي (Normal)
        if prediction_result[0] == -1:
            return "ATTACK"
        else:
            return "NORMAL"

# قسم الاختبار الذاتي (Security Check)
if __name__ == "__main__":
    inspector = NetworkInspector()
    print("📡 Inspector is ready for standalone test.")