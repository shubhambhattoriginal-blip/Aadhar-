import requests
import json
import base64
import uuid
import re
from datetime import datetime
import os
import sys
import time
import logging
import random
import string
import threading
from io import BytesIO
from urllib.parse import urlparse
import PyPDF2
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============== OCR ENGINE PRE-LOAD ==============
try:
    import ddddocr as _ddddocr
    _DDDD_MAIN = _ddddocr.DdddOcr(show_ad=False)
    try:
        _DDDD_BETA = _ddddocr.DdddOcr(show_ad=False, beta=True)
    except Exception:
        _DDDD_BETA = None
    _DDDD_OK = True
except Exception as _e:
    _DDDD_MAIN = _DDDD_BETA = None
    _DDDD_OK = False
    print(f"[WARN] ddddocr not available: {_e}")

try:
    import pytesseract as _pytesseract
    _pytesseract.get_tesseract_version()   # will raise if binary missing
    _TESS_OK = True
except Exception as _e:
    _TESS_OK = False
    print(f"[WARN] pytesseract/tesseract not available: {_e}")

# ============== LOGGING SETUP ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============== BOT TOKEN ==============
TELEGRAM_BOT_TOKEN = "8768801941:AAFA19gM5YQYZyA4tUFBeeVIy6FHYTAySYc"

# ============== PROXY CONFIGURATION ==============
TELEGRAM_PROXY = None
UIDAI_PROXY = "http://117.236.124.166:3128"   # working default; can be changed via /ip

# ============== SESSION FACTORY ==============
def create_session(use_proxy=False, proxy_string=None):
    session = requests.Session()
    session.mount('https://', requests.adapters.HTTPAdapter(
        pool_connections=5, pool_maxsize=5, max_retries=3, pool_block=False
    ))
    if use_proxy and proxy_string:
        parsed = urlparse(proxy_string)
        proxy_url = f"{parsed.scheme}://{parsed.netloc}"
        session.proxies = {'http': proxy_url, 'https': proxy_url}
        logger.info(f"Session proxy set: {proxy_url}")
    else:
        logger.info("No proxy (direct connection)")
    return session

telegram_session = None
def get_telegram_session():
    global telegram_session
    if telegram_session is None:
        telegram_session = create_session(True, TELEGRAM_PROXY)
        logger.info("Telegram session created with proxy")
    return telegram_session

uidai_session = None
def get_uidai_session():
    global uidai_session
    if uidai_session is None:
        uidai_session = create_session(True, UIDAI_PROXY)
        logger.info(f"UIDAI session created with proxy: {UIDAI_PROXY}")
    return uidai_session

def set_uidai_proxy(new_proxy):
    global UIDAI_PROXY, uidai_session, bot
    UIDAI_PROXY = new_proxy
    uidai_session = create_session(True, new_proxy)
    bot.session = uidai_session
    bot.session.headers.update(bot.base_headers)
    logger.info(f"UIDAI proxy updated to {new_proxy}")

# ============== PDF PASSWORD CRACKER ==============
class PDFPasswordCracker:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.found_password = None
        self.stop_flag = False
        self.progress = 0
        self.total_years = 0

    def try_password(self, pdf_path, password):
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                if pdf_reader.decrypt(password):
                    return True, password
                return False, None
        except Exception as e:
            logger.debug(f"Error with password {password}: {e}")
            return False, None

    def decrypt_pdf(self, pdf_path, password, output_path=None):
        try:
            if output_path is None:
                output_path = pdf_path.replace('.pdf', '_decrypted.pdf')
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                pdf_reader.decrypt(password)
                pdf_writer = PyPDF2.PdfWriter()
                for page in pdf_reader.pages:
                    pdf_writer.add_page(page)
                with open(output_path, 'wb') as output_file:
                    pdf_writer.write(output_file)
            logger.info(f"Decrypted PDF saved: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Error decrypting PDF: {e}")
            return None

    def crack_pdf(self, pdf_path, name, progress_callback=None):
        self.found_password = None
        self.stop_flag = False
        self.progress = 0
        name_upper = name.upper()
        patterns = []
        name_prefix = name_upper[:4] if len(name_upper) >= 4 else name_upper
        patterns.append(('first4', name_prefix))
        if len(name_upper) >= 6:
            patterns.append(('first6', name_upper[:6]))
        name_full = name_upper[:10] if len(name_upper) > 10 else name_upper
        patterns.append(('full', name_full))
        patterns.append(('lower_first4', name_prefix.lower()))
        if len(name_upper) >= 6:
            patterns.append(('lower_first6', name_upper[:6].lower()))
        patterns.append(('title_first4', name_prefix.title()))
        patterns.append(('first4_short', name_prefix[:4]))
        patterns.append(('with_at', f"{name_prefix}@"))
        patterns.append(('with_hash', f"{name_prefix}#"))
        patterns.append(('with_exclaim', f"{name_prefix}!"))
        patterns.append(('year_first', "@"))
        patterns.append(('only_name', name_prefix))
        current_year = datetime.now().year
        common_years = list(range(1940, 2010)) + list(range(1930, 1940)) + list(range(2010, current_year + 1))
        prioritized_passwords = []
        for year in common_years:
            for pattern_name, prefix in patterns:
                if pattern_name == 'year_first':
                    password = f"{year}{prefix}"
                elif pattern_name == 'only_name':
                    password = prefix
                elif pattern_name == 'first4_short':
                    password = f"{prefix[:4]}{year}"
                elif pattern_name == 'with_at':
                    password = f"{prefix}@{year}"
                elif pattern_name == 'with_hash':
                    password = f"{prefix}#{year}"
                elif pattern_name == 'with_exclaim':
                    password = f"{prefix}!{year}"
                else:
                    password = f"{prefix}{year}"
                prioritized_passwords.append(password)
        seen = set()
        unique_passwords = []
        for pwd in prioritized_passwords:
            if pwd not in seen:
                seen.add(pwd)
                unique_passwords.append(pwd)
        checked = 0
        batch_size = 20
        for i in range(0, len(unique_passwords), batch_size):
            if self.stop_flag:
                break
            batch = unique_passwords[i:i+batch_size]
            futures = [(self.executor.submit(self.try_password, pdf_path, p), p) for p in batch]
            for future, password in futures:
                if self.stop_flag:
                    break
                try:
                    success, found_pwd = future.result(timeout=2)
                    checked += 1
                    if success:
                        self.found_password = found_pwd
                        self.stop_flag = True
                        decrypted_path = self.decrypt_pdf(pdf_path, found_pwd)
                        return True, found_pwd, decrypted_path if decrypted_path else None
                except Exception as e:
                    logger.debug(f"Error checking password {password}: {e}")
                    continue
        no_year_passwords = [prefix for pattern_name, prefix in patterns if pattern_name not in ['only_name']]
        for password in no_year_passwords:
            if self.stop_flag:
                break
            success, found_pwd = self.try_password(pdf_path, password)
            if success:
                self.found_password = found_pwd
                self.stop_flag = True
                decrypted_path = self.decrypt_pdf(pdf_path, found_pwd)
                return True, found_pwd, decrypted_path if decrypted_path else None
        return False, None, None

