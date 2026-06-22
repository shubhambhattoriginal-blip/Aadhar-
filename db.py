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
from io import BytesIO
from urllib.parse import urlparse
import PyPDF2
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# ---------- CAPTCHA SOLVER IMPORTS ----------
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter

# ============== LOGGING SETUP ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============== BOT TOKEN - DIRECT HARDCODE ==============
TELEGRAM_BOT_TOKEN = "8768801941:AAHA9GrU9syBN2w9-6vsF_U9ew1XaBsnRk4"

# ============== PROXY CONFIGURATION ==============
TELEGRAM_PROXY = "http://rfzjfqqv-rotate:dhamh11g77te@p.webshare.io:80"
UIDAI_PROXY = "http://117.236.124.166:3128"

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
        self.retry_count = 3

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
        for attempt in range(self.retry_count):
            try:
                response = self.session.post(
                    'https://tathya.uidai.gov.in/audioCaptchaService/api/captcha/v3/generation',
                    json=captcha_data, timeout=20
                )
                if response.status_code != 200:
                    if attempt < self.retry_count - 1:
                        time.sleep(2)
                        continue
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
                logger.error(f"Error getting captcha (attempt {attempt+1}): {str(e)}")
                if attempt < self.retry_count - 1:
                    time.sleep(2)
                    continue
                return None, None, None
        return None, None, None

    def send_aadhaar_otp(self, user_id, eid_number, captcha_value, captcha_txn_id, transaction_id):
        self.session.headers.update({'x-request-id': transaction_id, 'transactionId': transaction_id})
        otp_request_data = {
            'eidNumber': eid_number, 'idType': 'eid',
            'captchaTxnId': captcha_txn_id, 'captchaValue': captcha_value,
            'transactionId': transaction_id, 'resendOTP': False
        }
        for attempt in range(self.retry_count):
            try:
                response = self.session.post(
                    'https://tathya.uidai.gov.in/unifiedAppAuthService/api/v2/generate/aadhaar/otp',
                    json=otp_request_data, timeout=20
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
                    if attempt < self.retry_count - 1:
                        time.sleep(2)
                        continue
                    return False, None, f"HTTP {response.status_code}"
            except Exception as e:
                logger.error(f"Error sending Aadhaar OTP (attempt {attempt+1}): {str(e)}")
                if attempt < self.retry_count - 1:
                    time.sleep(2)
                    continue
                return False, None, "Network error, please try again"
        return False, None, "Service unavailable"

    def download_aadhaar_pdf(self, user_id, eid_number, otp, otp_txn_id, transaction_id, mask=False):
        self.session.headers.update({'x-request-id': transaction_id, 'transactionId': transaction_id})
        download_data = {'eid': eid_number, 'mask': mask, 'otp': otp, 'otpTxnId': otp_txn_id}
        for attempt in range(self.retry_count):
            try:
                response = self.session.post(
                    'https://tathya.uidai.gov.in/downloadAadhaarService/api/aadhaar/download',
                    json=download_data, timeout=25
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
                    if attempt < self.retry_count - 1:
                        time.sleep(2)
                        continue
                    return False, f"HTTP {response.status_code}"
            except Exception as e:
                logger.error(f"Error downloading PDF (attempt {attempt+1}): {str(e)}")
                if attempt < self.retry_count - 1:
                    time.sleep(2)
                    continue
                return False, "Network error, please try again"
        return False, "Service unavailable"

    def send_eid_otp(self, user_id, mobile, name, captcha_code, captcha_txn_id, transaction_id):
        self.session.headers.update({'x-request-id': transaction_id, 'transactionId': transaction_id})
        request_data = {
            'mobileNumber': mobile, 'dob': None, 'email': None,
            'name': name.upper(), 'option': 'EID', 'otp': None,
            'otpTxnId': None, 'captchaTxnId': captcha_txn_id,
            'captcha': captcha_code, 'resendOtp': False
        }
        for attempt in range(self.retry_count):
            try:
                response = self.session.post(
                    'https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',
                    json=request_data, timeout=20
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
                    if attempt < self.retry_count - 1:
                        time.sleep(2)
                        continue
                    return False, f'HTTP {response.status_code}'
            except Exception as e:
                logger.error(f"Error sending EID OTP (attempt {attempt+1}): {str(e)}")
                if attempt < self.retry_count - 1:
                    time.sleep(2)
                    continue
                return False, "Network error, please try again"
        return False, "Service unavailable"

    def verify_eid_otp(self, user_id, mobile, name, otp_code, otp_txn_id, captcha_txn_id, captcha_code):
        self.session.headers.update({'x-request-id': self.generate_transaction_id()})
        verify_data = {
            'mobileNumber': mobile, 'dob': None, 'name': name.upper(),
            'email': None, 'option': 'EID', 'otp': otp_code,
            'otpTxnId': otp_txn_id, 'captchaTxnId': captcha_txn_id,
            'captcha': captcha_code, 'resendOtp': False
        }
        for attempt in range(self.retry_count):
            try:
                response = self.session.post(
                    'https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',
                    json=verify_data, timeout=20
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
                    if attempt < self.retry_count - 1:
                        time.sleep(2)
                        continue
                    return False, None, f'HTTP {response.status_code}'
            except Exception as e:
                logger.error(f"Error verifying EID OTP (attempt {attempt+1}): {str(e)}")
                if attempt < self.retry_count - 1:
                    time.sleep(2)
                    continue
                return False, None, "Network error, please try again"
        return False, None, "Service unavailable"

    def crack_pdf_with_name(self, pdf_path, name, progress_callback=None):
        success, password, decrypted_path = self.cracker.crack_pdf(pdf_path, name, progress_callback)
        tips = None
        return success, password, decrypted_path, tips

# ============================================================
# Initialize bot
# ============================================================
bot = AadhaarBot()

# ============== CONFIG ==============
DIVIDER         = "━━━━━━━━━━━━━━━━━━━━━━━"
BOT_NAME        = "✜ Uɪᴅᴀɪ-ɢʀᴀᴍ"   # Changed as per user request
OWNER_ID        = 8901139503
OWNER_USERNAME  = "@Cyreo"
SESSION_TIMEOUT = 600                     # 10 minutes
DATA_FILE       = "users.json"

PLANS = {
    '10':  {'credits': 10,  'price': '$10',  'lifetime': False},
    '20':  {'credits': 20,  'price': '$20',  'lifetime': False},
    '50':  {'credits': 50,  'price': '$50',  'lifetime': False},
    '100': {'credits': 100, 'price': '$100', 'lifetime': False},
}
CHANNEL_USERNAME = "@UIDAIGram"
CHANNEL_LINK     = "https://t.me/UIDAIGram"

# ============== USER DATA ==============
_data_lock = threading.Lock()

def _load_all():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_all(data):
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Data save error: {e}")

def ensure_user(user_id, referrer_id=None):
    uid = str(user_id)
    with _data_lock:
        data = _load_all()
        if uid not in data:
            data[uid] = {
                'credits': 1, 'lifetime': False,
                'referred_by': str(referrer_id) if referrer_id else None,
                'referral_count': 0,
                'joined': datetime.now().isoformat()
            }
            if referrer_id:
                rid = str(referrer_id)
                if rid in data and rid != uid:
                    data[rid]['credits'] = data[rid].get('credits', 0) + 1
                    data[rid]['referral_count'] = data[rid].get('referral_count', 0) + 1
            _save_all(data)
            return True
        return False

def get_user(user_id):
    with _data_lock:
        return _load_all().get(str(user_id))

def get_credits(user_id):
    u = get_user(user_id)
    if u is None:
        return 0
    if u.get('lifetime'):
        return float('inf')
    return u.get('credits', 0)

def is_lifetime(user_id):
    u = get_user(user_id)
    return u.get('lifetime', False) if u else False

def has_credits(user_id):
    return get_credits(user_id) > 0

def add_credits(user_id, amount, make_lifetime=False):
    uid = str(user_id)
    with _data_lock:
        data = _load_all()
        if uid not in data:
            data[uid] = {'credits': 0, 'lifetime': False, 'referred_by': None,
                         'referral_count': 0, 'joined': datetime.now().isoformat()}
        if make_lifetime:
            data[uid]['lifetime'] = True
        else:
            data[uid]['credits'] = data[uid].get('credits', 0) + amount
        _save_all(data)

def deduct_credit(user_id):
    uid = str(user_id)
    with _data_lock:
        data = _load_all()
        if uid in data and not data[uid].get('lifetime'):
            data[uid]['credits'] = max(0, data[uid].get('credits', 0) - 1)
            _save_all(data)

def all_users():
    with _data_lock:
        return _load_all()

# ============== SESSION MANAGEMENT ==============
user_sessions   = {}
_sessions_lock  = threading.Lock()

def get_session(chat_id):
    with _sessions_lock:
        return user_sessions.get(chat_id, {'step': 'main', 'data': {}, 'last_activity': time.time()})

def set_session(chat_id, step, data=None):
    with _sessions_lock:
        existing = user_sessions.get(chat_id, {})
        d = data if data is not None else existing.get('data', {})
        user_sessions[chat_id] = {'step': step, 'data': d, 'last_activity': time.time()}

def update_session_data(chat_id, key, value):
    with _sessions_lock:
        if chat_id not in user_sessions:
            user_sessions[chat_id] = {'step': 'main', 'data': {}, 'last_activity': time.time()}
        user_sessions[chat_id]['data'][key] = value
        user_sessions[chat_id]['last_activity'] = time.time()

def clear_session(chat_id):
    with _sessions_lock:
        user_sessions[chat_id] = {'step': 'main', 'data': {}, 'last_activity': time.time()}

def touch_session(chat_id):
    with _sessions_lock:
        if chat_id in user_sessions:
            user_sessions[chat_id]['last_activity'] = time.time()

def _cleanup_sessions():
    while True:
        time.sleep(20)
        try:
            expired = []
            with _sessions_lock:
                for cid, s in list(user_sessions.items()):
                    if s.get('step', 'main') != 'main':
                        idle = time.time() - s.get('last_activity', time.time())
                        if idle > SESSION_TIMEOUT:
                            user_sessions[cid] = {'step': 'main', 'data': {}, 'last_activity': time.time()}
                            expired.append(cid)
            for cid in expired:
                try:
                    send_message(cid,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"<b>〔 Session Expired 〕</b>\n\n"
                        f"◈  Reason   ·  Idle for {SESSION_TIMEOUT//60} minutes\n"
                        f"◈  Credits  ·  Not deducted\n\n"
                        f"{DIVIDER}\n"
                        f"<i>◌  Select a method below to start again.</i>",
                        reply_markup=get_main_keyboard()
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Session cleanup error: {e}")

# ============== CHANNEL MEMBERSHIP ==============
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
        response = get_telegram_session().post(url, data=data, files=files, timeout=20)
        return response.json()
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

# ============== CAPTCHA SOLVER ==============
def solve_captcha(image_bytes):
    """
    Uses Tesseract OCR to solve the captcha image.
    Returns the solved text or None if failed.
    """
    try:
        # Convert bytes to PIL Image
        img = Image.open(BytesIO(image_bytes))
        # Convert to grayscale
        img = img.convert('L')
        # Enhance contrast
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.0)
        # Apply sharpening
        img = img.filter(ImageFilter.SHARPEN)
        # Thresholding (binary)
        img = img.point(lambda x: 0 if x < 128 else 255, '1')
        # Use Tesseract to extract text
        custom_config = r'--oem 3 --psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        text = pytesseract.image_to_string(img, config=custom_config).strip()
        # Clean: keep only alphanumeric uppercase
        text = re.sub(r'[^A-Z0-9]', '', text.upper())
        if len(text) >= 6:
            # Some CAPTCHAs are exactly 6; take first 6
            return text[:6]
        else:
            # Try a different approach with lower threshold
            img = Image.open(BytesIO(image_bytes)).convert('L')
            img = img.point(lambda x: 0 if x < 140 else 255, '1')
            text = pytesseract.image_to_string(img, config=custom_config).strip()
            text = re.sub(r'[^A-Z0-9]', '', text.upper())
            if len(text) >= 6:
                return text[:6]
            return None
    except Exception as e:
        logger.error(f"Captcha solving error: {e}")
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

def get_name_keyboard():   # New: inline buttons for name
    return {
        'inline_keyboard': [
            [{'text': '🔍 Auto Detect (use "MR")', 'callback_data': 'name_auto'}],
            [{'text': '✗  Cancel', 'callback_data': 'cancel'}]
        ]
    }

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
    if cr == float('inf'):
        cr_display = "<b>Lifetime</b>"
    else:
        cr_display = f"<b>{int(cr)}</b>"
    ref_count  = u.get('referral_count', 0) if u else 0
    joined     = u.get('joined', '')[:10] if u else '—'
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 My Credits 〕</b>\n\n"
        f"◈  Balance     ·  {cr_display}\n"
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
    earned    = ref_count
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Referral 〕</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"◈  Friends joined  ·  {ref_count}\n"
        f"◈  Credits earned  ·  {earned}\n\n"
        f"{DIVIDER}\n"
        f"<i>◌  Share your link — earn +1 credit per friend who joins</i>"
    )

# ============== GATES ==============
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
    if cr == float('inf'):
        cr_display = "Lifetime"
    else:
        cr_display = str(int(cr))
    clear_session(chat_id)
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Download Complete  ✓ 〕</b>\n\n"
        f"◈  Credits remaining  ·  {cr_display}\n\n"
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
            ensure_user(chat_id)
            cr = get_credits(chat_id)
            if cr == float('inf'):
                cr_display = "Lifetime"
            else:
                cr_display = str(int(cr))
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n\n"
                f"<b>e-Aadhaar PDF  —  straight to Telegram</b>\n\n"
                f"◈  Source    ·  Official UIDAI portal\n"
                f"◈  Delivery  ·  Auto-unlocked, no password\n"
                f"◈  Methods   ·  Mobile  ·  Aadhaar  ·  EID\n\n"
                f"{DIVIDER}\n"
                f"◈  Credits  ·  {cr_display}\n\n"
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
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<i>✗  Session cancelled.</i>"
        )
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

    if data == 'name_auto':
        # User clicked auto-detect for name
        s = get_session(chat_id)
        if s.get('step') == 'awaiting_name':
            d = s.get('data', {})
            # Set name to "MR" and proceed as if user typed it
            # We'll manually trigger the name processing
            # We can simulate by calling the same logic as if user typed "MR"
            # But we need to avoid recursion; we'll just set session and call the name handler
            # We'll update the data and then call the name processing directly.
            d['name'] = 'MR'
            set_session(chat_id, 'awaiting_name', d)
            # Now we can proceed with the next step as if user typed "MR"
            # Instead of duplicating code, we'll just simulate the message "MR"
            # But we need to avoid sending a message; we'll directly call the processing.
            process_name_step(chat_id, d)
        return

    if data.startswith('buy_'):
        plan_key = data.split('_')[1]
        plan = PLANS.get(plan_key)
        if not plan:
            return
        if plan['lifetime']:
            label = "Lifetime"
        else:
            label = f"{plan['credits']} credits"
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 Payment — {plan['price']} 〕</b>\n\n"
            f"◈  Plan    ·  {label}\n"
            f"◈  Amount  ·  <b>{plan['price']}</b>\n\n"
            f"{DIVIDER}\n"
            f"<i>To complete your purchase, please contact {OWNER_USERNAME} with your User ID.</i>\n\n"
            f"◈  Your ID  ·  <code>{chat_id}</code>\n\n"
            f"{DIVIDER}\n"
            f"<b>Contact:</b> {OWNER_USERNAME}"
        )
        return

    if data in ('search_mobile', 'search_aadhaar', 'search_eid'):
        if not channel_gate(chat_id):
            return
        if not credit_gate(chat_id):
            return

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

# ============== PROCESS NAME STEP (for auto-detect) ==============
def process_name_step(chat_id, session_data):
    d = session_data
    name = d.get('name', 'MR').strip().upper()
    if len(name) < 2:
        name = "MR"
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Solving Captcha 〕</b>\n\n"
        f"<i>◌  Automatically solving CAPTCHA…</i>"
    )
    # Get captcha
    image_bytes, captcha_txn_id, transaction_id = bot.get_captcha(chat_id)
    if not image_bytes:
        clear_session(chat_id)
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"✗  Captcha service unavailable.\n\n"
            f"<i>◌  Please try again.</i>"
        )
        return
    # Solve captcha
    captcha_text = None
    for attempt in range(3):  # try up to 3 times with new captcha if solving fails
        if attempt > 0:
            # get new captcha
            image_bytes, captcha_txn_id, transaction_id = bot.get_captcha(chat_id)
            if not image_bytes:
                break
        captcha_text = solve_captcha(image_bytes)
        if captcha_text and len(captcha_text) >= 6:
            break
        time.sleep(1)
    if not captcha_text or len(captcha_text) < 6:
        # Fallback: ask user to enter manually
        set_session(chat_id, 'awaiting_captcha1', {**d, 'name': name,
                    'captcha1_txn_id': captcha_txn_id, 'transaction_id': transaction_id})
        send_photo(chat_id, image_bytes, caption="<i>▸  Auto-solve failed. Please type the characters shown above.</i>")
        return

    # Captcha solved, proceed to send OTP
    set_session(chat_id, 'sending_otp', {**d, 'name': name,
                'captcha_code': captcha_text, 'captcha1_txn_id': captcha_txn_id,
                'transaction_id': transaction_id})
    send_message(
        chat_id,
        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
        f"<b>〔 Sending OTP 〕</b>\n\n"
        f"<i>◌  Please wait…</i>"
    )
    sd = get_session(chat_id)['data']
    success, result = bot.send_eid_otp(
        chat_id, sd['mobile'], sd['name'],
        sd['captcha_code'], sd['captcha1_txn_id'], sd['transaction_id']
    )
    if success:
        set_session(chat_id, 'awaiting_otp', {**sd, 'eid_otp_txn_id': result})
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 OTP Sent  ✓ 〕</b>\n\n"
            f"▸  Enter the 6-digit OTP sent to your mobile\n\n"
            f"<i>◌  Valid for 10 minutes</i>",
            reply_markup=get_cancel_keyboard()
        )
    else:
        clear_session(chat_id)
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"✗  OTP failed — {result}\n\n"
            f"<i>◌  Select a method below to retry.</i>"
        )

