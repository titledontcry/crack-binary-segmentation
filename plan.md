# Crack Binary Segmentation — Plan

แผนภาพ: `pipeline_v2.drawio` (เปิดด้วย app.diagrams.net หรือ draw.io desktop)
Dataset หลัก: `/Users/attachatchannakorn/Documents/crack-binary-segmentation/Dataset/final_merged_dataset_v1` ใช้ **`manifest_v2.csv`** (11,597 คู่ หลังตัดสำเนา UAV, 448×448, split 80/10/10)

เป้าหมายปลายทาง: โดรนบินถ่ายภาพอย่างเดียว → นำภาพมา **inference บนคอม** → ได้ mask, ความยาว, ความกว้างของรอยร้าว → แสดงใน Web app / Dashboard

---

## ✅ Progress Checklist

อัปเดตล่าสุด: 2026-09-27

### เสร็จแล้ว
- [x] สำรวจ dataset ทั้ง 3 folder → เลือกใช้ `final_merged_dataset_v1` (อีก 2 folder เป็น source ที่ merge เข้าไปแล้ว)
- [x] ตรวจ manifest ↔ ไฟล์จริง: ไม่มีไฟล์หาย, ไม่มีไฟล์กำพร้า, ไม่มี `sample_id` ซ้ำ
- [x] ตรวจรูปและ mask: ทุกรูป 448×448, mask เป็น grayscale ค่า {0, 255}, `crack_pixels` ใน manifest ตรงกับ mask จริงทั้ง 13,212 รูป
- [x] ตรวจการรั่วข้าม split ภายใน source: crop จากภาพต้นฉบับเดียวกัน (CRACK500, Rissbilder, Volker, noncrack) ไม่ข้าม split
- [x] สรุปสถิติ: crack pixel ~3.1%, mask ว่าง 10.5% (1,392 รูป), สัดส่วนแต่ละ source/class ใกล้เคียงกันทุก split
- [x] พบข้อสังเกต: รูป RGBA 4 รูป (`UAV_DJI*.png`), `.DS_Store` ในโฟลเดอร์, path ใน summary เป็นของเครื่องผู้สร้างเดิม
- [x] Review pipeline เดิม → แก้เป็น `pipeline_v2.drawio`
- [x] ตัดสินใจ: **inference บนคอม** (ไม่ทำบนโดรน)
- [x] เขียน `plan.md`
- [x] ตรวจ pHash ทั้ง dataset (`scripts/phash_check.py` → `reports/phash/`) → **พบ leakage ใน UAV_Crack_Detection** (รายละเอียดใน Phase 0)
- [x] แก้ leakage UAV_Crack_Detection (แบบ A: เก็บ 1 รูปต่อภาพ) → `manifest_v2.csv` (`scripts/make_manifest_v2.py`)

- [x] เขียน `train.ipynb` (notebook เดียว: config → data → aug → loader → sanity → model → loss/metrics → optimizer → train → val analysis → final test)
- [x] ทดสอบ notebook ครบทุก cell บน subset 3% × 2 epoch (รวม cell Final Test) → ผ่าน
  - บน Mac: `num_workers=0` (fork worker ค้างบน macOS), fp32 (fp16 บน MPS ช้ากว่า)
  - ความเร็ว M2: ~1.9 s/step (batch 8) → **~39 นาที/epoch**
  - weights ResNet34 โหลดเก็บใน HF cache แล้ว (เน็ตช้า/`hf-xet` ค้าง → โหลดด้วย curl)

### กำลังจะทำต่อ
- [ ] Train U-Net baseline เต็ม dataset (รอตัดสินใจจำนวน epoch)
- [ ] Validation analysis + เลือก threshold/TTA (Phase 4)

### ทำทีหลัง
- [ ] Drone field test set (Phase 9)

---

## สิ่งที่เปลี่ยนจาก pipeline เดิม