# ============== AADHAAR BOT CLASS ==============
class AadhaarBot:
    def __init__(self):
        self.session = get_uidai_session()
        self.base_headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en_IN',
            'Connection': 'keep-alive',
            'Content-Type': 'application/json',
            'Origin': 'https://myaadhaar.uidai.gov.in',
            'Referer': 'https://myaadhaar.uidai.gov.in/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
            'appid': 'MYAADHAAR',
            'sec-ch-ua': '"Not-A.Brand";v="99", "Chromium";v="124"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"',
        }
        self.session.headers.update(self.base_headers)
        logger.info("AadhaarBot initialized")
        self.cracker = PDFPasswordCracker()

    def generate_transaction_id(self):
        return str(uuid.uuid4())

    def is_base64(self, s):
        if not isinstance(s, str) or len(s) < 100:
            return False
        if s.startswith('data:'):
            s = s.split(',')[1] if ',' in s else s
        if len(s) % 4 != 0:
            return False
        try:
            base64.b64decode(s)
            return True
        except:
            return False

    def detect_file_type(self, file_bytes):
        if file_bytes[:4] == b'%PDF':
            return 'pdf'
        elif file_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            return 'png'
        elif file_bytes[:2] == b'\xff\xd8':
            return 'jpg'
        return 'unknown'

    def detect_and_decode_base64(self, data, field_name="unknown", save=False):
        decoded_items = []
        if isinstance(data, dict):
            for key, value in list(data.items()):
                if isinstance(value, str) and len(value) > 100 and self.is_base64(value):
                    try:
                        clean_base64 = value.split(',')[1] if value.startswith('data:') and ',' in value else value
                        decoded_bytes = base64.b64decode(clean_base64)
                        file_type = self.detect_file_type(decoded_bytes)
                        if save and file_type in ['pdf', 'png', 'jpg']:
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            ext = {'pdf': 'pdf', 'png': 'png', 'jpg': 'jpg'}.get(file_type, 'bin')
                            filename = f"decoded_{field_name}_{key}_{timestamp}.{ext}"
                            with open(filename, 'wb') as f:
                                f.write(decoded_bytes)
                            decoded_items.append({'field': key, 'filename': filename, 'type': file_type, 'size': len(decoded_bytes), 'data': decoded_bytes})
                            logger.info(f"Saved: {filename}")
                        elif not save:
                            decoded_items.append({'field': key, 'type': file_type, 'size': len(decoded_bytes), 'data': decoded_bytes})
                    except Exception as e:
                        logger.error(f"Base64 decode error: {e}")
                if isinstance(value, (dict, list)):
                    decoded_items.extend(self.detect_and_decode_base64(value, f"{field_name}.{key}", save))
        elif isinstance(data, list):
            for idx, item in enumerate(data):
                if isinstance(item, (dict, list)):
                    decoded_items.extend(self.detect_and_decode_base64(item, f"{field_name}[{idx}]", save))
        return decoded_items

    def get_captcha(self, user_id):
        transaction_id = self.generate_transaction_id()
        self.session.headers.update({'x-request-id': transaction_id, 'transactionId': transaction_id})
        captcha_data = {'captchaLength': '6', 'captchaType': '2', 'audioCaptchaRequired': True}
        try:
            response = self.session.post(
                'https://tathya.uidai.gov.in/audioCaptchaService/api/captcha/v3/generation',
                json=captcha_data, timeout=15
            )
            if response.status_code != 200:
                return None, None, None
            resp_json = response.json()
            captcha_txn_id = resp_json.get('transactionId')
            captcha_base64 = resp_json.get('imageBase64')
            if not captcha_base64:
                for key, value in resp_json.items():
                    if isinstance(value, str) and len(value) > 100 and self.is_base64(value):
                        captcha_base64 = value
                        break
            if not captcha_base64:
                return None, None, None
            if captcha_base64.startswith('data:image'):
                captcha_base64 = captcha_base64.split(',')[1]
            image_bytes = base64.b64decode(captcha_base64)
            return image_bytes, captcha_txn_id, transaction_id
        except Exception as e:
            logger.error(f"Error getting captcha: {str(e)}")
            return None, None, None

    def send_aadhaar_otp(self, user_id, number, captcha_value, captcha_txn_id, transaction_id, id_type='eid'):
        self.session.headers.update({'x-request-id': transaction_id, 'transactionId': transaction_id})
        key = 'eidNumber' if id_type == 'eid' else 'uidNumber'
        otp_request_data = {
            key: number,
            'idType': id_type,
            'captchaTxnId': captcha_txn_id,
            'captchaValue': captcha_value,
            'transactionId': transaction_id,
            'resendOTP': False
        }
        try:
            response = self.session.post(
                'https://tathya.uidai.gov.in/unifiedAppAuthService/api/v2/generate/aadhaar/otp',
                json=otp_request_data, timeout=15
            )
            if response.status_code == 200:
                resp_json = response.json()
                otp_txn_id = resp_json.get('txnId')
                status = resp_json.get('status')
                message = resp_json.get('message')
                if otp_txn_id and status == "Success":
                    return True, otp_txn_id, message
                else:
                    return False, None, message
            else:
                return False, None, f"HTTP {response.status_code}"
        except Exception as e:
            return False, None, str(e)

    def download_aadhaar_pdf(self, user_id, number, otp, otp_txn_id, transaction_id, mask=False, id_type='eid'):
        self.session.headers.update({'x-request-id': transaction_id, 'transactionId': transaction_id})
        key = 'eid' if id_type == 'eid' else 'uid'
        download_data = {
            key: number,
            'mask': mask,
            'otp': otp,
            'otpTxnId': otp_txn_id
        }
        try:
            response = self.session.post(
                'https://tathya.uidai.gov.in/downloadAadhaarService/api/aadhaar/download',
                json=download_data, timeout=20
            )
            if response.status_code == 200:
                resp_json = response.json()
                decoded_files = self.detect_and_decode_base64(resp_json, "aadhaar_download", save=True)
                if decoded_files:
                    return True, decoded_files[0]['filename']
                else:
                    if resp_json.get('status') == 'Error' or resp_json.get('errorCode'):
                        error_msg = resp_json.get('message', resp_json.get('errorMessage', 'Unknown error'))
                        return False, error_msg
                    else:
                        return False, "No PDF data found"
            else:
                return False, f"HTTP {response.status_code}"
        except Exception as e:
            return False, str(e)

    def send_eid_otp(self, user_id, mobile, name, captcha_code, captcha_txn_id, transaction_id):
        self.session.headers.update({'x-request-id': transaction_id, 'transactionId': transaction_id})
        request_data = {
            'mobileNumber': mobile, 'dob': None, 'email': None,
            'name': name.upper(), 'option': 'EID', 'otp': None,
            'otpTxnId': None, 'captchaTxnId': captcha_txn_id,
            'captcha': captcha_code, 'resendOtp': False
        }
        try:
            response = self.session.post(
                'https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',
                json=request_data, timeout=15
            )
            if response.status_code == 200:
                resp_json = response.json()
                if 'responseData' in resp_json:
                    response_data = resp_json['responseData']
                    otp_txn_id = response_data.get('otpTxnId')
                    status = response_data.get('status')
                    if otp_txn_id and status == "Success":
                        return True, otp_txn_id
                    else:
                        return False, response_data.get('message', 'Unknown error')
                else:
                    return False, 'Invalid response'
            else:
                return False, f'HTTP {response.status_code}'
        except Exception as e:
            return False, str(e)

    def verify_eid_otp(self, user_id, mobile, name, otp_code, otp_txn_id, captcha_txn_id, captcha_code):
        self.session.headers.update({'x-request-id': self.generate_transaction_id()})
        verify_data = {
            'mobileNumber': mobile, 'dob': None, 'name': name.upper(),
            'email': None, 'option': 'EID', 'otp': otp_code,
            'otpTxnId': otp_txn_id, 'captchaTxnId': captcha_txn_id,
            'captcha': captcha_code, 'resendOtp': False
        }
        try:
            response = self.session.post(
                'https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',
                json=verify_data, timeout=15
            )
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get('status') == 200 or resp_json.get('status') == "Success":
                    if 'responseData' in resp_json:
                        response_data = resp_json['responseData']
                        eid_number = response_data.get('eidNumber')
                        name_from_response = response_data.get('name', name)
                        if eid_number:
                            return True, eid_number, name_from_response
                        else:
                            return False, None, "No EID found"
                    else:
                        return False, None, "Invalid response"
                else:
                    error_msg = resp_json.get('errorDetails', {}).get('messageEnglish', 'Verification failed')
                    return False, None, error_msg
            else:
                return False, None, f'HTTP {response.status_code}'
        except Exception as e:
            return False, None, str(e)

    def crack_pdf_with_name(self, pdf_path, name, progress_callback=None):
        success, password, decrypted_path = self.cracker.crack_pdf(pdf_path, name, progress_callback)
        return success, password, decrypted_path, None

    def auto_solve_captcha(self, image_bytes):
        """
        Fast & accurate UIDAI captcha solver.
        ddddocr works best on raw/lightly-processed bytes — no heavy preprocessing.
        Strategy:
          1. ddddocr_main  on raw bytes          → return immediately if valid
          2. ddddocr_beta  on raw bytes          → return immediately if valid
          3. ddddocr_main  on 2× upscaled gray   → return if valid
          4. ddddocr_beta  on 2× upscaled gray   → return if valid
        Total: max 4 fast neural-net calls (vs old 60 calls).
        """
        try:
            import re, io
            from PIL import Image

            if not _DDDD_OK:
                logger.error("ddddocr not available.")
                return None

            def _clean(r):
                # Keep original case — UIDAI captcha is case-sensitive (e.g. '435e5e')
                return re.sub(r'[^A-Za-z0-9]', '', r)

            def _dddd(b):
                """Try both ddddocr engines on raw bytes, return first valid result."""
                for engine in [_DDDD_MAIN, _DDDD_BETA]:
                    if not engine:
                        continue
                    try:
                        r = _clean(engine.classification(b))
                        if 4 <= len(r) <= 8:
                            return r
                    except Exception:
                        pass
                return None

            # Attempt 1: raw bytes (fastest, most natural for ddddocr)
            result = _dddd(image_bytes)
            if result:
                logger.info(f"Captcha → '{result}' (raw)")
                return result

            # Attempt 2: 2× upscale grayscale (helps when image is small/blurry)
            try:
                img = Image.open(io.BytesIO(image_bytes)).convert('L')
                img2x = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
                buf = io.BytesIO()
                img2x.save(buf, format='PNG')
                result = _dddd(buf.getvalue())
                if result:
                    logger.info(f"Captcha → '{result}' (2x gray)")
                    return result
            except Exception:
                pass

            logger.warning("auto_solve_captcha: no valid result from ddddocr")
            return None

        except Exception as e:
            logger.error(f"Auto-captcha solve error: {e}")
            return None

    def fetch_and_auto_solve_captcha(self, chat_id, max_retries=20):
        """Fetch a fresh captcha each attempt. 20 retries, multi-engine solver.
        Returns solved_text=None only when all 20 attempts fail."""
        last_img = last_txn = last_tid = None
        for attempt in range(max_retries):
            image_bytes, captcha_txn_id, transaction_id = self.get_captcha(chat_id)
            if not image_bytes:
                logger.warning(f"Captcha fetch failed {attempt+1}/{max_retries}")
                time.sleep(0.4)
                continue
            last_img, last_txn, last_tid = image_bytes, captcha_txn_id, transaction_id
            solved = self.auto_solve_captcha(image_bytes)
            if solved and 4 <= len(solved) <= 8:
                return image_bytes, captcha_txn_id, transaction_id, solved
            logger.warning(f"Weak/no result '{solved}' attempt {attempt+1}/{max_retries}")
            time.sleep(0.2)
        return last_img, last_txn, last_tid, None