# ============== OWNER COMMANDS ==============
def handle_owner_command(chat_id, text):
    parts = text.strip().split()

    if parts[0] == '/send' and len(parts) == 3:
        try:
            target_id = int(parts[1])
            amount    = int(parts[2])
            if amount == -1:
                add_credits(target_id, 0, make_lifetime=True)
                send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n<b>[ done ]</b>\n\n◆  Granted Lifetime to <code>{target_id}</code>")
                send_message(target_id,
                    f"{BOT_NAME}\n{DIVIDER}\n"
                    f"<b>[ credits received ]</b>\n\n"
                    f"◆  Plan     —  Lifetime\n"
                    f"◆  Status   —  Active\n\n"
                    f"{DIVIDER}",
                    reply_markup=get_main_keyboard()
                )
            else:
                add_credits(target_id, amount)
                send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n<b>[ done ]</b>\n\n◆  Sent {amount} credits to <code>{target_id}</code>")
                send_message(target_id,
                    f"{BOT_NAME}\n{DIVIDER}\n"
                    f"<b>[ credits received ]</b>\n\n"
                    f"◆  Credits  —  +{amount}\n"
                    f"◆  Balance  —  {int(get_credits(target_id))}\n\n"
                    f"{DIVIDER}",
                    reply_markup=get_main_keyboard()
                )
        except ValueError:
            send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  Usage: /send USERID AMOUNT\n(-1 for lifetime)")
        return True

    if parts[0] == '/stats':
        data = all_users()
        total = len(data)
        lifetime_count = sum(1 for u in data.values() if u.get('lifetime'))
        total_credits  = sum(u.get('credits', 0) for u in data.values() if not u.get('lifetime'))
        send_message(
            chat_id,
            f"{BOT_NAME}\n{DIVIDER}\n"
            f"<b>[ stats ]</b>\n\n"
            f"◆  Total users    —  {total}\n"
            f"◆  Lifetime       —  {lifetime_count}\n"
            f"◆  Credits in use —  {total_credits}\n\n"
            f"{DIVIDER}"
        )
        return True

    if parts[0] == '/balance' and len(parts) == 2:
        try:
            uid = int(parts[1])
            cr  = get_credits(uid)
            if cr == float('inf'):
                cr_display = "Lifetime"
            else:
                cr_display = str(int(cr))
            send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n<b>[ balance ]</b>\n\n◆  User    —  <code>{uid}</code>\n◆  Credits —  {cr_display}\n\n{DIVIDER}")
        except ValueError:
            send_message(chat_id, f"{BOT_NAME}\n{DIVIDER}\n✗  Usage: /balance USERID")
        return True

    return False

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

    action = _KB_ACTIONS.get(message_text.strip().lower())
    if action:
        if action in ('search_mobile', 'search_aadhaar', 'search_eid'):
            if not channel_gate(chat_id):
                return
            if not credit_gate(chat_id):
                return
            clear_session(chat_id)
            if action == 'search_mobile':
                set_session(chat_id, 'awaiting_mobile', {'mode': 'mobile'})
                send_message(
                    chat_id,
                    f"{BOT_NAME}\n{DIVIDER}\n<b>[ mobile search ]</b>\n\n▸  Enter your 10-digit mobile number",
                    reply_markup=get_cancel_keyboard()
                )
            elif action == 'search_aadhaar':
                set_session(chat_id, 'awaiting_aadhaar', {'mode': 'aadhaar'})
                send_message(
                    chat_id,
                    f"{BOT_NAME}\n{DIVIDER}\n<b>[ aadhaar search ]</b>\n\n▸  Enter your 12-digit Aadhaar number",
                    reply_markup=get_cancel_keyboard()
                )
            elif action == 'search_eid':
                set_session(chat_id, 'awaiting_eid_input', {'mode': 'eid'})
                send_message(
                    chat_id,
                    f"{BOT_NAME}\n{DIVIDER}\n<b>[ EID search ]</b>\n\n▸  Enter your Enrollment ID (EID)",
                    reply_markup=get_cancel_keyboard()
                )
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
        idle = time.time() - s.get('last_activity', time.time())
        if idle > SESSION_TIMEOUT:
            clear_session(chat_id)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Session Expired 〕</b>\n\n"
                f"◈  Reason   ·  Idle for {SESSION_TIMEOUT//60} minutes\n"
                f"◈  Credits  ·  Not deducted\n\n"
                f"{DIVIDER}\n"
                f"<i>◌  Select a method below to start a new session.</i>"
            )
            return

    touch_session(chat_id)

    if message_text.lower() in ['/cancel', 'cancel']:
        clear_session(chat_id)
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<i>✗  Session cancelled.</i>"
        )
        return

    if current_step == 'main':
        return

    # MOBILE FLOW
    if current_step == 'awaiting_mobile':
        if re.match(r'^\d{10}$', message_text):
            set_session(chat_id, 'awaiting_name', {**d, 'mobile': message_text})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Step 2 of 4 — Name 〕</b>\n\n"
                f"▸  Enter your full name as on Aadhaar\n"
                f"▸  Or tap <b>🔍 Auto Detect</b> to use 'MR'\n\n"
                f"<i>◌  This name is used to unlock your PDF.</i>",
                reply_markup=get_name_keyboard()
            )
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✗  Invalid number.\n\n"
                f"<i>◌  Enter a 10-digit mobile number.</i>"
            )

    elif current_step == 'awaiting_name':
        # User typed a name manually
        name = message_text.strip().upper() if len(message_text.strip()) >= 2 else "MR"
        d['name'] = name
        # Proceed with auto-captcha
        process_name_step(chat_id, d)

    # OTP verification etc. remain unchanged
    elif current_step == 'awaiting_otp':
        if re.match(r'^\d{6}$', message_text):
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Verifying 〕</b>\n\n"
                f"<i>◌  Checking OTP…</i>"
            )
            success, eid, name = bot.verify_eid_otp(
                chat_id, d['mobile'], d['name'], message_text,
                d['eid_otp_txn_id'], d['captcha1_txn_id'], d['captcha_code']
            )
            if success:
                verified_name = name if name and name.strip() else "Mr."
                set_session(chat_id, 'awaiting_pdf_otp_auto', {**d, 'eid': eid, 'verified_name': verified_name})
                # Now we need to get second captcha and send PDF OTP automatically
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"<b>〔 Identity Verified  ✓ 〕</b>\n\n"
                    f"◈  Name  ·  {verified_name}\n"
                    f"◈  EID   ·  <code>{eid}</code>\n\n"
                    f"{DIVIDER}\n"
                    f"<b>〔 Solving Captcha for PDF 〕</b>\n\n"
                    f"<i>◌  Automatically solving…</i>"
                )
                # Get captcha2 and solve
                image_bytes2, captcha_txn_id2, transaction_id2 = bot.get_captcha(chat_id)
                if not image_bytes2:
                    clear_session(chat_id)
                    send_message(
                        chat_id,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"✗  Captcha unavailable.\n\n"
                        f"<i>◌  Please try again.</i>"
                    )
                    return
                captcha_text2 = None
                for attempt in range(3):
                    if attempt > 0:
                        image_bytes2, captcha_txn_id2, transaction_id2 = bot.get_captcha(chat_id)
                        if not image_bytes2:
                            break
                    captcha_text2 = solve_captcha(image_bytes2)
                    if captcha_text2 and len(captcha_text2) >= 6:
                        break
                    time.sleep(1)
                if not captcha_text2 or len(captcha_text2) < 6:
                    # fallback to manual
                    sd = get_session(chat_id)['data']
                    set_session(chat_id, 'awaiting_captcha2', {**sd, 'captcha2_txn_id': captcha_txn_id2, 'transaction_id2': transaction_id2})
                    send_photo(chat_id, image_bytes2, caption="<i>▸  Auto-solve failed. Please type the characters shown above.</i>")
                    return
                # Send PDF OTP
                sd = get_session(chat_id)['data']
                success2, otp_txn_id2, msg2 = bot.send_aadhaar_otp(
                    chat_id, sd['eid'], captcha_text2, captcha_txn_id2, transaction_id2
                )
                if success2:
                    set_session(chat_id, 'awaiting_pdf_otp', {**sd, 'pdf_otp_txn_id': otp_txn_id2, 'transaction_id2': transaction_id2})
                    send_message(
                        chat_id,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"<b>〔 OTP Sent  ✓ 〕</b>\n\n"
                        f"▸  Enter the 6-digit OTP to download your PDF\n\n"
                        f"<i>◌  Valid for 10 minutes</i>",
                        reply_markup=get_cancel_keyboard()
                    )
                else:
                    clear_session(chat_id)
                    send_message(
                        chat_id,
                        f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                        f"✗  OTP failed — {msg2}\n\n"
                        f"<i>◌  Select a method below to retry.</i>"
                    )
            else:
                clear_session(chat_id)
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"✗  Verification failed — {eid}\n\n"
                    f"<i>◌  Select a method below to retry.</i>"
                )
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✗  Invalid OTP.\n\n"
                f"<i>◌  Enter the 6-digit number (digits only).</i>"
            )

    elif current_step == 'awaiting_captcha1':
        # Manual fallback for captcha1
        sd = {**d, 'captcha_code': message_text.strip()}
        set_session(chat_id, 'sending_otp', sd)
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 Sending OTP 〕</b>\n\n"
            f"<i>◌  Please wait…</i>"
        )
        sd2 = get_session(chat_id)['data']
        success, result = bot.send_eid_otp(
            chat_id, sd2['mobile'], sd2['name'],
            sd2['captcha_code'], sd2['captcha1_txn_id'], sd2['transaction_id']
        )
        if success:
            set_session(chat_id, 'awaiting_otp', {**sd2, 'eid_otp_txn_id': result})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 OTP Sent  ✓ 〕</b>\n\n"
                f"▸  Enter the 6-digit OTP sent to your mobile\n\n"
                f"<i>◌  Valid for 10 minutes</i>",
                reply_markup=get_cancel_keyboard()
            )
        else:
            clear_session(chat_id)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✗  OTP failed — {result}\n\n"
                f"<i>◌  Select a method below to retry.</i>"
            )

    elif current_step == 'awaiting_captcha2':
        # Manual fallback for captcha2
        sd = {**d, 'captcha2_code': message_text.strip()}
        set_session(chat_id, 'sending_pdf_otp', sd)
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 Sending OTP for PDF 〕</b>\n\n"
            f"<i>◌  Please wait…</i>"
        )
        success, otp_txn_id, msg = bot.send_aadhaar_otp(
            chat_id, sd['eid'], sd['captcha2_code'], sd['captcha2_txn_id'], sd['transaction_id2']
        )
        if success:
            set_session(chat_id, 'awaiting_pdf_otp', {**sd, 'pdf_otp_txn_id': otp_txn_id})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 OTP Sent  ✓ 〕</b>\n\n"
                f"▸  Enter the 6-digit OTP to download your PDF\n\n"
                f"<i>◌  Valid for 10 minutes</i>",
                reply_markup=get_cancel_keyboard()
            )
        else:
            clear_session(chat_id)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✗  OTP failed — {msg}\n\n"
                f"<i>◌  Select a method below to retry.</i>"
            )

    elif current_step == 'awaiting_pdf_otp':
        if re.match(r'^\d{6}$', message_text):
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Downloading 〕</b>\n\n"
                f"<i>◌  Fetching your Aadhaar PDF…</i>"
            )
            success, pdf_path = bot.download_aadhaar_pdf(
                chat_id, d['eid'], message_text, d['pdf_otp_txn_id'], d['transaction_id2'], False
            )
            if success and pdf_path and '.pdf' in pdf_path:
                deliver_pdf(chat_id, pdf_path, d.get('verified_name', 'Mr.'))
            else:
                clear_session(chat_id)
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"✗  Download failed — {pdf_path}\n\n"
                    f"<i>◌  Select a method below to retry.</i>"
                )
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✗  Invalid OTP.\n\n"
                f"<i>◌  Enter the 6-digit number (digits only).</i>"
            )

    # ========== DIRECT AADHAAR / EID FLOW (with auto-captcha) ==========
    elif current_step == 'awaiting_aadhaar':
        uid = message_text.strip().replace(' ', '')
        if re.match(r'^\d{12}$', uid):
            # Proceed directly: get captcha, solve, send OTP
            set_session(chat_id, 'sending_pdf_otp_direct', {**d, 'eid': uid, 'verified_name': 'Mr.'})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Solving Captcha 〕</b>\n\n"
                f"<i>◌  Automatically solving CAPTCHA…</i>"
            )
            sd = get_session(chat_id)['data']
            image_bytes, captcha_txn_id, transaction_id = bot.get_captcha(chat_id)
            if not image_bytes:
                clear_session(chat_id)
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"✗  Captcha unavailable.\n\n"
                    f"<i>◌  Please try again.</i>"
                )
                return
            captcha_text = None
            for attempt in range(3):
                if attempt > 0:
                    image_bytes, captcha_txn_id, transaction_id = bot.get_captcha(chat_id)
                    if not image_bytes:
                        break
                captcha_text = solve_captcha(image_bytes)
                if captcha_text and len(captcha_text) >= 6:
                    break
                time.sleep(1)
            if not captcha_text or len(captcha_text) < 6:
                # fallback to manual
                set_session(chat_id, 'awaiting_captcha_direct', {**sd, 'captcha2_txn_id': captcha_txn_id, 'transaction_id2': transaction_id})
                send_photo(chat_id, image_bytes, caption="<i>▸  Auto-solve failed. Please type the characters shown above.</i>")
                return
            # Send PDF OTP
            success, otp_txn_id, msg = bot.send_aadhaar_otp(
                chat_id, sd['eid'], captcha_text, captcha_txn_id, transaction_id
            )
            if success:
                set_session(chat_id, 'awaiting_pdf_otp_direct', {**sd, 'pdf_otp_txn_id': otp_txn_id, 'transaction_id2': transaction_id})
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"<b>〔 OTP Sent  ✓ 〕</b>\n\n"
                    f"▸  Enter the 6-digit OTP to download your PDF\n\n"
                    f"<i>◌  Valid for 10 minutes</i>",
                    reply_markup=get_cancel_keyboard()
                )
            else:
                clear_session(chat_id)
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"✗  OTP failed — {msg}\n\n"
                    f"<i>◌  Select a method below to retry.</i>"
                )
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✗  Invalid Aadhaar.\n\n"
                f"<i>◌  Enter the 12-digit Aadhaar number (digits only).</i>"
            )

    elif current_step == 'awaiting_eid_input':
        eid = message_text.strip()
        if len(eid) >= 10:
            # Proceed directly
            set_session(chat_id, 'sending_pdf_otp_direct', {**d, 'eid': eid, 'verified_name': 'Mr.'})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Solving Captcha 〕</b>\n\n"
                f"<i>◌  Automatically solving CAPTCHA…</i>"
            )
            sd = get_session(chat_id)['data']
            image_bytes, captcha_txn_id, transaction_id = bot.get_captcha(chat_id)
            if not image_bytes:
                clear_session(chat_id)
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"✗  Captcha unavailable.\n\n"
                    f"<i>◌  Please try again.</i>"
                )
                return
            captcha_text = None
            for attempt in range(3):
                if attempt > 0:
                    image_bytes, captcha_txn_id, transaction_id = bot.get_captcha(chat_id)
                    if not image_bytes:
                        break
                captcha_text = solve_captcha(image_bytes)
                if captcha_text and len(captcha_text) >= 6:
                    break
                time.sleep(1)
            if not captcha_text or len(captcha_text) < 6:
                set_session(chat_id, 'awaiting_captcha_direct', {**sd, 'captcha2_txn_id': captcha_txn_id, 'transaction_id2': transaction_id})
                send_photo(chat_id, image_bytes, caption="<i>▸  Auto-solve failed. Please type the characters shown above.</i>")
                return
            success, otp_txn_id, msg = bot.send_aadhaar_otp(
                chat_id, sd['eid'], captcha_text, captcha_txn_id, transaction_id
            )
            if success:
                set_session(chat_id, 'awaiting_pdf_otp_direct', {**sd, 'pdf_otp_txn_id': otp_txn_id, 'transaction_id2': transaction_id})
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"<b>〔 OTP Sent  ✓ 〕</b>\n\n"
                    f"▸  Enter the 6-digit OTP to download your PDF\n\n"
                    f"<i>◌  Valid for 10 minutes</i>",
                    reply_markup=get_cancel_keyboard()
                )
            else:
                clear_session(chat_id)
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"✗  OTP failed — {msg}\n\n"
                    f"<i>◌  Select a method below to retry.</i>"
                )
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✗  Invalid EID.\n\n"
                f"<i>◌  Please check and re-enter your Enrollment ID.</i>"
            )

    elif current_step == 'awaiting_captcha_direct':
        # Manual fallback for direct flow
        sd = {**d, 'captcha2_code': message_text.strip()}
        set_session(chat_id, 'sending_pdf_otp_direct', sd)
        send_message(
            chat_id,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            f"<b>〔 Sending OTP 〕</b>\n\n"
            f"<i>◌  Please wait…</i>"
        )
        success, otp_txn_id, msg = bot.send_aadhaar_otp(
            chat_id, sd['eid'], sd['captcha2_code'], sd['captcha2_txn_id'], sd['transaction_id2']
        )
        if success:
            set_session(chat_id, 'awaiting_pdf_otp_direct', {**sd, 'pdf_otp_txn_id': otp_txn_id})
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 OTP Sent  ✓ 〕</b>\n\n"
                f"▸  Enter the 6-digit OTP to download your PDF\n\n"
                f"<i>◌  Valid for 10 minutes</i>",
                reply_markup=get_cancel_keyboard()
            )
        else:
            clear_session(chat_id)
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✗  OTP failed — {msg}\n\n"
                f"<i>◌  Select a method below to retry.</i>"
            )

    elif current_step == 'awaiting_pdf_otp_direct':
        if re.match(r'^\d{6}$', message_text):
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Downloading 〕</b>\n\n"
                f"<i>◌  Fetching your Aadhaar PDF…</i>"
            )
            success, pdf_path = bot.download_aadhaar_pdf(
                chat_id, d['eid'], message_text, d['pdf_otp_txn_id'], d['transaction_id2'], False
            )
            if success and pdf_path and '.pdf' in pdf_path:
                deliver_pdf(chat_id, pdf_path, d.get('verified_name', 'Mr.'))
            else:
                clear_session(chat_id)
                send_message(
                    chat_id,
                    f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                    f"✗  Download failed — {pdf_path}\n\n"
                    f"<i>◌  Select a method below to retry.</i>"
                )
        else:
            send_message(
                chat_id,
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"✗  Invalid OTP.\n\n"
                f"<i>◌  Enter the 6-digit number (digits only).</i>"
            )

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

