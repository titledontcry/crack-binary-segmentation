# Drone inspection dashboard

ระบบนี้รันบน **คอมภาคพื้นดิน**: Jetson Nano ส่งภาพนิ่งจากโดรนเข้ามาทาง Wi-Fi, คอมเข้าแถวประมวลผลด้วยโมเดล segmentation, แล้ว Dashboard แสดงภาพ live และผลตรวจตามลำดับภาพ

Pixhawk 2 ทำหน้าที่ควบคุมการบิน; Jetson Nano ควรเป็น companion computer ที่ต่อกล้องและเครือข่าย ไม่ต้องรันโมเดล segmentation บน Jetson ในเวอร์ชันนี้

## สิ่งที่ระบบรับประกัน

- ภาพที่ Jetson ส่งมาได้รับ `image_id` ทันทีและเข้าคิวแบบ FIFO
- เก็บไฟล์ original ไว้ที่คอมก่อนเริ่มทำนาย
- หน้าเว็บมี live-stream แยกจากรายการภาพตรวจ จึงไม่ทำให้ inference ที่ช้ากระทบวิดีโอควบคุมการบิน
- ทุกแถวมีสถานะ `queued`, `processing`, `done`, หรือ `failed` และคลิกเพื่อดู original/overlay/mask ได้
- ผลวัดเก็บเป็น pixel เสมอ; ระบบจะแปลงเป็น mm ก็ต่อเมื่อ Jetson ส่ง `gsd_mm_per_px` ที่น่าเชื่อถือมา

## เริ่มใช้งานบนคอมภาคพื้นดิน

จาก root ของ repository:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r drone_inference/requirements.txt
cp drone_inference/config.example.yaml drone_inference/config.yaml
uvicorn drone_inference.app:app --host 0.0.0.0 --port 8000
```

เปิด `http://<computer-ip>:8000/projects` จากคอมหรืออุปกรณ์ใน Wi-Fi เดียวกัน หน้าเว็บใช้ภาษาอังกฤษทั้งหมด

ก่อนรันจริง แก้ `checkpoint` ใน `config.yaml` ให้ชี้ไปยัง `best.pt` ที่ผ่าน Final Test แล้ว และตั้ง `live_stream_url` เป็น MJPEG/HLS/WebRTC gateway ของ Jetson ตามที่ใช้จริง

## ทดสอบรับภาพจากโดรน

หน้าเว็บทำงานตามลำดับ **Projects → Overview → Start/Stop capture → Images → Human Review → Reports**. สร้างโครงการด้วย **New project**, เปิดโครงการ แล้วกด **Start capture** ระบบจะสร้างโฟลเดอร์ `data/flight_<id>/` และจดเวลาเริ่ม–หยุดใน `data/catalog.sqlite3` ข้อมูลนี้คงอยู่หลังรีสตาร์ตเซิร์ฟเวอร์ โฟลเดอร์ `data/YYYYMMDD_HHMMSS/` เดิมจะปรากฏในโครงการ **Imported inspections** โดยไม่ย้ายไฟล์หรือประมวลผลใหม่

Jetson agent ยังถ่ายและส่งภาพตามเดิม ปุ่มบนเว็บ **ไม่ได้สั่งให้โดรนบินหรือหยุดกล้อง** ฝั่งรับภาพใช้ `metadata.captured_at` (UTC จาก Jetson) จัดภาพเข้ารอบตามช่วงเวลา ภาพที่ถ่ายนอกช่วงหรือไม่มีเวลา UTC ที่ใช้ได้จะเข้า `data/_inbox/` ในสถานะ `held` และไม่ถูกส่งเข้า AI; ภาพที่ถ่ายระหว่างรอบแต่ส่งมาช้าหลังหยุดจะยังเข้ารอบเดิม การส่งภาพซ้ำพร้อม metadata เดิมจะได้ `image_id` เดิม นาฬิกา Jetson กับคอมภาคพื้นดินจึงควรซิงก์เวลาก่อนใช้งานจริง

ให้เริ่ม Ground Station ก่อน แล้วตั้ง agent บน Jetson ให้ส่ง endpoint มาที่ IP ปัจจุบันของคอมภาคพื้นดิน เช่น `http://<ground-computer-ip>:8000` (เส้นทางอัปโหลดคือ `POST /api/images`). เมื่อมีรอบเปิดอยู่ Agent จะอัปโหลดภาพแต่ละใบเข้าคิว ทุกหน้าในเว็บอ้างอิงโครงการเดียวกันและเลือกรอบตรวจเฉพาะได้จาก **Capture session**. หน้า **Images** มีภาพทุกใบพร้อมค้นหา/แบ่งหน้า; เปิดภาพเพื่อเทียบต้นฉบับกับ mask ของ AI, แก้ mask, วัดระยะเอง และบันทึกผล. ผลที่ตรวจแล้วปรากฏในแท็บ **Reviewed** ของ **Human Review**. หน้า **Reports** ส่งออก CSV, ZIP หลักฐานภาพ และ PDF สรุปตามโครงการหรือรอบตรวจที่เลือก พร้อมระบุว่างานตรวจโดยคนครบหรือยัง