| จุด | เดิม | ใหม่ | เหตุผล |
|---|---|---|---|
| Dedup | pHash | SHA-256 (ทำแล้ว) + **pHash ข้าม source** | dataset ปัจจุบันทำแค่ SHA-256 ซึ่งจับรูปที่ re-encode/resize ไม่ได้ |
| Split | Split | **Group-aware split** (ตามภาพต้นฉบับ) | ตรวจแล้ว: crop จากภาพเดียวกันไม่ข้าม split |
| Tiling | อยู่ใน preprocessing | ย้ายไป **Inference** | รูป train เป็น 448×448 อยู่แล้ว tiling ใช้กับภาพโดรนเต็มขนาด |
| Feature Representation | กล่องแยก | ตัดออก → **Normalize (ImageNet mean/std)** | encoder เรียน feature เอง |
| U-Net | Baseline | Baseline + **ResNet34 encoder pretrained** | เทียบกับ ConvNeXt ได้ยุติธรรม |
| ConvNeXt-UPerNet | — | + **full-res head** | UPerNet ออก 1/4 res ไม่เหมาะกับรอยร้าวบาง 1–3 px |
| Loss | Dice-BCE | BCE + **batch-level** Dice | มี mask ว่าง 10.5% ทำให้ per-image Dice ไม่เสถียร |
| Evaluation | IoU, Dice, TTA (วนลูปได้) | **Validation** (วนลูปได้) แยกจาก **Final Test** (รันครั้งเดียว) | กัน overfit กับ test |
| Metrics | IoU, Dice | + P/R/F1, tolerance-F1, clDice, แยกตาม class/source | IoU ลงโทษการคลาด 1–2 px หนักเกินไป |
| Final output | Refine, skeleton + EDT | + ระบุ refine + **แปลง px → mm ด้วย GSD** | ต้องมีสเกลถึงจะวัดขนาดจริงได้ |
| Inference | — | **Batch inference บนคอม** (โดรนถ่ายภาพอย่างเดียว) | ใช้ภาพเต็มความละเอียด, โมเดลใหญ่ + TTA ได้, ไม่ต้องทำ TensorRT/edge |
| ใหม่ | — | **Drone field test set (ทำทีหลัง)** | วัด domain gap กับภาพโดรนจริง |

---

## Phase 0 — ตรวจ Dataset ให้ครบ

- [x] ตรวจ manifest ↔ ไฟล์, ขนาดรูป, ค่า mask, `crack_pixels`
- [x] ตรวจการรั่วข้าม split ภายใน source
- [x] **pHash ข้าม source** (รวม 8 แบบ rotate/flip, threshold ≤ 6): ผลอยู่ใน `reports/phash/`
  - **UAV_Crack_Detection**: 2,426 รูป จริง ๆ คือภาพต้นฉบับ ~794 ภาพ × 3 (ต้นฉบับ + flip ซ้าย-ขวา / flip บน-ล่าง / หมุน 180°) ยืนยันระดับพิกเซลแล้ว (MAE ≈ 0.8)
    - **383 กลุ่มกระจายข้าม split** → 1,188 รูปเกี่ยวข้อง, 485 รูปอยู่ใน val/test → **leakage จริง**
    - mask ของสำเนาในกลุ่มเดียวกันไม่ตรงกัน 100% (IoU ≈ 0.4, ถ้ายอมคลาด 2 px ตรงกัน ~0.9) และบางกลุ่ม label ต่างกันชัดเจน → มี label noise
    - รายชื่อกลุ่ม: `reports/phash/uav_clusters.csv`
  - CRACK500 / Rissbilder / Volker / noncrack: คู่ซ้ำอยู่ใน split เดียวกันทั้งหมด (0 ข้าม split) → ไม่มีปัญหา
  - ข้าม source (29 คู่): ตรวจด้วยตาแล้วเป็น false positive ทั้งหมด (แค่ layout คล้าย) → ไม่มีปัญหา
- [x] แก้ leakage (แบบ A) → `Dataset/final_merged_dataset_v1/manifest_v2.csv` (ใช้ไฟล์รูปเดิม ไม่ได้ copy/ลบไฟล์)
  - ยืนยันภาพซ้ำระดับพิกเซล (MAE < 3 หลัง flip/rotate) → UAV 2,426 → **811 รูป** (ตัด 1,615 สำเนา, รายชื่อใน `reports/phash/uav_dropped.csv`)
  - แบ่ง split UAV ใหม่ 80/10/10 ตามกลุ่ม pHash (stratify ตาม `crack_area_class`) → ไม่มีกลุ่มไหนข้าม split แล้ว
  - source อื่นใช้ split เดิม
  - รวม **11,597 คู่**: train 9,268 / validation 1,165 / test 1,164 (80/10/10) สัดส่วน class ใกล้เคียงกันทุก split
  - ⚠️ **ใช้คอลัมน์ `split` ใน manifest_v2 เป็นหลัก** ไม่ใช่ชื่อโฟลเดอร์ — มี 284 รูป UAV ที่ย้าย split แต่ไฟล์ยังอยู่ในโฟลเดอร์เดิม
  - ถ้าต้องการ UAV มากขึ้นตอน train → ใช้ weighted sampling แทน