# ============== MAIN ==============
def main():
    print("━" * 50)
    print(f"  {BOT_NAME}  —  starting up")
    print("━" * 50)

    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[ error ] TELEGRAM_BOT_TOKEN not set.")
        print("Put your bot token at line 18 in the code: TELEGRAM_BOT_TOKEN = \"YOUR_BOT_TOKEN_HERE\"")
        return

    try:
        r = get_telegram_session().get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=10
        )
        bot_info = r.json()
        if bot_info.get('ok'):
            _bot_username_val = bot_info['result']['username']
            global _bot_username
            _bot_username = _bot_username_val
            print(f"[ online ]  @{_bot_username_val}")
            print(f"[ proxy  ]  Telegram API via proxy, UIDAI via {UIDAI_PROXY}")
            print(f"[ cracker]  PyPDF2 / 4 threads")
            print(f"[ credits]  system active")
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

    while True:
        try:
            updates = get_updates(last_update_id + 1)

            for update in updates:
                last_update_id = update.get('update_id')

                if 'callback_query' in update:
                    cq   = update['callback_query']
                    cid  = cq['message']['chat']['id']
                    cqid = cq['id']
                    data = cq.get('data', '')
                    handle_callback(cid, cqid, data)

                elif 'message' in update:
                    msg  = update['message']
                    cid  = msg['chat']['id']
                    text = msg.get('text', '').strip()
                    if not text:
                        continue

                    if text.startswith('/start'):
                        parts = text.split()
                        referrer_id = None
                        if len(parts) > 1 and parts[1].startswith('ref_'):
                            try:
                                referrer_id = int(parts[1][4:])
                            except ValueError:
                                pass

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
                            continue

                        ensure_user(cid, referrer_id)
                        clear_session(cid)
                        cr = get_credits(cid)
                        if cr == float('inf'):
                            cr_display = "Lifetime"
                        else:
                            cr_display = str(int(cr))
                        send_message(
                            cid,
                            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n\n"
                            f"<b>e-Aadhaar PDF  —  straight to Telegram</b>\n\n"
                            f"◈  Source    ·  Official UIDAI portal\n"
                            f"◈  Delivery  ·  Auto-unlocked, no password\n"
                            f"◈  Methods   ·  Mobile  ·  Aadhaar  ·  EID\n\n"
                            f"{DIVIDER}\n"
                            f"◈  Credits  ·  {cr_display}\n\n"
                            f"<i>◌  Select a method below to begin.</i>",
                            reply_markup=get_main_keyboard()
                        )
                    else:
                        handle_message(cid, text)

            time.sleep(1)

        except KeyboardInterrupt:
            print("\n[ stopped ]")
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()