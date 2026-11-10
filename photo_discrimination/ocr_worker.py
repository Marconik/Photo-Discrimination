"""Executed only inside an opt-in OCR venv; writes JSON events to stdout."""

import argparse
import json
import sys
import traceback
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from photo_discrimination.ocr_runtime import OcrChoice, model_ready


def emit(kind, **values):
    print("PD_EVENT:" + json.dumps({"type": kind, **values}, ensure_ascii=False), flush=True)


def make_pipeline(choice, root):
    import paddle
    from paddleocr import PaddleOCR

    det, rec = choice.model_names()
    for name in (det, rec):
        if not model_ready(root / "models" / name):
            raise RuntimeError(f"模型不完整：{name}；请先在设置中下载。")
    device = "gpu:0" if choice.device == "gpu" else "cpu"
    if choice.device == "gpu":
        if not paddle.is_compiled_with_cuda() or paddle.device.cuda.device_count() < 1:
            raise RuntimeError("没有可用的 CUDA GPU；请核对手动选择的版本与显卡驱动。")
    paddle.set_device(device)
    # Force an actual allocation/operation on the requested device.
    if float(paddle.sum(paddle.ones([4], dtype="float32")).numpy()) != 4:
        raise RuntimeError("设备计算验证失败。")
    pipeline = PaddleOCR(
        text_detection_model_name=det,
        text_detection_model_dir=str(root / "models" / det),
        text_recognition_model_name=rec,
        text_recognition_model_dir=str(root / "models" / rec),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_recognition_batch_size=1,
        text_det_limit_side_len=960,
        text_det_limit_type="max",
        enable_mkldnn=False,
        use_tensorrt=False,
        enable_hpi=False,
        device=device,
    )
    return pipeline, paddle.get_device()


def texts_from(pipeline, image):
    return [str(text) for result in pipeline.predict(image) for text in result.get("rec_texts", [])]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--choice", required=True)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--request", type=Path)
    args = parser.parse_args()
    choice = OcrChoice(**json.loads(args.choice))
    choice.validate()
    pipeline, device = make_pipeline(choice, args.root)
    if args.probe:
        import cv2
        import numpy as np
        image = np.full((180, 640, 3), 255, dtype=np.uint8)
        cv2.putText(image, "OCR 123", (25, 120), cv2.FONT_HERSHEY_SIMPLEX, 2.5, (0, 0, 0), 4)
        texts = texts_from(pipeline, image)
        if not texts:
            raise RuntimeError("模型能加载，但没有通过文字样本的实际推理验证。")
        import paddle
        emit("probe", ok=True, device=device, texts=texts, cuda=paddle.version.cuda(),
             paddle=version("paddlepaddle-gpu" if choice.device == "gpu" else "paddlepaddle"),
             paddleocr=version("paddleocr"), paddlex=version("paddlex"))
        return
    if not args.request:
        raise RuntimeError("缺少图片任务。")
    import numpy as np
    from PIL import Image, ImageOps
    photos = json.loads(args.request.read_text(encoding="utf-8"))["photos"]
    emit("ready", device=device, total=len(photos))
    for number, photo in enumerate(photos, 1):
        try:
            # PIL supports non-ASCII Windows paths, and keeps EXIF orientation correct.
            with Image.open(photo["path"]) as original:
                image = np.asarray(ImageOps.exif_transpose(original).convert("RGB"))[:, :, ::-1].copy()
            text = "".join("".join(texts_from(pipeline, image)).split())
            emit("photo", name=photo["name"], text=text, number=number, total=len(photos), device=device)
        except Exception as exc:
            emit("photo_error", name=photo["name"], error=str(exc), number=number, total=len(photos))
    emit("done")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("fatal", error=str(exc))
        traceback.print_exc()
        raise SystemExit(1)