# ============================================================
# Initialize bot
# ============================================================
bot = AadhaarBot()

# ============== AUTO CAPTCHA HELPERS ==============

def auto_send_eid_otp(chat_id, mobile, name, d):
    """Auto-solve captcha and send EID OTP.
    Flow:
      1. Try with user's name (up to 5 attempts)
      2. If name gives definitive 'no record' → silently retry with 'MR' (up to 10 attempts)
      3. If MR succeeds → OTP sent, user never knows name failed
      4. If MR also gets definitive error → return 'no_record'
      5. If MR fails due to server/captcha issues → show manual captcha
    Returns:
      (True,  session_dict)       — success
      (False, (img,txn,tid))      — show manual captcha
      ('no_record', error_msg)    — number not linked with any Aadhaar
    """
    send_message(chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Sending OTP 〕</b>\n\n"
        f"<i>◌  Please wait…</i>"
    )

    RETRY_KEYS = ('captcha', 'mismatch', 'timeout', 'timed out', 'time out',
                  'technical', 'invalid response', 'invalid captcha',
                  'try again', 'server error', 'connection', '500', '502', '503', '504')
    BREAK_KEYS = ('no record', 'not found', 'not registered', 'not linked',
                  'no aadhaar', 'not exist', 'does not exist', 'name mismatch',
                  'dob mismatch', 'mobile not')

    last_img = last_txn = last_tid = None

    def _try_name(try_name, max_attempts):
        """Returns: ('ok', sd) | ('no_record', msg) | ('retry_fail', None)
        Only returns 'no_record' after 3 consecutive BREAK_KEY responses —
        because UIDAI sometimes returns 'No Records Found' on wrong captcha too.
        """
        nonlocal last_img, last_txn, last_tid
        consecutive_no_record = 0
        last_no_record_msg = None
        for attempt in range(max_attempts):
            image_bytes, captcha_txn_id, transaction_id, solved = bot.fetch_and_auto_solve_captcha(chat_id)
            if not image_bytes:
                time.sleep(0.4)
                continue
            last_img, last_txn, last_tid = image_bytes, captcha_txn_id, transaction_id
            if not solved:
                return ('retry_fail', None)
            success, result = bot.send_eid_otp(chat_id, mobile, try_name, solved, captcha_txn_id, transaction_id)
            if success:
                sd = {**d, 'name': try_name, 'captcha_code': solved,
                      'captcha1_txn_id': captcha_txn_id, 'transaction_id': transaction_id,
                      'eid_otp_txn_id': result}
                return ('ok', sd)
            err = str(result).lower()
            logger.warning(f"EID OTP error (name='{try_name}', captcha='{solved}', attempt={attempt+1}): {result}")
            if any(k in err for k in BREAK_KEYS):
                consecutive_no_record += 1
                last_no_record_msg = str(result)
                logger.warning(f"No-record response #{consecutive_no_record} for name='{try_name}': {result}")
                if consecutive_no_record >= 3:
                    # 3 consecutive definitive errors → truly not found
                    return ('no_record', last_no_record_msg)
                time.sleep(0.5)
                continue
            # Any retryable error resets the consecutive counter
            consecutive_no_record = 0
            time.sleep(0.5)
        return ('retry_fail', None)

    # Step 1: Try with user's actual name (5 attempts)
    if name and name != 'MR':
        status, payload = _try_name(name, 5)
        if status == 'ok':
            return True, payload
        if status == 'retry_fail':
            # Server/captcha issues on user name — still try MR silently
            logger.info(f"Name '{name}' failed due to server/captcha issues, trying MR silently")
        # 'no_record' on user name → silently fall through to MR below

    # Step 2: Silently try MR (10 attempts)
    status, payload = _try_name('MR', 10)
    if status == 'ok':
        return True, payload
    if status == 'no_record':
        return 'no_record', payload

    # MR also failed due to server/captcha issues — show manual captcha
    if last_img:
        return False, (last_img, last_txn, last_tid)
    img, txn, tid = bot.get_captcha(chat_id)
    return False, (img, txn, tid)


def auto_send_aadhaar_otp(chat_id, eid, id_type, d):
    """Silently auto-solve captcha and send Aadhaar PDF OTP.
    Returns:
      (True,  session_dict)       — success
      (False, (img,txn,tid))      — OCR stuck, show manual captcha
      ('no_record', error_msg)    — definitive API error (EID not found, etc.)
    """
    send_message(chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Sending OTP 〕</b>\n\n"
        f"<i>◌  Please wait…</i>"
    )
    last_img = last_txn = last_tid = None
    last_definitive_error = None

    RETRY_KEYS = ('captcha', 'mismatch', 'timeout', 'timed out', 'time out',
                  'technical', 'invalid response', 'invalid captcha',
                  'try again', 'server error', 'connection', '500', '502', '503', '504')
    BREAK_KEYS = ('no record', 'not found', 'not registered', 'not linked',
                  'no aadhaar', 'not exist', 'does not exist', 'name mismatch',
                  'dob mismatch', 'mobile not')

    for attempt in range(10):
        image_bytes, captcha_txn_id, transaction_id, solved = bot.fetch_and_auto_solve_captcha(chat_id)
        if not image_bytes:
            time.sleep(0.4)
            continue
        last_img, last_txn, last_tid = image_bytes, captcha_txn_id, transaction_id
        if not solved:
            return False, (image_bytes, captcha_txn_id, transaction_id)
        success, otp_txn_id, msg = bot.send_aadhaar_otp(
            chat_id, eid, solved, captcha_txn_id, transaction_id, id_type=id_type
        )
        if success:
            sd = {**d, 'captcha2_code': solved, 'captcha2_txn_id': captcha_txn_id,
                  'transaction_id2': transaction_id, 'pdf_otp_txn_id': otp_txn_id}
            return True, sd
        err = str(msg).lower()
        logger.warning(f"Aadhaar OTP error (captcha='{solved}', attempt={attempt+1}): {msg}")
        if any(k in err for k in BREAK_KEYS):
            last_definitive_error = str(msg)
            logger.warning(f"Definitive Aadhaar OTP error, stopping: {msg}")
            break
        if any(k in err for k in RETRY_KEYS):
            time.sleep(0.5)
            continue
        time.sleep(0.5)
        continue

    if last_definitive_error:
        return 'no_record', last_definitive_error

    if last_img:
        return False, (last_img, last_txn, last_tid)
    img, txn, tid = bot.get_captcha(chat_id)
    return False, (img, txn, tid)

# ============== CONFIG ==============
DIVIDER         = "━━━━━━━━━━━━━━━━━━━━━━━"
BOT_NAME        = "✜ Uɪᴅᴀɪ-Gʀᴀᴍ"
OWNER_ID        = 8901139503
OWNER_USERNAME  = "@Cyreo"
SESSION_TIMEOUT = 600
DATA_FILE       = "users.json"
CODES_FILE      = "codes.json"

