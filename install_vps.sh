#!/bin/bash
# ============================================================
#  VPS Setup Script — UIDAI-Gram Bot
#  Run once:  bash install_vps.sh
# ============================================================

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Installing system dependencies..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Tesseract OCR binary (required for pytesseract)
apt-get update -qq
apt-get install -y tesseract-ocr tesseract-ocr-eng libgl1 libglib2.0-0

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Installing Python packages..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

pip install -r requirements.txt

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Verifying OCR engines..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

python3 -c "
import ddddocr; d = ddddocr.DdddOcr(show_ad=False); print('ddddocr  : OK')
import pytesseract; print('tesseract:', pytesseract.get_tesseract_version())
"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Done! Start bot with:  python3 bot_final.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
