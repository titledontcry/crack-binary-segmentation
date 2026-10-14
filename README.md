# Crack Binary Segmentation

โปรเจกต์สำหรับตรวจหารอยแตกร้าวบนพื้นถนนด้วยการแบ่งส่วนภาพ (binary crack segmentation) เปรียบเทียบโมเดล U-Net/ResNet34 กับ ConvNeXt-Tiny/UPerNet และเชื่อมผล inference เข้ากับเว็บ Ground Station สำหรับภาพจากโดรน

## ส่วนประกอบ

- `train.ipynb` — เตรียมข้อมูล ฝึกโมเดล จูน hyperparameters ด้วย Optuna และประเมินผล
- `final_road_dataset_cleaned/` — ชุดข้อมูลและ manifest สำหรับแบ่ง train/validation/test (ต้องเตรียมแยก ไม่ได้รวมไว้ใน Git)
- `runs/` — checkpoint, log และผลการทดลอง (ไฟล์โมเดลขนาดใหญ่อาจไม่ได้อยู่ใน Git)
- `drone_inference/` — API และเว็บ Ground Station สำหรับรับภาพจาก Jetson, ทำ inference, ตรวจทานผล และ export รายงาน

## ผลการทดลองโมเดล

ผล validation จาก checkpoint ที่เลือก:

| โมเดล | Best epoch | IoU | Dice | Tolerance-F1 (±2 px) |
|---|---:|---:|---:|---:|
| U-Net / ResNet34 | 36 | 0.6374 | 0.7786 | 0.8662 |
| ConvNeXt-Tiny / UPerNet | 33 | 0.6469 | 0.7856 | 0.8728 |

ตัวเลขนี้เป็นผลบน validation ไม่ใช่ผล test; โปรดดูผลประเมิน test แยกต่างหากก่อนสรุปประสิทธิภาพทั่วไป

## เริ่มเว็บ Ground Station

ต้องมี Python และชุดข้อมูล/ไฟล์ checkpoint ตาม config ก่อน จากโฟลเดอร์หลักของโปรเจกต์:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r drone_inference/requirements.txt
cp drone_inference/config.example.yaml drone_inference/config.yaml
```

แก้ `drone_inference/config.yaml` ให้ชี้ไปยัง checkpoint และตั้งค่าที่จำเป็น จากนั้นเริ่ม server:

```bash
uvicorn drone_inference.app:app --host 0.0.0.0 --port 8000
```

เปิด `http://localhost:8000/projects` บนเครื่องเดียวกัน หรือใช้ IP ของคอมภาคพื้นดินเมื่อเข้าเว็บจากอุปกรณ์ในเครือข่ายเดียวกัน

ขั้นตอนติดตั้งและการใช้งาน Jetson, API, capture sessions และการแก้ปัญหาอยู่ใน [คู่มือ Ground Station](drone_inference/README.md) และ [คู่มือ Jetson agent](drone_inference/jetson/README.md)

## ประเมินและตรวจสอบ

ใช้ manifest และ checkpoint ที่ตรงกับการทดลอง เพื่อประเมินบน test set ที่กันไว้ และอย่าใช้ test set เลือก hyperparameters หรือ checkpoint สำหรับรายละเอียด workflow ของ notebook โปรดดูคำอธิบายใน `train.ipynb`.

ทดสอบระบบเว็บ/API:

```bash
.venv/bin/python -m unittest discover -s drone_inference/tests -v
node drone_inference/tests/browser_smoke.mjs
```

## หมายเหตุ

- ภาพ UAV จริงที่ไม่มี Ground Truth ใช้แสดงผล inference เชิงคุณภาพได้ แต่ใช้คำนวณ Dice/IoU หรือยืนยันความแม่นยำไม่ได้
- ความสูงจาก barometer/EKF เป็นค่าประมาณเทียบจุดอ้างอิงของอากาศยาน ไม่ใช่ระยะเหนือพื้นดิน (AGL) โดยอัตโนมัติ
- การแปลงระยะรอยแตกจากพิกเซลเป็นหน่วยจริงต้องมี GSD/การสอบเทียบที่เหมาะสมกับกล้อง ระยะ และสภาพการถ่ายภาพ
- ตรวจสอบและปฏิบัติตามข้อควรระวังด้านความปลอดภัยก่อนใช้งานโดรนจริง ระบบเว็บไม่ได้สั่งควบคุมการบิน