PLANS = {
    '10':  {'credits': 10,  'price': '$10'},
    '20':  {'credits': 20,  'price': '$20'},
    '50':  {'credits': 50,  'price': '$50'},
    '100': {'credits': 100, 'price': '$100'},
}
CHANNEL_USERNAME = "@UIDAIGram"
CHANNEL_LINK     = "https://t.me/UIDAIGram"

# ============== USER DATA ==============
_data_lock = threading.Lock()

def _load_json(file):
    try:
        if os.path.exists(file):
            with open(file, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_json(file, data):
    try:
        with open(file, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Save error {file}: {e}")

def _load_users():
    return _load_json(DATA_FILE)

def _save_users(data):
    _save_json(DATA_FILE, data)

def _load_codes():
    return _load_json(CODES_FILE)

def _save_codes(data):
    _save_json(CODES_FILE, data)

def ensure_user(user_id, referrer_id=None):
    """Create user if not exists, handle referral rewards"""
    uid = str(user_id)
    with _data_lock:
        data = _load_users()
        if uid not in data:
            data[uid] = {
                'credits': 1,
                'referred_by': str(referrer_id) if referrer_id else None,
                'referral_count': 0,
                'joined': datetime.now().isoformat()
            }

            # Handle referral reward
            if referrer_id and str(referrer_id) != uid:
                rid = str(referrer_id)
                if rid in data:
                    data[rid]['credits'] = data[rid].get('credits', 0) + 1
                    data[rid]['referral_count'] = data[rid].get('referral_count', 0) + 1
                    _save_users(data)

                    # Send notification directly using API
                    try:
                        notif_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        notif_data = {
                            'chat_id': int(rid),
                            'text': (
                                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                                f"<b>〔 Referral Reward 〕</b>\n\n"
                                f"◈  New user joined via your link!\n"
                                f"◈  You earned +1 credit.\n"
                                f"◈  Total credits: {data[rid]['credits']}\n\n"
                                f"{DIVIDER}"
                            ),
                            'parse_mode': 'HTML'
                        }
                        get_telegram_session().post(notif_url, json=notif_data, timeout=10)
                        logger.info(f"Referral notification sent to {rid} for new user {uid}")
                    except Exception as e:
                        logger.error(f"Failed to send referral notification to {rid}: {e}")
                else:
                    _save_users(data)
            else:
                _save_users(data)
            logger.info(f"New user created: {uid}" + (f" (referred by {referrer_id})" if referrer_id else ""))
            return True

        logger.info(f"Existing user: {uid}")
        return False

# ============== TELEGRAM HELPERS ==============
def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    try:
        response = get_telegram_session().post(url, json=data, timeout=10)
        result = response.json()
        if not result.get('ok'):
            logger.error(f"Telegram send error: {result}")
        return result
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

def answer_callback_query(callback_query_id, text=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    data = {'callback_query_id': callback_query_id}
    if text:
        data['text'] = text
    try:
        get_telegram_session().post(url, json=data, timeout=5)
    except Exception as e:
        logger.error(f"Error answering callback: {e}")

def send_photo(chat_id, photo_bytes, caption=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {'photo': ('captcha.png', photo_bytes, 'image/png')}
    data = {'chat_id': chat_id, 'parse_mode': 'HTML'}
    if caption:
        data['caption'] = caption
    try:
        return get_telegram_session().post(url, data=data, files=files, timeout=20).json()
    except Exception as e:
        logger.error(f"Error sending photo: {e}")
        return None

def send_document(chat_id, file_path, caption=None, filename="Aadhaar.pdf"):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, 'rb') as f:
            files = {'document': (filename, f, 'application/pdf')}
            data = {'chat_id': chat_id, 'parse_mode': 'HTML'}
            if caption:
                data['caption'] = caption
            response = get_telegram_session().post(url, data=data, files=files, timeout=30).json()
        try:
            os.remove(file_path)
        except Exception:
            pass
        return response
    except Exception as e:
        logger.error(f"Error sending document: {e}")
        return None

# ============== KEYBOARDS ==============
def get_main_keyboard():
    return {
        'keyboard': [
            ['◆  Mobile Number', '◆  Aadhaar Number'],
            ['◆  EID'],
            ['◇  Credits', '◇  Buy Credits', '◇  Referral'],
        ],
        'resize_keyboard': True,
        'one_time_keyboard': False
    }

def get_cancel_keyboard():
    return {'inline_keyboard': [[{'text': '✗  Cancel', 'callback_data': 'cancel'}]]}

def get_name_auto_keyboard():
    return {'inline_keyboard': [[{'text': 'Find Automatically ✓', 'callback_data': 'auto_name'}]]}

def get_buy_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '◆  10 Credits  —  $10',  'callback_data': 'buy_10'}],
            [{'text': '◆  20 Credits  —  $20',  'callback_data': 'buy_20'}],
            [{'text': '◆  50 Credits  —  $50',  'callback_data': 'buy_50'}],
            [{'text': '◆  100 Credits —  $100', 'callback_data': 'buy_100'}],
        ]
    }

def get_join_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '◆  Join Channel',    'url': CHANNEL_LINK}],
            [{'text': '◇  I have joined ✓', 'callback_data': 'check_join'}],
        ]
    }

# ============== SHARED DISPLAY HELPERS ==============
def show_credits_info(chat_id):
    u  = get_user(chat_id)
    cr = get_credits(chat_id)
    ref_count  = u.get('referral_count', 0) if u else 0
    joined     = u.get('joined', '')[:10] if u else '—'
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 My Credits 〕</b>\n\n"
        f"◈  Balance     ·  {cr}\n"
        f"◈  Referrals   ·  {ref_count}\n"
        f"◈  Member since·  {joined}\n\n"
        f"{DIVIDER}\n"
        f"<i>◌  1 credit = 1 Aadhaar download\n"
        f"◌  Earn free credits via your referral link</i>"
    )

def show_buy_menu(chat_id):
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Buy Credits 〕</b>\n\n"
        f"◈  10 credits    ·  <b>$10</b>\n"
        f"◈  20 credits    ·  <b>$20</b>\n"
        f"◈  50 credits    ·  <b>$50</b>\n"
        f"◈  100 credits   ·  <b>$100</b>\n\n"
        f"{DIVIDER}\n"
        f"<i>◌  Tap a plan below to see payment details</i>",
        reply_markup=get_buy_keyboard()
    )

def show_referral_info(chat_id):
    username  = get_bot_username()
    link      = f"https://t.me/{username}?start=ref_{chat_id}"
    u         = get_user(chat_id)
    ref_count = u.get('referral_count', 0) if u else 0
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Referral 〕</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"◈  Friends joined  ·  {ref_count}\n"
        f"◈  Credits earned  ·  {ref_count}\n\n"
        f"{DIVIDER}\n"
        f"<i>◌  Share your link — earn +1 credit per friend who joins</i>"
    )

# ============== GATES ==============
def is_channel_member(user_id):
    try:
        r = get_telegram_session().get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChatMember",
            params={'chat_id': CHANNEL_USERNAME, 'user_id': user_id},
            timeout=6
        ).json()
        if r.get('ok'):
            status = r['result']['status']
            return status in ('member', 'administrator', 'creator')
    except Exception as e:
        logger.error(f"Channel check error: {e}")
    return False

def channel_gate(chat_id):
    if is_channel_member(chat_id):
        return True
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Channel Required 〕</b>\n\n"
        f"▸  Join <b>{CHANNEL_USERNAME}</b> to use this bot.\n\n"
        f"{DIVIDER}\n"
        f"<i>◌  Tap Join below, then confirm with the button.</i>",
        reply_markup=get_join_keyboard()
    )
    return False

def credit_gate(chat_id):
    if has_credits(chat_id):
        return True
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 No Credits 〕</b>\n\n"
        f"◈  Balance  ·  <b>0</b>\n\n"
        f"▸  Tap <b>◇ Buy Credits</b> to purchase a plan.\n"
        f"▸  Tap <b>◇ Referral</b> to earn credits free.\n\n"
        f"{DIVIDER}"
    )
    return False

