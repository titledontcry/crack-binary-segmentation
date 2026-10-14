# Crack Binary Segmentation & Drone Ground Station

โปรเจกต์นี้ประกอบด้วย 3 ส่วนที่ทำงานต่อกัน: (1) ชุดข้อมูลและโน้ตบุ๊กสำหรับฝึก/ประเมินโมเดล segmentation, (2) Ground Station บนคอมพิวเตอร์ภาคพื้นดินสำหรับรับภาพและประมวลผล AI, และ (3) camera agent บน Jetson สำหรับถ่ายภาพนิ่งพร้อมแนบ telemetry แล้วส่งเข้าระบบ

> ขอบเขตความปลอดภัย: Ground Station และ Jetson camera agent ใน repo นี้รับ/ประมวลผลภาพและ telemetry แต่ **ไม่ได้ควบคุมการบินหรือส่งคำสั่งไปยัง Pixhawk**

## สารบัญ

- [โครงสร้าง Repository](#โครงสร้าง-repository)
- [สถาปัตยกรรมและลำดับการทำงาน](#สถาปัตยกรรมและลำดับการทำงาน)
- [รายละเอียดโมดูล](#รายละเอียดโมดูล)
- [การฝึกและประเมินโมเดล](#การฝึกและประเมินโมเดล)
- [ติดตั้งและเริ่ม Ground Station](#ติดตั้งและเริ่ม-ground-station)
- [การรับภาพจาก Jetson](#การรับภาพจาก-jetson)
- [ข้อมูลที่ระบบจัดเก็บ](#ข้อมูลที่ระบบจัดเก็บ)
- [การทดสอบ](#การทดสอบ)
- [ไฟล์ที่ไม่ควร commit](#ไฟล์ที่ไม่ควร-commit)
- [ข้อจำกัด](#ข้อจำกัด)

## โครงสร้าง Repository

```text
.
├── README.md                         # คู่มือภาพรวมและอธิบายโครงสร้างโค้ด
├── train.ipynb                       # training, Optuna, validation และ model comparison
├── training-requirements.txt         # dependencies สำหรับ notebook/training
├── final_road_dataset_cleaned/       # dataset + manifest (เก็บในเครื่อง ไม่ใส่ Git)
├── runs/                             # checkpoints, training logs, evaluation artifacts
├── drone_inference/
│   ├── app.py                        # FastAPI app, API, queue/worker และ persistence
│   ├── workspace_api.py              # project/workspace, review และ report endpoints
│   ├── pipeline.py                   # โหลดโมเดล, tile inference, mask และ measurements
│   ├── load_model.py                 # ตัวช่วยโหลด checkpoint/ตรวจ architecture
│   ├── config.example.yaml           # config ตัวอย่าง; คัดลอกเป็น config.yaml ก่อนใช้
│   ├── requirements.txt              # dependencies ของ Ground Station
│   ├── jetson_sender.py              # ส่งไฟล์ภาพจาก watch-folder เข้า API
│   ├── jetson/                       # camera agent ที่ติดตั้งบน Jetson
│   ├── static/                       # หน้าเว็บ, JavaScript และ CSS
│   ├── tests/                        # API/unit และ browser smoke tests
│   └── *_*.py                        # benchmark, evaluation, plots และตัวอย่างภาพ
├── scripts/                          # utilities สร้าง manifest และตรวจ near-duplicates
├── reports/phash/                    # รายงาน/ผลตรวจ perceptual-hash ที่สร้างไว้
├── plan.md                           # แผนงาน/บริบทการพัฒนา
├── pipeline.png                      # ภาพ pipeline ประกอบเอกสาร
└── pipeline(edited).png              # ภาพ pipeline ฉบับแก้ไข
```

`drone_inference/README.md` มีคู่มือ Ground Station, API และ project workflow เพิ่มเติม ส่วน `drone_inference/jetson/README.md` อธิบายการติดตั้งและรัน agent บน Jetson โดยละเอียด

## สถาปัตยกรรมและลำดับการทำงาน

```text
กล้องบนโดรน
   ↓
Jetson camera agent
   ├── ถ่ายภาพนิ่งตาม interval
   ├── เก็บภาพลง spool ก่อนส่ง
   ├── อ่าน MAVLink telemetry แบบ read-only (ถ้าตั้งค่าไว้)
   └── POST ภาพ + metadata ไปยัง Ground Station
          ↓
FastAPI /api/images
   ├── ตรวจข้อมูลและกำหนด image/session ID
   ├── บันทึก original ลง disk และรายการลง SQLite
   └── ใส่ image เข้า FIFO inference queue
          ↓
Inference worker → CrackPredictor → overlay / mask / metrics เชิงภาพ
          ↓
Projects → Overview / Images → Human Review → Reports / Export
```

ปุ่ม **Start/Stop capture** ในเว็บใช้เปิด/ปิดรอบรับภาพใน Ground Station เท่านั้น ไม่ได้สั่งให้โดรนเริ่ม/หยุดบินหรือสั่ง camera agent ปิดกล้อง ตัว agent ทำงานตามการตั้งค่าของมัน และ Ground Station ตัดสินใจว่าจะรับภาพเข้ารอบปัจจุบันหรือไม่

Live feed เป็น video stream แยกจาก API ภาพนิ่ง ภาพนิ่งที่อัปโหลดจะถูกเข้าคิวตามลำดับ และประมวลผลแบบ asynchronous เพื่อไม่ให้การ infer ภาพหนึ่งขัดขวางการรับภาพใหม่

## รายละเอียดโมดูล

### Training, dataset และ utilities

- `train.ipynb` — notebook หลักสำหรับตรวจ dataset, สร้าง DataLoader/augmentation, สร้าง U-Net/ResNet34 และ ConvNeXt-Tiny/UPerNet, จูน hyperparameters ด้วย Optuna, ฝึก final runs และวิเคราะห์ผล validation
- `training-requirements.txt` — แพ็กเกจที่ใช้ในงานฝึกและวิเคราะห์ผล แยกจาก dependencies ของเว็บ
- `scripts/make_manifest_v2.py` — สร้าง manifest รุ่นใหม่เพื่อจัดการภาพ UAV ที่ซ้ำหรือใกล้ซ้ำ โดยไม่ย้ายไฟล์ภาพ
- `scripts/phash_check.py` — คำนวณ perceptual hash และค้นหาคู่ภาพคล้าย/ซ้ำข้าม split หรือ source
- `reports/phash/` — outputs และรายงานการตรวจ near-duplicates ไม่ใช่ input runtime ของเว็บ
- `runs/` — ผล run เช่น `config.json`, `history.csv`, Optuna CSV, checkpoint และ artifacts สำหรับ evaluation/slide; ถูก ignore จาก Git

### Backend ของ Ground Station (`drone_inference/`)

- `app.py` — จุดเริ่ม FastAPI (`drone_inference.app:app`); สร้าง schema SQLite, หน้า dashboard เดิม, API ภาพ/รอบบิน, upload handling, inference queue/worker และ health/status endpoints
- `workspace_api.py` — เพิ่ม routes สำหรับ projects/workspace, รายการภาพ, การ review, report และ export โดย register router เข้ากับ app
- `pipeline.py` — `InferenceConfig`, `CrackPredictor` และ ConvNeXt-UPerNet; เลือก device, โหลด checkpoint, infer แบบ tile, รวม mask, คำนวณความยาว/ความกว้างใน pixel และเขียนภาพผลลัพธ์
- `load_model.py` — helper สำหรับอ่าน checkpoint และระบุ/เตรียม architecture ให้ตรงกับ weights
- `measurements.py` — ฟังก์ชันคำนวณค่าการวัดจาก mask แยกจาก HTTP/UI layer
- `config.example.yaml` — ตัวอย่างค่า checkpoint, device, threshold, tile size/overlap, cleanup และ data directory
- `requirements.txt` — dependencies ของ Ground Station/API/inference
- `jetson_sender.py` — watch-folder uploader สำหรับส่งภาพที่โปรแกรมกล้องบันทึกไว้ โดย retry เมื่อส่งไม่สำเร็จ

### หน้าเว็บ (`drone_inference/static/`)

- `index.html`, `app.js`, `style.css`, `overrides.css` — dashboard/review UI รุ่นเดิมและ interaction หลัก
- `projects.html`, `projects.js`, `projects.css` — หน้าเลือก/สร้าง project และ capture sessions
- `workspace.html`, `workspace.js`, `workspace.css` — หน้าภายใน project สำหรับ Overview, Images, Human Review และ Reports

หน้าเว็บเรียก API ของ FastAPI ผ่าน JavaScript; backend เป็นแหล่งข้อมูลหลัก ส่วนไฟล์ภาพและรายการ review ถูกเก็บแยกจากตัว UI

API หลักที่ใช้ใน workflow:

| Endpoint | หน้าที่ |
|---|---|
| `GET /api/health` | ตรวจสถานะ server/model |
| `POST /api/images` | รับไฟล์ภาพและ metadata จาก Jetson/ผู้ส่งภาพ |
| `GET /api/images/{image_id}` | ตรวจสถานะ queued/processing/done/failed |
| `GET /api/images` | รายการภาพและผล inference |
| `POST /api/flights/start` · `POST /api/flights/{id}/stop` | เปิด/ปิด capture session ใน Ground Station |
| `GET /api/workspace/projects` · `POST /api/projects` | อ่าน/สร้าง project |
| `GET /api/workspace/projects/{id}/images` | เรียกภาพใน project/session พร้อม filter/search |
| `PUT /api/workspace/projects/{id}/images/{session}/{image_id}/review` | บันทึกผล human review และ mask ที่แก้ |
| `GET /api/workspace/projects/{id}/export/{format}` | export ข้อมูล/รายงานตาม project และ session |

### Jetson (`drone_inference/jetson/`)

- `crack_agent.py` — เปิดกล้อง, ให้ live MJPEG, ถ่ายภาพนิ่งตาม interval, เก็บลง local spool, แนบ telemetry จาก MAVLink listener แบบอ่านอย่างเดียว และ retry upload ไป Ground Station
- `crack-agent.service` — ตัวอย่าง systemd unit สำหรับเริ่ม agent อัตโนมัติ
- `requirements.txt` — dependencies ที่จำเป็นบน Jetson แยกจากเครื่อง Ground Station
- `README.md` — ขั้นตอนติดตั้ง, ตั้ง IP, ตรวจ health/live, MAVProxy forwarding และ start agent

### Evaluation, benchmarking และ slide assets

- `evaluate_checkpoints.py` — ประเมิน checkpoint กับ manifest split; รองรับ smoke, validation threshold/TTA selection และ test; ส่งออก summary, per-source, per-class และ per-image CSV/JSON
- `benchmark_inference.py` — วัดเวลา inference/throughput บนภาพและ device ที่ระบุ; ใช้เปรียบเทียบได้เมื่อควบคุม hardware, batch/tile และ settings ให้เหมือนกัน
- `compare_uav.py` — สร้างภาพเปรียบเทียบ inference ของโมเดลบนภาพ UAV; ภาพไม่มี Ground Truth จึงเป็น qualitative output
- `make_test_examples.py` — ทำภาพตัวอย่างที่เทียบ prediction กับ Ground Truth จาก test set
- `make_test_slide.py` — จัดภาพตัวอย่าง test เป็น layout สำหรับนำเสนอ
- `make_uav_field_examples.py` — สร้างตัวอย่างผล inference จากภาพโดรนจริง โดยไม่มีการอ้าง metric เมื่อไม่มี Ground Truth
- `plot_optuna.py` — สร้าง plot จาก CSV ของ Optuna
- `width_distribution.py` — สรุป distribution ของความกว้างจากผล segmentation เพื่อสำรวจข้อมูล

### Tests (`drone_inference/tests/`)

- `test_workspace.py` — unit/API tests สำหรับ project, session, upload/review และ report behavior
- `browser_smoke.mjs` — เปิดหน้าเว็บใน browser แล้วตรวจ workflow หลัก
- `preview_server.py` — helper server ที่ browser smoke test ใช้ระหว่างทดสอบ

## การฝึกและประเมินโมเดล

โมเดลที่เปรียบเทียบคือ U-Net ที่ใช้ ResNet34 encoder และ ConvNeXt-Tiny ที่ใช้ UPerNet-style decoder. Run config จริงถูกบันทึกไว้ใต้โฟลเดอร์แต่ละ run ใน `runs/` เมื่อมีไฟล์ครบ

ผล validation ที่บันทึกไว้สำหรับ checkpoint สองตัว:

| โมเดล | Best epoch | IoU | Dice | Tolerance-F1 (±2 px) |
|---|---:|---:|---:|---:|
| U-Net / ResNet34 | 36 | 0.6374 | 0.7786 | 0.8662 |
| ConvNeXt-Tiny / UPerNet | 33 | 0.6469 | 0.7856 | 0.8728 |

ค่าข้างต้นเป็น **Validation results** ไม่ใช่ Test results. ใช้ Test set สำหรับรายงานผลสุดท้ายหลังตรึง checkpoint, threshold และ inference settings แล้วเท่านั้น

ตัวอย่างรัน evaluation จาก project root:

```bash
.venv/bin/python -m drone_inference.evaluate_checkpoints smoke --model all --limit 2
.venv/bin/python -m drone_inference.evaluate_checkpoints validation --model all
.venv/bin/python -m drone_inference.evaluate_checkpoints test --model all
```

ดู `--help` เพื่อเลือก dataset/output/device/batch-size และอ่าน docstring ใน `evaluate_checkpoints.py` ก่อนใช้ test mode เพราะ test output ที่มีอยู่แล้วจะไม่ถูกเขียนทับ และการเลือก threshold ต้องมาจาก validation ไม่ใช่ test

ตัวอย่าง benchmark:

```bash
.venv/bin/python -m drone_inference.benchmark_inference --help
```

เวลา/FPS ที่วัดบน Mac, NPU และ Jetson เปรียบเทียบตรงกันไม่ได้หาก hardware, input size, tile count, warm-up, TTA หรือ batch size ต่างกัน ให้บันทึก environment และ settings คู่กับผลทุกครั้ง

## ติดตั้งและเริ่ม Ground Station

จาก project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r drone_inference/requirements.txt
cp drone_inference/config.example.yaml drone_inference/config.yaml
```

แก้ `drone_inference/config.yaml` ให้ `checkpoint` ชี้ไปยัง weights ที่ต้องการ และตรวจ `device`, `threshold`, `tile_size`, `tile_overlap`, `min_component_px`, `live_stream_url` และ `data_dir` ก่อนเริ่มระบบ จากนั้นรัน:

```bash
uvicorn drone_inference.app:app --host 0.0.0.0 --port 8000
```

เข้า `http://localhost:8000/projects` บนเครื่องเดียวกัน หรือ `http://<GROUND_STATION_IP>:8000/projects` จากอุปกรณ์ใน LAN เดียวกัน

`config.yaml` เป็น local config และไม่ควร commit; ให้ใช้ `config.example.yaml` เป็น template แทน การเปลี่ยน checkpoint ให้เปลี่ยน path ใน config แล้ว restart server ตามวิธี deploy ปัจจุบัน

## การรับภาพจาก Jetson

1. เชื่อมกล้องเข้ากับ Jetson และตรวจชื่ออุปกรณ์/สิทธิ์เข้าถึงกล้อง
2. ให้ MAVProxy ส่ง telemetry มาที่ UDP listener ของ agent หากต้องการบันทึก telemetry; agent ไม่ได้เปิด serial Pixhawk เอง
3. รัน `crack_agent.py` โดยตั้ง `--endpoint http://<GROUND_STATION_IP>:8000`, กล้อง, interval และ MAVLink endpoint ตามการต่อจริง
4. ตรวจ `http://<JETSON_IP>:8080/health` และ `/live` ก่อนบิน
5. เปิด Ground Station แล้วเริ่ม capture session ใน project ที่ต้องการ
6. ภาพนิ่งถูกเก็บลง spool ที่ Jetson แล้วส่ง `POST /api/images`; ตรวจรายการภาพและสถานะ inference ในหน้า project
7. ตรวจผล/แก้ mask ใน Human Review แล้ว export ผ่าน Reports

ตัวอย่างส่งภาพเดี่ยวเข้า API:

```bash
curl -X POST http://<GROUND_STATION_IP>:8000/api/images \
  -F 'file=@sample.jpg' \
  -F 'metadata={"captured_at":"2026-10-14T08:30:00Z","latitude":14.0,"longitude":99.9}'
```

Metadata รองรับข้อมูลประกอบการถ่าย เช่น เวลา, พิกัด, telemetry/ความสูง, camera offset และ GSD เมื่อผู้ส่งคำนวณหรือวัดได้จริง อย่าส่งค่าที่คาดเดาเป็นค่าที่สอบเทียบแล้ว

## ข้อมูลที่ระบบจัดเก็บ

โดยทั่วไป `drone_inference/data/` ประกอบด้วย:

- `catalog.sqlite3` — catalog ของ projects/capture sessions
- `flight_<id>/inspection.sqlite3` — สถานะภาพ, queue, inference และ review ของ session
- `flight_<id>/originals/` — ภาพต้นฉบับที่รับจาก agent
- `flight_<id>/results/` — AI masks, overlays และข้อมูลผล inference
- `flight_<id>/reviews/` — mask/ข้อมูลที่แก้ไขจาก Human Review และประวัติการ review
- `_inbox/` หรือ `_idle/` — ภาพที่ยังไม่เข้า session ที่เปิดรับ หรือข้อมูลที่ยังจัด scope ไม่ได้

ชื่อ/ตำแหน่ง directory อาจต่างกันตามการตั้งค่า `data_dir` และรุ่น schema; ใช้ API และคู่มือใน `drone_inference/README.md` เป็นหลัก ไม่แก้ SQLite ด้วยมือขณะ server ทำงาน

## การทดสอบ

```bash
.venv/bin/python -m unittest discover -s drone_inference/tests -v
node drone_inference/tests/browser_smoke.mjs
```

Browser smoke test อาจเริ่ม preview server/browser ชั่วคราว โปรดปิด server ที่ใช้ port ซ้ำก่อนรัน หากต้องการตรวจ API อย่างเดียวให้เริ่มจาก Python unit tests

## ไฟล์ที่ไม่ควร commit

`.gitignore` กันข้อมูล/ไฟล์ขนาดใหญ่และ local state ออกจาก Git เช่น:

- `final_road_dataset_cleaned/`, `Dataset/` และไฟล์ `*.zip`
- `runs/`, `*.pt`, `*.pth`, `*.onnx`
- `drone_inference/data/` และ `drone_inference/config.yaml`
- CSV export ที่ match `inspection-*.csv` (อาจมีพิกัดหรือข้อมูลปฏิบัติการ)
- Python cache และ `.DS_Store`

หากต้องส่ง dataset/checkpoint ให้ใช้ storage สำหรับ artifact/data ที่ทีมกำหนด และบันทึกวิธีดาวน์โหลดหรือ checksum แทนการใส่ weights/ภาพจำนวนมากลง Git

## ข้อจำกัดและการตีความผล

- UAV inference ที่ไม่มี Ground Truth เป็นตัวอย่างเชิงคุณภาพ ไม่ใช่การวัด accuracy และใช้คำนวณ Dice/IoU ไม่ได้
- Barometer/EKF altitude ที่ส่งมากับภาพเป็น relative altitude พร้อม camera offset correction ไม่ได้ยืนยันระยะเหนือพื้น (AGL)
- ความยาว/ความกว้างจาก mask อยู่ในหน่วย pixel ก่อน; การแปลงเป็น mm ต้องมี GSD ที่สอบเทียบและเหมาะกับกล้อง/ความสูง/มุมมองนั้น
- การประเมินบน dataset หลายแหล่งไม่ได้รับประกันว่าจะครอบคลุมทุกผิวถนน แสง ความคมชัด มุมกล้อง และขนาดรอยแตกของสถานที่จริง
- Ground Station ไม่ได้ส่งคำสั่งบิน; ตรวจ failsafe และระบบควบคุมแยกต่างหากก่อนปฏิบัติการโดรนจริง