หน้าตรวจบันทึกผู้ตรวจ, คำตัดสิน, หมายเหตุ, mask ที่แก้, เส้นวัด และ revision history แยกจากผล AI เดิม. ภาพที่ยังไม่มีผล AI ยังวัดและบันทึกเส้นวัดเองได้. ถ้าไม่มี GSD ที่สอบเทียบแล้ว ระบบรายงานความยาวและความกว้างเป็น px; ไม่แสดงความแม่นยำหรือความคลาดเคลื่อนภาคสนามที่ยังไม่ได้ประเมิน

## สัญญา API สำหรับ Jetson Nano

ส่งภาพนิ่งหลังกล้องบันทึกภาพ (ไม่ใช่ทุก frame ของ live video):

```bash
curl -X POST http://<ground-computer-ip>:8000/api/images \
  -F 'file=@DJI_0001.jpg' \
  -F 'metadata={"captured_at":"2026-10-04T10:30:00Z","latitude":13.7563,"longitude":100.5018,"distance_to_surface_m":5.2,"gsd_mm_per_px":0.42}'
```

`metadata` เป็น JSON และไม่บังคับทุก field. ให้คำนวณ `gsd_mm_per_px` บน Jetson หรือส่ง camera metadata ที่เพียงพอมาในรุ่นถัดไป. ห้ามใช้ GPS altitude แทนระยะถึงผิวเมื่อบินตรวจผนัง/สะพาน

ตัวตอบกลับมี `image_id` และ `status`; Jetson สามารถเรียก `GET /api/images/{image_id}` เพื่อตรวจผลภายหลังได้

มีตัวส่งไฟล์แบบ watch-folder สำหรับ Jetson ที่ [jetson_sender.py](jetson_sender.py): ให้โปรแกรมกล้องบันทึกภาพนิ่งลงโฟลเดอร์ แล้วรัน

```bash
python3 drone_inference/jetson_sender.py \
  --endpoint http://<ground-computer-ip>:8000 \
  --source /path/to/camera/stills
```

ตัวส่งจะ retry เมื่อ Wi-Fi หลุดและไม่ลบ original บน Jetson. การเชื่อม telemetry จาก Pixhawk (GPS, ระยะถึงผิว และ GSD) ยังต้องเติมให้ตรงกับกล้องและวิธีวัดระยะที่ติดตั้งจริง

## ภาพ live จาก Jetson

ให้ Jetson เผย video stream ด้วยวิธีที่กล้องรองรับ (เช่น MJPEG server หรือ RTSP-to-HLS gateway) แล้วใส่ URL ใน `live_stream_url`. Dashboard แสดงเฉพาะ stream นั้น ไม่ส่งภาพวิดีโอผ่าน API upload นี้ เพื่อไม่แย่ง bandwidth กับภาพนิ่งสำหรับวัดผล

สำหรับ Jetson Nano / JetPack 4.6 ใช้ [jetson/](jetson/) ได้โดยตรง: ตัว agent เปิดกล้องเพียง process เดียว, ปล่อย MJPEG และส่งภาพนิ่งเป็นระยะไปยัง API นี้ โดยไม่ยุ่งกับ Pixhawk หรือรันโมเดลบน Jetson.

## โฟลเดอร์ข้อมูล runtime

`data/` และ `config.yaml` ถูก ignore จาก Git:

- `data/<session>/originals/` ภาพต้นฉบับที่รับมาใน session นั้น
- `data/<session>/results/` overlay, mask และ JSON
- `data/<session>/inspection.sqlite3` สถานะคิวและผลวัด

ข้อมูลจากรอบเก่าไม่ถูกลบ แต่จะไม่ปะปนใน dashboard session ใหม่

## ตรวจสอบหลังแก้โค้ด

```bash
.venv/bin/python -m unittest discover -s drone_inference/tests -v
node drone_inference/tests/browser_smoke.mjs
```

ชุดทดสอบเบราว์เซอร์เปิด preview server และ Chrome ชั่วคราว ใช้ฐานข้อมูลสำเนาของรอบ `20261010_134920` พร้อมไฟล์ต้นฉบับแบบอ่านอย่างเดียว ไม่แก้ข้อมูลจริง และทดสอบการแก้ mask, รายการ Human Review, CSV และหน้าจอมือถือ

## ข้อจำกัดปัจจุบัน

โมเดลอ่าน architecture จาก checkpoint; `best_boat.pt` เป็น ConvNeXt-Tiny UPerNet และใช้ sliding window 448 px with 25% overlap. Threshold ค่าเริ่มต้น 0.1 มาจาก validation เท่านั้น; ต้อง freeze ค่านี้หลัง Final Test ก่อนนำไปตรวจงานจริง. ค่า length/width จาก mask ยังเป็นการวัดเชิงภาพ ต้อง validate ด้วย drone field test และ crack gauge.
# crack-inspection-ground-station