- [ ] รูป RGBA 4 รูป → `.convert("RGB")` ตอนโหลด (จัดการใน Phase 1)

## Phase 1 — Data pipeline

- `Dataset` class โหลดจาก **`manifest_v2.csv`** และเลือก split จากคอลัมน์ `split` (ไม่ใช้ `os.listdir` เพราะชื่อไฟล์ image/mask คนละนามสกุล และมี `.DS_Store`)
- Mask: `/255` → float {0,1}
- Normalize ด้วย ImageNet mean/std
- Augmentation (Albumentations) เฉพาะ train:
  - Geometric: HorizontalFlip, VerticalFlip, RandomRotate90, ShiftScaleRotate เบา ๆ, RandomResizedCrop (scale 0.5–1.0)
  - Photometric: ColorJitter / RandomBrightnessContrast, CLAHE
  - **จำลองภาพโดรน**: MotionBlur, GaussianBlur, Downscale, ImageCompression (JPEG), GaussNoise, RandomShadow
  - mask ใช้ nearest interpolation เสมอ
- Sampler: เริ่มจาก random ปกติก่อน ถ้า recall ในกลุ่ม `small` ต่ำค่อยทำ weighted sampling ตาม `crack_area_class`

## Phase 2 — Models

| โมเดล | Encoder | หมายเหตุ |
|---|---|---|
| U-Net (baseline) | ResNet34, ImageNet pretrained | `segmentation_models_pytorch` |
| ConvNeXt-UPerNet | ConvNeXt-Tiny (ขยับเป็น Small/Base ได้), ImageNet pretrained | เพิ่ม full-res head: upsample ×4 + skip จาก stage แรก |

- ขนาด input 448 หาร 32 ลงตัว ใช้ได้ทั้งสองโมเดล
- เพราะ inference บนคอม ไม่มีข้อจำกัดเรื่องโมเดลต้องเล็ก → **เลือกโมเดลจาก accuracy เป็นหลัก** แต่ยังบันทึก params และเวลาต่อภาพไว้ประกอบการตัดสินใจ

## Phase 3 — Training

- Loss: `BCEWithLogits + (1 − batch Dice)` (น้ำหนัก 0.5/0.5 เป็นจุดเริ่ม)
  - ถ้า recall ต่ำ: ลอง `pos_weight`, Focal, Tversky (α=0.3, β=0.7)
- Optimizer: AdamW, lr encoder ต่ำกว่า decoder (เช่น 1e-4 / 1e-3), weight decay 1e-4
- Scheduler: cosine + warmup
- AMP (mixed precision), gradient clipping
- Early stopping / เลือก checkpoint ตาม **val F1 (หรือ Dice)**
- Seed คงที่, log ด้วย TensorBoard หรือ W&B, บันทึก config ทุก run

## Phase 4 — Validation (วนลูปได้)

ใช้ **validation set เท่านั้น** ในการปรับทุกอย่าง

- Metrics:
  - IoU, Dice, Precision, Recall, F1 (ระดับพิกเซล คิดรวมทั้ง dataset)
  - Tolerance-F1 (ยอมคลาด 2 px) และ clDice (ความต่อเนื่องของรอยร้าว)
  - False-positive rate บนรูป negative (mask ว่าง)
- แยกผลตาม `crack_area_class` และ `source_dataset` (โดยเฉพาะ UAV_Crack_Detection, UAV_SEG_2025 ซึ่งใกล้ภาพโดรนที่สุด)
- จูน threshold บน val (ไม่ fix ไว้ที่ 0.5)
- TTA (flip / rot90) → รายงานทั้งแบบมีและไม่มี TTA (บนคอมใช้ TTA ได้จริง)
- ถ้าผลยังไม่พอ → กลับไปปรับ augmentation / loss / sampling แล้ว train ใหม่

## Phase 5 — Final Test (ครั้งเดียว)

- Freeze: โมเดล, checkpoint, threshold, การใช้ TTA หรือไม่
- รันบน `test` ของ merged dataset **ครั้งเดียว** แล้วรายงานผล metric ชุดเดียวกับ Phase 4
- ตารางเทียบ U-Net vs ConvNeXt-UPerNet: accuracy + params + เวลาต่อภาพ

## Phase 6 — Batch inference บนคอม

Flow: โดรนบินถ่ายภาพ → ดึงภาพเข้าคอม (SD card / cloud sync) → รันสคริปต์ inference ทั้งโฟลเดอร์