# ============== PDF DELIVERY ==============
def deliver_pdf(chat_id, pdf_path, verified_name):
    name_display = verified_name if verified_name and verified_name.strip() else "Mr."
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Processing 〕</b>\n\n"
        f"<i>◌  Decrypting your document…</i>"
    )
    try:
        crack_success, password, decrypted_path, _ = bot.crack_pdf_with_name(pdf_path, name_display, None)
        if crack_success and decrypted_path:
            caption = (
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Document Ready  ✓ 〕</b>\n\n"
                f"◈  Name    ·  {name_display}\n"
                f"◈  Format  ·  e-Aadhaar PDF\n"
                f"◈  Status  ·  <b>Unlocked</b>\n"
                f"{DIVIDER}"
            )
            send_document(chat_id, decrypted_path, caption=caption, filename="Aadhaar.pdf")
            try:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
            except Exception:
                pass
        else:
            caption = (
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Document Ready 〕</b>\n\n"
                f"◈  Name    ·  {name_display}\n"
                f"◈  Format  ·  e-Aadhaar PDF\n"
                f"◈  Status  ·  Password Protected\n"
                f"{DIVIDER}\n\n"
                f"<i>◌  Password: first 4 letters of name + birth year\n"
                f"   Example: <code>RAJE1995</code></i>"
            )
            send_document(chat_id, pdf_path, caption=caption, filename="Aadhaar.pdf")
    except Exception as e:
        logger.error(f"PDF delivery error: {e}")
        send_document(
            chat_id, pdf_path,
            caption=f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 Document Ready 〕</b>",
            filename="Aadhaar.pdf"
        )

    deduct_credit(chat_id)
    cr = get_credits(chat_id)
    clear_session(chat_id)
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Download Complete  ✓ 〕</b>\n\n"
        f"◈  Credits remaining  ·  {cr}\n\n"
        f"{DIVIDER}\n"
        f"<i>◌  Select a method below for another download.</i>",
        reply_markup=get_main_keyboard()
    )

# ============== CALLBACK HANDLER ==============
def handle_callback(chat_id, callback_query_id, data):
    answer_callback_query(callback_query_id)
    ensure_user(chat_id)

    if data == 'check_join':
        if is_channel_member(chat_id):
            cr = get_credits(chat_id)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n\n"
                f"<b>e-Aadhaar PDF  —  straight to Telegram</b>\n\n"
                f"◈  Source    ·  Official UIDAI portal\n"
                f"◈  Delivery  ·  Auto-unlocked, no password\n"
                f"◈  Methods   ·  Mobile  ·  Aadhaar  ·  EID\n\n"
                f"{DIVIDER}\n"
                f"◈  Credits  ·  {cr}\n\n"
                f"<i>◌  Select a method below to begin.</i>",
                reply_markup=get_main_keyboard()
            )
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Not Joined Yet 〕</b>\n\n"
                f"✗  Channel membership not detected.\n\n"
                f"<i>◌  Join the channel, then tap the button again.</i>",
                reply_markup=get_join_keyboard()
            )
        return

    if data == 'cancel':
        clear_session(chat_id)
        send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<i>✗  Session cancelled.</i>")
        return

    if data == 'credits':
        show_credits_info(chat_id)
        return
    if data == 'buy':
        show_buy_menu(chat_id)
        return
    if data == 'referral':
        show_referral_info(chat_id)
        return

    if data.startswith('buy_'):
        plan_key = data.split('_')[1]
        plan = PLANS.get(plan_key)
        if not plan:
            return
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 Payment — {plan['price']} 〕</b>\n\n"
            f"◈  Plan    ·  {plan['credits']} credits\n"
            f"◈  Amount  ·  <b>{plan['price']}</b>\n\n"
            f"{DIVIDER}\n"
            f"▸  Message <b>{OWNER_USERNAME}</b> on Telegram to pay\n\n"
            f"◈  Your ID  ·  <code>{chat_id}</code>\n\n"
            f"{DIVIDER}\n"
            f"<i>◌  Credits will be added after payment is verified.</i>"
        )
        return

    if data == 'auto_name':
        s = get_session(chat_id)
        d = s.get('data', {})
        name = "MR"
        ok, result = auto_send_eid_otp(chat_id, d.get('mobile', ''), name, d)
        if ok is True:
            set_session(chat_id, 'awaiting_otp', result)
            send_message(chat_id, f"<b>〔 OTP Sent  ✓ 〕</b>\n\n▸  Enter the 6-digit OTP\n\n<i>◌  Valid for 10 minutes</i>", reply_markup=get_cancel_keyboard())
        elif ok == 'no_record':
            clear_session(chat_id)
            send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  <b>No Records Found</b>\n\n<i>◌  This mobile number is not linked with any Aadhaar.</i>")
        else:
            image_bytes, captcha_txn_id, transaction_id = result if result else (None, None, None)
            if image_bytes:
                set_session(chat_id, 'awaiting_captcha1', {**d, 'name': name,
                            'captcha1_txn_id': captcha_txn_id, 'transaction_id': transaction_id})
                send_photo(chat_id, image_bytes, caption="<i>▸  Auto-solve failed. Type the captcha manually:</i>")
            else:
                clear_session(chat_id)
                send_message(chat_id, f"✗  Captcha service unavailable.")
        return

    if data in ('search_mobile', 'search_aadhaar', 'search_eid'):
        if not channel_gate(chat_id): return
        if not credit_gate(chat_id): return

    if data == 'search_mobile':
        set_session(chat_id, 'awaiting_mobile', {'mode': 'mobile'})
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 Mobile Search 〕</b>\n\n"
            f"▸  Enter your 10-digit mobile number\n\n"
            f"<i>◌  OTP will be sent to this number</i>",
            reply_markup=get_cancel_keyboard()
        )
    elif data == 'search_aadhaar':
        set_session(chat_id, 'awaiting_aadhaar', {'mode': 'aadhaar'})
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 Aadhaar Search 〕</b>\n\n"
            f"▸  Enter your 12-digit Aadhaar number\n\n"
            f"<i>◌  Spaces are removed automatically</i>",
            reply_markup=get_cancel_keyboard()
        )
    elif data == 'search_eid':
        set_session(chat_id, 'awaiting_eid_input', {'mode': 'eid'})
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 EID Search 〕</b>\n\n"
            f"▸  Enter your Enrollment ID (EID)\n\n"
            f"<i>◌  Format: 1234/56789/12345</i>",
            reply_markup=get_cancel_keyboard()
        )

