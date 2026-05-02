"""
Benchmark: วิเคราะห์-เปรียบเทียบงานวิจัย Feature
Tests 4 scenarios (1 Thai, 1 English, 2 mixed, 3 mixed) against quality + latency gates.
Uses mock papers_context strings; calls the real OpenThaiGPT API via reviewer.py.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
from dotenv import load_dotenv
from reviewer import analyze_papers_critically_stream

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Thresholds ───────────────────────────────────────────────────────────────
MAX_LATENCY_S = 60
MIN_OUTPUT_CHARS = 500
REQUIRED_HEADERS = [
    "## วิเคราะห์แต่ละงานวิจัย",
    "## การเปรียบเทียบ",
    "## ช่องว่าง",
]
COMPARISON_MARKER = "|"  # Markdown table marker expected for ≥2 papers

# ── Mock paper contexts ───────────────────────────────────────────────────────
MOCK_THAI = """\
=== งานวิจัย: การประมวลผลภาษาไทยด้วย Transformer ===
บทคัดย่อ: งานวิจัยนี้ศึกษาการประยุกต์ใช้โมเดล Transformer สำหรับการวิเคราะห์ความรู้สึกในข้อความภาษาไทย
วัตถุประสงค์: เพื่อพัฒนาโมเดล NLP ที่มีประสิทธิภาพสูงสำหรับภาษาไทย โดยใช้ชุดข้อมูล Wisesight Sentiment
กรอบทฤษฎี: ใช้แนวคิด Transfer Learning และ Fine-tuning จากโมเดล WangchanBERTa
ระเบียบวิธีวิจัย: รูปแบบการวิจัยเชิงทดลอง กลุ่มตัวอย่างคือข้อความโซเชียลมีเดียภาษาไทย 40,000 ตัวอย่าง
เครื่องมือ: Python, HuggingFace Transformers, PyTorch เมตริกการประเมิน: F1-score, Accuracy
ผลการวิจัย: โมเดลที่พัฒนาได้ F1-score 0.89 บน Wisesight Sentiment เทียบเท่ากับ state-of-the-art
คุณค่าทางวิชาการ: เป็นการศึกษาแรกที่เปรียบเทียบ Transformer หลายสถาปัตยกรรมสำหรับภาษาไทยอย่างเป็นระบบ
ข้อจำกัด: ชุดข้อมูลมาจากโซเชียลมีเดียเท่านั้น ไม่ครอบคลุมภาษาไทยเชิงวิชาการหรือวรรณกรรม
"""

MOCK_ENGLISH = """\
=== งานวิจัย: Deep Learning for Medical Image Classification ===
Abstract: This study investigates convolutional neural networks (CNN) for automated diagnosis of diabetic retinopathy from fundus photographs.
Objectives: To develop a high-accuracy deep learning model for early detection of diabetic retinopathy, reducing dependency on specialist ophthalmologists.
Theoretical Framework: Based on ResNet-50 architecture with transfer learning from ImageNet pre-training.
Methodology: Experimental research design. Dataset: 88,702 retinal images from the EyePACS competition. Tools: TensorFlow, Keras. Metrics: AUC-ROC, Sensitivity, Specificity.
Key Findings: Achieved AUC of 0.97 for detecting referable diabetic retinopathy, surpassing the performance of general ophthalmologists (AUC 0.91).
Academic Contribution: First large-scale validation of CNN-based screening in a diverse multi-ethnic population dataset.
Limitations: Model trained primarily on US patient data; generalizability to Asian populations requires further validation.
"""

MOCK_PAPER_C = """\
=== งานวิจัย: RAG-Based Question Answering for Thai Legal Documents ===
บทคัดย่อ: งานวิจัยนี้พัฒนาระบบ Retrieval-Augmented Generation (RAG) สำหรับตอบคำถามจากเอกสารกฎหมายไทย
วัตถุประสงค์: สร้างระบบ QA ที่สามารถดึงข้อมูลจากประมวลกฎหมายไทยและตอบคำถามได้อย่างแม่นยำ
กรอบทฤษฎี: ผสมผสาน Dense Retrieval (DPR) และ Generative LLM (GPT-3.5) ในรูปแบบ RAG Pipeline
ระเบียบวิธีวิจัย: สร้างชุดข้อมูลคำถาม-คำตอบจากประมวลกฎหมายแพ่งและพาณิชย์ 500 คำถาม
เครื่องมือ: LangChain, Pinecone, OpenAI API ประเมินด้วย Exact Match และ F1-score
ผลการวิจัย: ระบบ RAG ทำได้ F1 0.78 เทียบกับ baseline LLM ที่ 0.51 (เพิ่มขึ้น 53%)
คุณค่าทางวิชาการ: แสดงให้เห็นว่า RAG ช่วยลด hallucination ได้อย่างมีนัยสำคัญในบริบทกฎหมาย
ข้อจำกัด: ครอบคลุมเฉพาะกฎหมายแพ่งและพาณิชย์ ยังไม่ทดสอบกับกฎหมายอาญาหรือภาษีอากร
"""

# ── Test scenarios ────────────────────────────────────────────────────────────
SCENARIOS = [
    {
        "id": 1,
        "name": "1 Thai paper",
        "context": MOCK_THAI,
        "papers": ["การประมวลผลภาษาไทยด้วย Transformer"],
        "require_table": False,
    },
    {
        "id": 2,
        "name": "1 English paper",
        "context": MOCK_ENGLISH,
        "papers": ["Deep Learning for Medical Image Classification"],
        "require_table": False,
    },
    {
        "id": 3,
        "name": "2 papers (Thai + English)",
        "context": MOCK_THAI + "\n\n" + MOCK_ENGLISH,
        "papers": [
            "การประมวลผลภาษาไทยด้วย Transformer",
            "Deep Learning for Medical Image Classification",
        ],
        "require_table": True,
    },
    {
        "id": 4,
        "name": "3 papers (mixed)",
        "context": MOCK_THAI + "\n\n" + MOCK_ENGLISH + "\n\n" + MOCK_PAPER_C,
        "papers": [
            "การประมวลผลภาษาไทยด้วย Transformer",
            "Deep Learning for Medical Image Classification",
            "RAG-Based Question Answering for Thai Legal Documents",
        ],
        "require_table": True,
    },
]


def check_quality(output: str, scenario: dict) -> tuple[bool, list[str]]:
    """Return (passed, list_of_failures)."""
    failures = []

    if len(output) < MIN_OUTPUT_CHARS:
        failures.append(f"Output too short: {len(output)} chars (min {MIN_OUTPUT_CHARS})")

    for header in REQUIRED_HEADERS:
        if header not in output:
            failures.append(f"Missing section: {header!r}")

    if scenario["require_table"] and COMPARISON_MARKER not in output:
        failures.append("Missing comparison table (no '|' marker found)")

    # Check no phantom paper names (papers not in the scenario appear in output)
    all_paper_names = [
        "การประมวลผลภาษาไทยด้วย Transformer",
        "Deep Learning for Medical Image Classification",
        "RAG-Based Question Answering for Thai Legal Documents",
    ]
    expected = set(scenario["papers"])
    for name in all_paper_names:
        if name not in expected and name in output:
            failures.append(f"Phantom paper name in output: {name!r}")

    return len(failures) == 0, failures


def run_scenario(scenario: dict) -> dict:
    print(f"\n{'='*60}")
    print(f"Test {scenario['id']}: {scenario['name']}")
    print(f"{'='*60}")

    start = time.time()
    tokens = []
    try:
        for chunk in analyze_papers_critically_stream(
            scenario["context"], scenario["papers"]
        ):
            tokens.append(chunk)
        output = "".join(tokens)
    except Exception as e:
        elapsed = time.time() - start
        return {
            "id": scenario["id"],
            "name": scenario["name"],
            "passed": False,
            "latency_s": round(elapsed, 1),
            "output_chars": 0,
            "failures": [f"API error: {e}"],
        }

    elapsed = time.time() - start
    passed_quality, quality_failures = check_quality(output, scenario)
    latency_ok = elapsed <= MAX_LATENCY_S
    if not latency_ok:
        quality_failures.append(
            f"Latency too high: {elapsed:.1f}s (max {MAX_LATENCY_S}s)"
        )

    all_passed = passed_quality and latency_ok

    status = "PASS" if all_passed else "FAIL"
    print(f"Status   : {status}")
    print(f"Latency  : {elapsed:.1f}s")
    print(f"Output   : {len(output)} chars")
    if quality_failures:
        print("Failures :")
        for f in quality_failures:
            print(f"  - {f}")
    else:
        print("All quality gates passed")

    return {
        "id": scenario["id"],
        "name": scenario["name"],
        "passed": all_passed,
        "latency_s": round(elapsed, 1),
        "output_chars": len(output),
        "failures": quality_failures,
    }


def main():
    print("Benchmark: วิเคราะห์-เปรียบเทียบงานวิจัย")
    print(f"Thresholds: latency<{MAX_LATENCY_S}s, output>{MIN_OUTPUT_CHARS} chars")

    results = []
    for scenario in SCENARIOS:
        result = run_scenario(scenario)
        results.append(result)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    passed_count = sum(1 for r in results if r["passed"])
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  [{mark}] Test {r['id']}: {r['name']} — {r['latency_s']}s, {r['output_chars']} chars")

    print(f"\nResult: {passed_count}/{len(results)} tests passed")
    if passed_count < len(results):
        print("\nFailed tests:")
        for r in results:
            if not r["passed"]:
                print(f"  Test {r['id']}: {r['name']}")
                for f in r["failures"]:
                    print(f"    - {f}")
        sys.exit(1)
    else:
        print("All tests PASSED")


if __name__ == "__main__":
    main()