- Input: โฟลเดอร์ภาพโดรนความละเอียดเต็ม (JPEG/DNG) พร้อม EXIF
- อ่าน **EXIF / telemetry** ของแต่ละภาพ: altitude, focal length, รุ่นกล้อง, GPS → ใช้คำนวณ GSD ใน Phase 7
- Sliding-window tiling: tile 448, overlap ~25%, blend ความน่าจะเป็นด้วย Gaussian/cosine weight แล้วต่อกลับเป็น mask ภาพเต็ม
- Batch หลาย tile พร้อมกันบน GPU, ใช้ TTA ได้ถ้าช่วยจริงบน val
- Output ต่อภาพ: probability map / binary mask + ไฟล์ JSON ผลวัด
- ถ้าภาพเยอะ: ทำ progress bar, resume ต่อจากภาพที่ค้างได้, ข้ามภาพที่ประมวลผลแล้ว

## Phase 7 — Post-processing (Final output)

1. Threshold ด้วยค่าที่จูนจาก val
2. Refine: ลบ connected component ที่เล็กกว่า N px, morphological closing เชื่อมช่องว่างเล็ก ๆ
3. Skeletonize → **ความยาว** (จำนวนพิกเซลบน skeleton, นับเส้นทแยงเป็น √2)
4. EDT → **ความกว้าง** = 2 × EDT ที่จุดบน skeleton (รายงาน max / mean / distribution)
5. แปลง px → mm ด้วย **GSD** = (sensor width × distance to surface) / (focal length × image width)
   - ถ้าถ่ายผนัง/สะพานในแนวราบ ต้องใช้ **ระยะถึงพื้นผิว** ไม่ใช่ altitude จาก GPS
6. Output: mask overlay, polyline ของรอยร้าว, ตารางความยาว/ความกว้าง (JSON)

> ⚠️ ข้อสังเกต: GT mask ของบาง source (เช่น CRACK500, Volker) หนากว่ารอยร้าวจริงมาก → ความกว้างจาก EDT จะ **overestimate** ถ้าไม่ calibrate กับการวัดจริง (ดู Phase 9)

## Phase 8 — Web app / Dashboard

- อัปโหลดโฟลเดอร์ภาพจากโดรน → เรียก batch inference (Phase 6–7) → แสดง overlay, ความยาว/ความกว้าง, ระดับความรุนแรง
- (ภายหลัง) ผูกกับพิกัด GPS ของแต่ละภาพ แสดงบนแผนที่

## Phase 9 — Drone field test set (ทำทีหลัง)

**ยังไม่ทำตอนนี้** — จดไว้เพื่อเตรียมตั้งแต่ตอนนี้

- เก็บภาพจากโดรนของเราเอง ทำ GT mask
- ใช้เป็น test set แยกจาก merged dataset ห้ามนำไป train หรือจูน (ถ้าอยากใช้ fine-tune ให้แบ่งเป็น train/test แยกกัน โดยแบ่งตามเที่ยวบินหรือสถานที่)
- ประเมินแบบ end-to-end บนภาพเต็ม (ผ่าน tiling ของ Phase 6) ไม่ใช่แค่ tile 448
- ใช้วัด domain gap: เทียบผลกับ Final Test ของ public set
- ถ้ามีระยะวัดจริงหน้างาน (วัดความกว้างรอยร้าวด้วย crack gauge) → ใช้ validate การแปลง px → mm
- สิ่งที่ควรเตรียมตั้งแต่ตอนนี้: เก็บภาพ **ไฟล์ต้นฉบับ** (ห้าม resize/ส่งผ่านแอปแชทที่บีบอัด), บันทึกระยะถ่าย, รุ่นกล้อง, มุมกล้อง ทุกเที่ยวบิน

---

## คำถามที่ยังต้องตัดสินใจ

1. **รุ่นโดรน / กล้อง** อะไร? — ต้องใช้ sensor width, focal length, ความละเอียดภาพ ในการคำนวณ GSD
2. ถ่าย **ระยะเท่าไหร่** จากพื้นผิว และถ่ายแนวดิ่ง (พื้นถนน) หรือแนวราบ (ผนัง/สะพาน)?
3. GPU ที่ใช้ train และ inference (local Mac / Colab / เครื่องที่มี CUDA)?
4. ใช้ metric ไหนเป็นตัวหลักในการเลือกโมเดล — เสนอ **F1 (tolerance 2 px)** บน val

## ลำดับงานถัดไป

1. `Dataset` / `DataLoader` + augmentation (Phase 1) พร้อมสคริปต์ visualize ตัวอย่าง
2. Train U-Net baseline ให้ครบลูปก่อน (train → val → metrics) แล้วค่อยเพิ่ม ConvNeXt-UPerNet
