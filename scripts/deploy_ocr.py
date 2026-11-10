"""Opt-in CLI equivalent of the Download and deploy button."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from photo_discrimination.ocr_runtime import ROOT, OcrChoice, deploy, save_active


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "gpu"), required=True)
    parser.add_argument("--variant", choices=("cu118", "cu126", "cu129"), default="")
    parser.add_argument("--model", choices=("mobile", "server"), default="mobile")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--use-installed-packages", action="store_true", help="Skip pip; still verify exact versions and actual inference")
    args = parser.parse_args()
    choice = OcrChoice(args.device, args.variant, args.model)
    choice.validate()
    deploy(choice, ROOT, lambda line: print(line, flush=True), reuse_packages=args.use_installed_packages)
    if args.activate:
        save_active(choice)
        print("已启用此 OCR 配置。")