# ============== OWNER COMMANDS ==============
def handle_owner_command(chat_id, text):
    parts = text.strip().split()
    if not parts:
        return False

    cmd = parts[0].lower()

    if cmd == '/send' and len(parts) == 3:
        # /send all AMOUNT  — broadcast to every user
        if parts[1].lower() == 'all':
            try:
                amount = int(parts[2])
                if amount <= 0:
                    send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  Amount must be positive.")
                    return True
                data = _load_users()
                total = len(data)
                send_message(chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"<b>〔 Broadcasting Credits 〕</b>\n\n"
                    f"◈  Amount  ·  {amount} credits\n"
                    f"◈  Users   ·  {total}\n\n"
                    f"<i>◌  Sending… please wait.</i>"
                )
                ok = fail = 0
                for uid_str in list(data.keys()):
                    try:
                        add_credits(int(uid_str), amount)
                        send_message(int(uid_str),
                            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                            f"<b>〔 Credits Received  ✓ 〕</b>\n\n"
                            f"◈  Credited  ·  +{amount}\n"
                            f"◈  Balance   ·  {get_credits(int(uid_str))}\n\n"
                            f"{DIVIDER}",
                            reply_markup=get_main_keyboard()
                        )
                        ok += 1
                    except Exception as ex:
                        logger.warning(f"Broadcast failed for {uid_str}: {ex}")
                        fail += 1
                    time.sleep(0.05)   # rate-limit friendly
                send_message(chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"<b>〔 Broadcast Complete  ✓ 〕</b>\n\n"
                    f"◈  Sent     ·  {ok}\n"
                    f"◈  Failed   ·  {fail}\n"
                    f"◈  Amount   ·  {amount} credits each\n\n"
                    f"{DIVIDER}"
                )
            except ValueError:
                send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  Usage: /send all AMOUNT")
            return True
        # /send USERID AMOUNT
        try:
            target_id = int(parts[1])
            amount = int(parts[2])
            if amount <= 0:
                send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  Amount must be positive.")
                return True
            add_credits(target_id, amount)
            send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n<b>[ done ]</b>\n\n◆  Sent {amount} credits to <code>{target_id}</code>")
            send_message(target_id,
                f"{BOT_NAME}\n{DIVIDER}\n"
                f"<b>[ credits received ]</b>\n\n"
                f"◆  Credits  —  +{amount}\n"
                f"◆  Balance  —  {get_credits(target_id)}\n\n"
                f"{DIVIDER}",
                reply_markup=get_main_keyboard()
            )
        except ValueError:
            send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  Usage: /send USERID AMOUNT")
        return True

    if cmd == '/remove' and len(parts) == 3:
        try:
            target_id = int(parts[1])
            amount = int(parts[2])
            if amount <= 0:
                send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  Amount must be positive.")
                return True
            uid = str(target_id)
            data = _load_users()
            if uid in data:
                data[uid]['credits'] = max(0, data[uid].get('credits', 0) - amount)
                _save_users(data)
                send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n<b>[ done ]</b>\n\n◆  Removed {amount} credits from <code>{target_id}</code>")
                send_message(target_id,
                    f"{BOT_NAME}\n{DIVIDER}\n"
                    f"<b>[ credits deducted ]</b>\n\n"
                    f"◆  Deducted  —  -{amount}\n"
                    f"◆  Balance   —  {get_credits(target_id)}\n\n"
                    f"{DIVIDER}",
                    reply_markup=get_main_keyboard()
                )
            else:
                send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  User not found.")
        except ValueError:
            send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  Usage: /remove USERID AMOUNT")
        return True

    if cmd == '/stats':
        data = _load_users()
        total_users = len(data)
        total_credits = 0
        for u in data.values():
            c = u.get('credits', 0)
            if isinstance(c, int) and 0 <= c <= 100000:
                total_credits += c
        codes_data = _load_codes()
        unused_codes = sum(1 for c in codes_data.values() if not c.get('used'))
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>[ Advanced Stats ]</b>\n\n"
            f"◆  Total users      —  {total_users}\n"
            f"◆  Active credits   —  {total_credits}\n"
            f"◆  Unused codes     —  {unused_codes}\n\n"
            f"{DIVIDER}"
        )
        return True

    if cmd == '/balance' and len(parts) == 2:
        try:
            uid = int(parts[1])
            cr = get_credits(uid)
            send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n<b>[ balance ]</b>\n\n◆  User    —  <code>{uid}</code>\n◆  Credits —  {cr}\n\n{DIVIDER}")
        except ValueError:
            send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  Usage: /balance USERID")
        return True

    if cmd == '/ip' and len(parts) == 2:
        new_proxy = parts[1]
        set_uidai_proxy(new_proxy)
        send_message(chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 Proxy Updated ✓ 〕</b>\n\n"
            f"◈  New proxy  ·  {new_proxy}\n\n"
            f"{DIVIDER}"
        )
        return True

    if cmd == '/gen' and len(parts) == 2:
        try:
            amount = int(parts[1])
            if amount <= 0:
                send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  Amount must be positive.")
                return True
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
            codes = _load_codes()
            codes[code] = {'amount': amount, 'used': False, 'created': datetime.now().isoformat()}
            _save_codes(codes)
            send_message(chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Code Generated 〕</b>\n\n"
                f"◈  Code    ·  <code>{code}</code>\n"
                f"◈  Credits ·  {amount}\n\n"
                f"{DIVIDER}\n"
                f"<i>◌  Share this code with user to redeem.</i>"
            )
        except ValueError:
            send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  Usage: /gen AMOUNT")
        return True

    return False

# ============== USER COMMANDS (Redeem) ==============
def handle_user_command(chat_id, text):
    parts = text.strip().split()
    if len(parts) != 2 or parts[0].lower() != '/redeem':
        return False
    code = parts[1].upper()
    codes = _load_codes()
    if code not in codes or codes[code]['used']:
        send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  Invalid or already used code.")
        return True
    amount = codes[code]['amount']
    add_credits(chat_id, amount)
    codes[code]['used'] = True
    codes[code]['redeemed_by'] = str(chat_id)
    codes[code]['redeemed_at'] = datetime.now().isoformat()
    _save_codes(codes)
    send_message(chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Gift Card Redeemed ✓ 〕</b>\n\n"
        f"◈  Code    ·  <code>{code}</code>\n"
        f"◈  Value   ·  {amount} Credits\n"
        f"◈  Balance ·  {get_credits(chat_id)}\n\n"
        f"{DIVIDER}\n"
        f"<i>◌  Credits added successfully!</i>"
    )
    # Notify owner
    send_message(OWNER_ID,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Code Redeemed by User 〕</b>\n\n"
        f"◈  User    ·  <code>{chat_id}</code>\n"
        f"◈  Code    ·  <code>{code}</code>\n"
        f"◈  Credits ·  {amount}\n\n"
        f"{DIVIDER}"
    )
    return True

# ============== MESSAGE HANDLER ==============
_KB_ACTIONS = {
    '◆  mobile number':  'search_mobile',
    '◆  aadhaar number': 'search_aadhaar',
    '◆  eid':            'search_eid',
    '◇  credits':        'credits',
    '◇  buy credits':    'buy',
    '◇  referral':       'referral',
}

