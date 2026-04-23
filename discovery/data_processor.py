import pandas as pd # مكتبة معالجة البيانات
import numpy as np  # مكتبة العمليات الحسابية
import glob         # للبحث عن الملفات
import os           # للتعامل مع النظام

def process_raw_data():
    # تحديد مكان البيانات الخام
    raw_folder = 'raw_data' 
    all_csv_files = glob.glob(os.path.join(raw_folder, "*.csv"))

    # الـ 14 ميزة الأساسية + عمود النتيجة
    required_cols = [
        'Destination Port', 'Flow Duration', 'Total Fwd Packets', 
        'Total Backward Packets', 'Total Length of Fwd Packets', 
        'Total Length of Bwd Packets', 'Fwd Packet Length Max', 
        'Bwd Packet Length Max', 'Flow Packets/s', 'Flow Bytes/s', 
        'Flow IAT Mean', 'Fwd IAT Mean', 'Bwd IAT Mean', 
        'Packet Length Variance', 'Label'
    ]

    holder = []
    for file in all_csv_files:
        temp_df = pd.read_csv(file, low_memory=False) # قراءة الملف
        temp_df.columns = temp_df.columns.str.strip() # تنظيف أسماء الأعمدة
        valid_cols = [c for c in required_cols if c in temp_df.columns] # اختيار الموجود
        temp_df = temp_df[valid_cols].replace([np.inf, -np.inf], np.nan).dropna() # حذف الخطأ
        holder.append(temp_df)

    final_output = pd.concat(holder, ignore_index=True) # دمج الكل
    final_output.to_csv('cleaned_network_data.csv', index=False) # حفظ
    print("✅ Cleaned CSV is ready!")

if __name__ == "__main__":
    process_raw_data()