def handle_message(chat_id, message_text):
    logger.info(f"Msg [{chat_id}]: {message_text[:60]}")
    ensure_user(chat_id)

    if chat_id == OWNER_ID and message_text.startswith('/'):
        if handle_owner_command(chat_id, message_text):
            return

    if message_text.startswith('/redeem'):
        if handle_user_command(chat_id, message_text):
            return

    action = _KB_ACTIONS.get(message_text.strip().lower())
    if action:
        if action in ('search_mobile', 'search_aadhaar', 'search_eid'):
            if not channel_gate(chat_id): return
            if not credit_gate(chat_id): return
            clear_session(chat_id)
            if action == 'search_mobile':
                set_session(chat_id, 'awaiting_mobile', {'mode': 'mobile'})
                send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n<b>[ mobile search ]</b>\n\n▸  Enter your 10-digit mobile number", reply_markup=get_cancel_keyboard())
            elif action == 'search_aadhaar':
                set_session(chat_id, 'awaiting_aadhaar', {'mode': 'aadhaar'})
                send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n<b>[ aadhaar search ]</b>\n\n▸  Enter your 12-digit Aadhaar number", reply_markup=get_cancel_keyboard())
            elif action == 'search_eid':
                set_session(chat_id, 'awaiting_eid_input', {'mode': 'eid'})
                send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n<b>[ EID search ]</b>\n\n▸  Enter your Enrollment ID (EID)", reply_markup=get_cancel_keyboard())
        elif action == 'credits':
            show_credits_info(chat_id)
        elif action == 'buy':
            show_buy_menu(chat_id)
        elif action == 'referral':
            show_referral_info(chat_id)
        return

    s = get_session(chat_id)
    current_step = s.get('step', 'main')
    d = s.get('data', {})

    if current_step != 'main':
        age = time.time() - s.get('created_at', time.time())
        if age > SESSION_TIMEOUT:
            clear_session(chat_id)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Session Expired 〕</b>\n\n"
                f"◈  Status   ·  Timed out after 10 min\n"
                f"◈  Credits  ·  Not deducted\n\n"
                f"{DIVIDER}\n"
                f"<i>◌  Select a method below to start a new session.</i>"
            )
            return

    touch_session(chat_id)

    if message_text.lower() in ['/cancel', 'cancel']:
        clear_session(chat_id)
        send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<i>✗  Session cancelled.</i>")
        return

    if current_step == 'main':
        return

    # ============== MOBILE FLOW ==============
    if current_step == 'awaiting_mobile':
        if re.match(r'^\d{10}$', message_text):
            set_session(chat_id, 'awaiting_name', {**d, 'mobile': message_text})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Step 2 of 4 — Name 〕</b>\n\n"
                f"▸  Enter full name as on Aadhaar\n\n"
                f"<i>◌  Unknown name? Tap below to proceed anyway</i>",
                reply_markup=get_name_auto_keyboard()
            )
        else:
            send_message(chat_id, f"✗  Invalid number. Enter a 10-digit mobile number.")

    elif current_step == 'awaiting_name':
        name = message_text.strip().upper() if len(message_text.strip()) >= 2 else "MR"
        ok, result = auto_send_eid_otp(chat_id, d.get('mobile', ''), name, d)
        if ok is True:
            set_session(chat_id, 'awaiting_otp', result)
            send_message(chat_id, f"<b>〔 OTP Sent  ✓ 〕</b>\n\n▸  Enter the 6-digit OTP\n\n<i>◌  Valid for 10 minutes</i>", reply_markup=get_cancel_keyboard())
        elif ok == 'no_record':
            clear_session(chat_id)
            send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  <b>No Records Found</b>\n\n<i>◌  This mobile number is not linked with any Aadhaar.</i>")
        else:
            image_bytes, captcha_txn_id, transaction_id = result if result else (None, None, None)
            if image_bytes:
                set_session(chat_id, 'awaiting_captcha1', {**d, 'name': name,
                            'captcha1_txn_id': captcha_txn_id, 'transaction_id': transaction_id})
                send_photo(chat_id, image_bytes, caption="<i>▸  Auto-solve failed. Type the captcha manually:</i>")
            else:
                clear_session(chat_id)
                send_message(chat_id, f"✗  Captcha service unavailable.")

    elif current_step == 'awaiting_captcha1':
        set_session(chat_id, 'sending_otp', {**d, 'captcha_code': message_text.strip()})
        send_message(chat_id, f"<b>〔 Sending OTP 〕</b>\n<i>◌  Please wait…</i>")
        sd = get_session(chat_id)['data']
        success, result = bot.send_eid_otp(
            chat_id, sd['mobile'], sd['name'],
            sd['captcha_code'], sd['captcha1_txn_id'], sd['transaction_id']
        )
        if success:
            set_session(chat_id, 'awaiting_otp', {**sd, 'eid_otp_txn_id': result})
            send_message(chat_id, f"<b>〔 OTP Sent  ✓ 〕</b>\n\n▸  Enter the 6-digit OTP\n\n<i>◌  Valid for 10 minutes</i>", reply_markup=get_cancel_keyboard())
        else:
            clear_session(chat_id)
            send_message(chat_id, f"✗  No Records Found\n\n<i>◌  Select a method below to retry.</i>")

    elif current_step == 'awaiting_otp':
        if re.match(r'^\d{6}$', message_text):
            send_message(chat_id, f"<b>〔 Verifying 〕</b>\n<i>◌  Checking OTP…</i>")
            success, eid, name = bot.verify_eid_otp(
                chat_id, d['mobile'], d['name'], message_text,
                d['eid_otp_txn_id'], d['captcha1_txn_id'], d['captcha_code']
            )
            if success:
                verified_name = name if name and name.strip() else "Mr."
                send_message(chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"<b>〔 Identity Verified  ✓ 〕</b>\n\n"
                    f"◈  Name  ·  {verified_name}\n"
                    f"◈  EID   ·  <code>{eid}</code>\n\n"
                    f"{DIVIDER}"
                )
                base_d = {**d, 'eid': eid, 'verified_name': verified_name, 'id_type': 'eid'}
                ok2, result2 = auto_send_aadhaar_otp(chat_id, eid, 'eid', base_d)
                if ok2 is True:
                    set_session(chat_id, 'awaiting_pdf_otp', result2)
                    send_message(chat_id, f"<b>〔 OTP Sent  ✓ 〕</b>\n\n▸  Enter the 6-digit OTP\n\n<i>◌  Valid for 10 minutes</i>", reply_markup=get_cancel_keyboard())
                elif ok2 == 'no_record':
                    clear_session(chat_id)
                    send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  <b>Aadhaar Not Found</b>\n\n<i>◌  This EID is not linked with any Aadhaar account.</i>")
                else:
                    image_bytes, captcha_txn_id, transaction_id = result2 if result2 else (None, None, None)
                    if image_bytes:
                        set_session(chat_id, 'awaiting_captcha2', {**base_d,
                                    'captcha2_txn_id': captcha_txn_id, 'transaction_id2': transaction_id})
                        send_photo(chat_id, image_bytes, caption="<i>▸  Auto-solve failed. Type the captcha manually:</i>")
                    else:
                        clear_session(chat_id)
                        send_message(chat_id, f"✗  Captcha unavailable.")
            else:
                clear_session(chat_id)
                send_message(chat_id, f"✗  Verification failed — {eid}\n\n<i>◌  Select a method below to retry.</i>")
        else:
            send_message(chat_id, f"✗  Invalid OTP. Enter 6 digits.")

    elif current_step == 'awaiting_captcha2':
        sd = {**d, 'captcha2_code': message_text.strip()}
        set_session(chat_id, 'sending_pdf_otp', sd)
        send_message(chat_id, f"<b>〔 Sending OTP for PDF 〕</b>\n<i>◌  Please wait…</i>")
        success, otp_txn_id, msg = bot.send_aadhaar_otp(
            chat_id, sd['eid'], sd['captcha2_code'], sd['captcha2_txn_id'],
            sd['transaction_id2'], id_type=sd.get('id_type', 'eid')
        )
        if success:
            set_session(chat_id, 'awaiting_pdf_otp', {**sd, 'pdf_otp_txn_id': otp_txn_id})
            send_message(chat_id, f"<b>〔 OTP Sent  ✓ 〕</b>\n\n▸  Enter the 6-digit OTP\n\n<i>◌  Valid for 10 minutes</i>", reply_markup=get_cancel_keyboard())
        else:
            clear_session(chat_id)
            send_message(chat_id, f"✗  OTP failed — {msg}")

    elif current_step == 'awaiting_pdf_otp':
        if re.match(r'^\d{6}$', message_text):
            send_message(chat_id, f"<b>〔 Downloading 〕</b>\n<i>◌  Fetching your Aadhaar PDF…</i>")
            success, pdf_path = bot.download_aadhaar_pdf(
                chat_id, d['eid'], message_text, d['pdf_otp_txn_id'],
                d['transaction_id2'], False, id_type=d.get('id_type', 'eid')
            )
            if success and pdf_path and '.pdf' in pdf_path:
                deliver_pdf(chat_id, pdf_path, d.get('verified_name', 'Mr.'))
            else:
                clear_session(chat_id)
                send_message(chat_id, f"✗  Download failed — {pdf_path}")
        else:
            send_message(chat_id, f"✗  Invalid OTP.")

    # ============== DIRECT AADHAAR / EID FLOW ==============
    elif current_step == 'awaiting_aadhaar':
        uid = message_text.strip().replace(' ', '')
        if re.match(r'^\d{12}$', uid):
            set_session(chat_id, 'awaiting_name_direct', {**d, 'eid': uid, 'id_type': 'uid'})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Step 2 — Name 〕</b>\n\n"
                f"▸  Enter full name as on Aadhaar (required for PDF unlock)"
            )
        else:
            send_message(chat_id, f"✗  Invalid Aadhaar. Enter 12 digits.")

    elif current_step == 'awaiting_eid_input':
        eid = message_text.strip()
        if len(eid) >= 10:
            set_session(chat_id, 'awaiting_name_direct', {**d, 'eid': eid, 'id_type': 'eid'})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Step 2 — Name 〕</b>\n\n"
                f"▸  Enter full name as on Aadhaar (required for PDF unlock)"
            )
        else:
            send_message(chat_id, f"✗  Invalid EID.")

    elif current_step == 'awaiting_name_direct':
        name = message_text.strip().upper() if len(message_text.strip()) >= 2 else "MR"
        base_d = {**d, 'verified_name': name}
        ok, result = auto_send_aadhaar_otp(chat_id, d.get('eid', ''), d.get('id_type', 'eid'), base_d)
        if ok is True:
            set_session(chat_id, 'awaiting_pdf_otp_direct', result)
            send_message(chat_id, f"<b>〔 OTP Sent  ✓ 〕</b>\n\n▸  Enter the 6-digit OTP\n\n<i>◌  Valid for 10 minutes</i>", reply_markup=get_cancel_keyboard())
        elif ok == 'no_record':
            clear_session(chat_id)
            send_message(chat_id, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  <b>Aadhaar Not Found</b>\n\n<i>◌  This EID is not linked with any Aadhaar account.</i>")
        else:
            image_bytes, captcha_txn_id, transaction_id = result if result else (None, None, None)
            if image_bytes:
                set_session(chat_id, 'awaiting_captcha_direct', {**base_d,
                            'captcha2_txn_id': captcha_txn_id, 'transaction_id2': transaction_id})
                send_photo(chat_id, image_bytes, caption="<i>▸  Auto-solve failed. Type the captcha manually:</i>")
            else:
                clear_session(chat_id)
                send_message(chat_id, f"✗  Captcha unavailable.")

    elif current_step == 'awaiting_captcha_direct':
        sd = {**d, 'captcha2_code': message_text.strip()}
        set_session(chat_id, 'sending_pdf_otp_direct', sd)
        send_message(chat_id, f"<b>〔 Sending OTP 〕</b>\n<i>◌  Please wait…</i>")
        success, otp_txn_id, msg = bot.send_aadhaar_otp(
            chat_id, sd['eid'], sd['captcha2_code'], sd['captcha2_txn_id'],
            sd['transaction_id2'], id_type=sd.get('id_type', 'eid')
        )
        if success:
            set_session(chat_id, 'awaiting_pdf_otp_direct', {**sd, 'pdf_otp_txn_id': otp_txn_id})
            send_message(chat_id, f"<b>〔 OTP Sent  ✓ 〕</b>\n\n▸  Enter the 6-digit OTP\n\n<i>◌  Valid for 10 minutes</i>", reply_markup=get_cancel_keyboard())
        else:
            clear_session(chat_id)
            send_message(chat_id, f"✗  OTP failed — {msg}")

    elif current_step == 'awaiting_pdf_otp_direct':
        if re.match(r'^\d{6}$', message_text):
            send_message(chat_id, f"<b>〔 Downloading 〕</b>\n<i>◌  Fetching your Aadhaar PDF…</i>")
            success, pdf_path = bot.download_aadhaar_pdf(
                chat_id, d['eid'], message_text, d['pdf_otp_txn_id'],
                d['transaction_id2'], False, id_type=d.get('id_type', 'eid')
            )
            if success and pdf_path and '.pdf' in pdf_path:
                deliver_pdf(chat_id, pdf_path, d.get('verified_name', 'Mr.'))
            else:
                clear_session(chat_id)
                send_message(chat_id, f"✗  Download failed — {pdf_path}")
        else:
            send_message(chat_id, f"✗  Invalid OTP.")

# ============== GET UPDATES ==============
def get_updates(offset=None):
    url    = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {'timeout': 30, 'allowed_updates': ['message', 'callback_query']}
    if offset:
        params['offset'] = offset
    try:
        response = get_telegram_session().get(url, params=params, timeout=35)
        result   = response.json()
        if result.get('ok'):
            return result.get('result', [])
        else:
            logger.error(f"Telegram API error: {result}")
            return []
    except Exception as e:
        logger.error(f"Error getting updates: {e}")
        return []

# ============== SESSION MANAGEMENT ==============
# Sessions use an ABSOLUTE 10-minute timeout from creation — activity does NOT reset the clock.
user_sessions   = {}
_sessions_lock  = threading.Lock()

def get_session(chat_id):
    with _sessions_lock:
        return user_sessions.get(chat_id, {'step': 'main', 'data': {}, 'created_at': time.time()})

def set_session(chat_id, step, data=None):
    with _sessions_lock:
        existing = user_sessions.get(chat_id, {})
        d = data if data is not None else existing.get('data', {})
        # Preserve the original created_at so the 10-min clock isn't reset on each step update
        created_at = existing.get('created_at', time.time()) if existing.get('step', 'main') != 'main' else time.time()
        user_sessions[chat_id] = {'step': step, 'data': d, 'created_at': created_at}

def clear_session(chat_id):
    with _sessions_lock:
        user_sessions[chat_id] = {'step': 'main', 'data': {}, 'created_at': time.time()}

def touch_session(chat_id):
    pass  # no-op: absolute timeout — activity doesn't extend the session

def _cleanup_sessions():
    while True:
        time.sleep(20)
        try:
            expired = []
            with _sessions_lock:
                for cid, s in list(user_sessions.items()):
                    if s.get('step', 'main') != 'main':
                        age = time.time() - s.get('created_at', time.time())
                        if age > SESSION_TIMEOUT:
                            user_sessions[cid] = {'step': 'main', 'data': {}, 'created_at': time.time()}
                            expired.append(cid)
            for cid in expired:
                try:
                    send_message(cid,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"<b>〔 Session Expired 〕</b>\n\n"
                        f"◈  Status   ·  Timed out after 10 min\n"
                        f"◈  Credits  ·  Not deducted\n\n"
                        f"{DIVIDER}\n"
                        f"<i>◌  Select a method below to start again.</i>",
                        reply_markup=get_main_keyboard()
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Session cleanup error: {e}")

# ============== USER DATA FUNCTIONS ==============
def get_user(user_id):
    with _data_lock:
        return _load_users().get(str(user_id))

def get_credits(user_id):
    u = get_user(user_id)
    if u is None:
        return 0
    return u.get('credits', 0)

def has_credits(user_id):
    return get_credits(user_id) > 0

def add_credits(user_id, amount):
    uid = str(user_id)
    with _data_lock:
        data = _load_users()
        if uid not in data:
            data[uid] = {'credits': 0, 'referred_by': None, 'referral_count': 0, 'joined': datetime.now().isoformat()}
        data[uid]['credits'] = data[uid].get('credits', 0) + amount
        _save_users(data)

def deduct_credit(user_id):
    uid = str(user_id)
    with _data_lock:
        data = _load_users()
        if uid in data:
            data[uid]['credits'] = max(0, data[uid].get('credits', 0) - 1)
            _save_users(data)

# ============== BOT USERNAME ==============
_bot_username = None
def get_bot_username():
    global _bot_username
    if _bot_username:
        return _bot_username
    try:
        r = get_telegram_session().get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=5
        ).json()
        if r.get('ok'):
            _bot_username = r['result']['username']
    except Exception:
        pass
    return _bot_username or "UIDAIGrambot"

# ============== MAIN ==============
def main():
    print("━" * 50)
    print(f"  {BOT_NAME}  —  starting up")
    print("━" * 50)
    print(f"[ ocr    ]  ddddocr  : {'✓ ready' if _DDDD_OK else '✗ NOT installed'}")
    print(f"[ ocr    ]  tesseract: {'✓ ready' if _TESS_OK else '✗ NOT installed'}")
    if not _DDDD_OK and not _TESS_OK:
        print("[ ERROR  ]  No OCR engine found — auto captcha will NOT work!")
        print("[ FIX    ]  Run:  pip install ddddocr pytesseract  &&  apt install tesseract-ocr")
    print("━" * 50)

    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[ error ] TELEGRAM_BOT_TOKEN not set.")
        return

    try:
        r = get_telegram_session().get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=10
        )
        bot_info = r.json()
        if bot_info.get('ok'):
            global _bot_username
            _bot_username = bot_info['result']['username']
            print(f"[ online ]  @{_bot_username}")
            print(f"[ proxy  ]  UIDAI: {UIDAI_PROXY}")
            print(f"[ owner  ]  {OWNER_ID}")
        else:
            print(f"[ error ] Bot auth failed: {bot_info}")
            return
    except Exception as e:
        print(f"[ error ] {e}")
        return

    t = threading.Thread(target=_cleanup_sessions, daemon=True)
    t.start()

    print("━" * 50)
    print("  running  —  Ctrl+C to stop")
    print("━" * 50)

    last_update_id = 0
    _update_executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix='upd')

    def _process_update(update):
        try:
            if 'callback_query' in update:
                cq   = update['callback_query']
                cid  = cq['message']['chat']['id']
                cqid = cq['id']
                data = cq.get('data', '')
                handle_callback(cid, cqid, data)

            elif 'message' in update:
                msg  = update.get('message', {})
                cid  = msg.get('chat', {}).get('id')
                if not cid:
                    return
                text = msg.get('text', '').strip()
                if not text:
                    return

                if text.startswith('/start'):
                    parts = text.split()
                    referrer_id = None
                    if len(parts) > 1 and parts[1].startswith('ref_'):
                        try:
                            referrer_id = int(parts[1][4:])
                        except ValueError:
                            pass

                    ensure_user(cid, referrer_id)
                    clear_session(cid)

                    if not is_channel_member(cid):
                        send_message(
                            cid,
                            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                            f"<b>〔 Channel Required 〕</b>\n\n"
                            f"▸  Join <b>{CHANNEL_USERNAME}</b> to use this bot.\n\n"
                            f"{DIVIDER}\n"
                            f"<i>◌  Tap the button below after joining.</i>",
                            reply_markup=get_join_keyboard()
                        )
                        return

                    cr = get_credits(cid)
                    send_message(
                        cid,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n\n"
                        f"<b>e-Aadhaar PDF  —  straight to Telegram</b>\n\n"
                        f"◈  Source    ·  Official UIDAI portal\n"
                        f"◈  Delivery  ·  Auto-unlocked, no password\n"
                        f"◈  Methods   ·  Mobile  ·  Aadhaar  ·  EID\n\n"
                        f"{DIVIDER}\n"
                        f"◈  Credits  ·  {cr}\n\n"
                        f"<i>◌  Select a method below to begin.</i>",
                        reply_markup=get_main_keyboard()
                    )
                else:
                    handle_message(cid, text)

        except Exception as e:
            logger.error(f"Update processing error: {e}", exc_info=True)

    while True:
        try:
            updates = get_updates(last_update_id + 1)

            for update in updates:
                uid = update.get('update_id')
                if uid:
                    last_update_id = uid
                _update_executor.submit(_process_update, update)

            time.sleep(0.3)

        except KeyboardInterrupt:
            print("\n[ stopped ]")
            _update_executor.shutdown(wait=False)
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
