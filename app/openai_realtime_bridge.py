import asyncio
import base64
import json
import os
import re
import time
import struct
import uuid
import audioop
from pathlib import Path

import websockets
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = os.getenv("ENV_FILE", str(BASE_DIR / ".env"))
load_dotenv(env_path, override=True)

from app.security_guard import check_rate_limit, log_security_event, reset_rate_limit, is_locked
from app.config import (
    validate_bridge_config,
    OPENAI_API_KEY,
    OPENAI_REALTIME_MODEL,
    MAX_CONCURRENT_CALLS,
    CALL_MAX_SECONDS,
    VAD_THRESHOLD,
    VAD_SILENCE_MS,
    VAD_IDLE_TIMEOUT_MS,
    ASTERISK_QUEUE_STANDARD,
    ASTERISK_QUEUE_EXECUTIVE,
    ASTERISK_QUEUE_EMERGENCY,
)
from app.call_logger import (
    create_call,
    rename_call_id,
    update_call,
    close_call as log_close_call,
    reconcile_stale_calls,
)
from app.verify import verify_user, lookup_caller_by_phone
from app.transfer import transfer_call, check_queue_availability, get_active_channel_info
from app.ticketing import get_ticketing_client

CALLS_PER_NUMBER_LIMIT = int(os.getenv("CALLS_PER_NUMBER_LIMIT", "5"))
CALLS_PER_NUMBER_WINDOW = int(os.getenv("CALLS_PER_NUMBER_WINDOW", "600"))
CALLS_PER_NUMBER_LOCK = int(os.getenv("CALLS_PER_NUMBER_LOCK", "900"))

VERIFY_FAIL_LIMIT = int(os.getenv("VERIFY_FAIL_LIMIT", "5"))
VERIFY_FAIL_WINDOW = int(os.getenv("VERIFY_FAIL_WINDOW", "3600"))
VERIFY_FAIL_LOCK = int(os.getenv("VERIFY_FAIL_LOCK", "3600"))

ASTERISK_WS_HOST = os.getenv("ASTERISK_WS_HOST", "127.0.0.1")
ASTERISK_WS_PORT = int(os.getenv("ASTERISK_WS_PORT", "8765"))
ASTERISK_WS_SSL_CERT = os.getenv("ASTERISK_WS_SSL_CERT", "").strip()
ASTERISK_WS_SSL_KEY = os.getenv("ASTERISK_WS_SSL_KEY", "").strip()

ACTIVE_CALLS = 0
ACTIVE_CALLS_LOCK = asyncio.Lock()

OPENAI_WS_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"

AUTO_TICKET_CATEGORIES = {
    "keyboard_issue",
    "mouse_issue",
    "hardware_issue",
    "hardware_damage",
    "printer_replacement",
    "monitor_issue",
    "dock_issue",
    "accessory_issue",
    "charger_issue",
    "headset_issue",
}

EMERGENCY_KEYWORDS = {
    "outage", "system down", "core banking", "ransomware", "hacked", "breach",
    "data center", "datacenter", "fire", "server down", "network down",
    "network outage", "internet down", "internet outage", "total outage",
    "branch down", "payment gateway", "emergency", "طوارئ", "توقف النظام", "النظام متعطل",
    "انقطاع الشبكة", "الشبكة متوقفة", "انقطاع الانترنت",
}


def load_knowledge_base():
    kb = {}
    kb_dir = BASE_DIR / "knowledge_base"
    if kb_dir.is_dir():
        for file in kb_dir.glob("*.md"):
            try:
                kb[file.stem.lower()] = file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[KB] Error loading {file}: {e}")
    return kb


KNOWLEDGE_BASE = load_knowledge_base()

KB_METADATA = {
    "wifi_issue": {
        "title": "Wi-Fi / Network Connectivity Issues",
        "keywords_en": [
            "wifi", "wi-fi", "internet", "network", "connection", "disconnect", "reconnect",
            "offline", "hotspot", "signal", "ethernet", "lan", "wlan", "ssid", "no internet",
            "limited connectivity", "cable", "unplugged", "router"
        ],
        "keywords_ar": [
            "واي فاي", "وايفاي", "انترنت", "إنترنت", "شبكة", "اتصال", "انقطاع", "غير متصل",
            "النت", "الوايرلس", "فصل النت", "ما يشبك", "كيبل الشبكة", "راوتر"
        ],
    },
    "printer_issue": {
        "title": "Printer / Printing Issues",
        "keywords_en": [
            "printer", "printing", "print", "paper jam", "spooler", "scanner", "copier",
            "toner", "cartridge", "cannot print", "driver", "offline printer"
        ],
        "keywords_ar": [
            "طابعة", "طباعة", "طابعه", "سكانر", "ماسح", "حبر", "ورق", "تعليق الورق",
            "مشكلة في الطابعة", "ما تطبع", "طابعات", "تصوير", "طباعه"
        ],
    },
    "password_reset": {
        "title": "Password Reset & Credential Expiry",
        "keywords_en": [
            "password", "reset password", "forgot password", "change password", "expired password",
            "credentials", "login failed", "new password", "passcode"
        ],
        "keywords_ar": [
            "كلمة المرور", "باسوورد", "باسورد", "كلمة السر", "نسيت كلمة السر",
            "تغيير كلمة المرور", "انتهاء كلمة السر", "تعديل الباسوورد", "تغيير الباسورد", "نسيت الباسورد"
        ],
    },
    "account_locked": {
        "title": "Domain Account Lockout / Active Directory Locked",
        "keywords_en": [
            "locked", "lockout", "account locked", "disabled", "access denied", "locked out",
            "ad lock", "domain locked", "user locked"
        ],
        "keywords_ar": [
            "مقفل", "مغلق", "الحساب مقفل", "تم قفل الحساب", "حسابي مقفل", "بلوك",
            "قفل الحساب", "حسابي مغلق", "معطل"
        ],
    },
    "ad_lockout": {
        "title": "Active Directory Account Lockout Diagnostic",
        "keywords_en": [
            "ad lockout", "active directory lockout", "bad password attempts", "domain controller lock"
        ],
        "keywords_ar": [
            "قفل الدومين", "اكتيف دايركتوري", "محاولات تسجيل دخول خاطئة"
        ],
    },
    "outlook_issue": {
        "title": "Microsoft Outlook & Email Issues",
        "keywords_en": [
            "outlook", "email", "mail", "inbox", "pst", "ost", "send receive", "exchange",
            "mailbox full", "cannot send email", "not receiving emails"
        ],
        "keywords_ar": [
            "اوتلوك", "آوتلوك", "بريد", "ايميل", "إيميل", "رسائل", "صندوق الوارد",
            "مشكلة البريد", "ارسال ايميل", "استقبال ايميل", "الايميلات"
        ],
    },
    "teams_issue": {
        "title": "Microsoft Teams & Virtual Meetings",
        "keywords_en": [
            "teams", "microsoft teams", "meeting", "call", "screen share", "camera", "microphone",
            "mic", "headset", "teams meeting", "teams audio"
        ],
        "keywords_ar": [
            "تيمز", "مايكروسوفت تيمز", "اجتماع", "مكالمة", "مايك", "كاميرا", "صوت",
            "مشاركة الشاشة", "ميتينج", "تطبيق تيمز"
        ],
    },
    "vpn_issue": {
        "title": "VPN & Remote Connectivity",
        "keywords_en": [
            "vpn", "forticlient", "cisco anyconnect", "remote access", "home connection", "tunnel",
            "work from home", "wfh", "gateway"
        ],
        "keywords_ar": [
            "في بي ان", "الفي بي ان", "اتصال عن بعد", "العمل من المنزل", "الربط الخارجي",
            "ريموت اكسس", "بوابة الاتصال"
        ],
    },
    "slow_computer": {
        "title": "Slow Computer & System Performance",
        "keywords_en": [
            "slow", "freezing", "frozen", "lag", "performance", "hang", "stuck", "high cpu",
            "memory", "sluggish", "crash", "rebooting", "blue screen", "pc slow", "laptop slow"
        ],
        "keywords_ar": [
            "بطيء", "بطء", "معلق", "تعليق", "الجهاز بطيء", "لا يستجيب", "تهنيج", "ثقيل",
            "اللاب توب بطيء", "الكمبيوتر معلق", "بطء الجهاز"
        ],
    },
    "mfa_issue": {
        "title": "Multi-Factor Authentication (MFA / 2FA) Issues",
        "keywords_en": [
            "mfa", "2fa", "authenticator", "otp", "verification code", "sms code",
            "microsoft authenticator", "token"
        ],
        "keywords_ar": [
            "التحقق الثنائي", "رمز التحقق", "او تي بي", "تطبيق المصادقة", "رمز الدخول",
            "كود التحقق", "المصادقة الثنائية"
        ],
    },
    "software_request": {
        "title": "Software Installation & License Request",
        "keywords_en": [
            "software", "install", "application", "license", "download", "setup", "program",
            "request software", "install app"
        ],
        "keywords_ar": [
            "تثبيت برنامج", "برنامج", "تطبيق", "ترخيص", "تحميل", "تنزيل برنامج",
            "طلب برنامج", "تنصيب"
        ],
    },
    "fileshare_access": {
        "title": "Network File Share & Shared Folder Access",
        "keywords_en": [
            "file share", "shared folder", "drive", "network drive", "nas", "permission",
            "mapped drive", "z drive", "shared drive", "folder access"
        ],
        "keywords_ar": [
            "مجلد مشترك", "شير فولدر", "صلاحيات", "درايف", "ملفات مشتركة",
            "مجلدات الشبكة", "مشاركة الملفات"
        ],
    },
    "mobile_device": {
        "title": "Mobile Device Management (MDM / Intune)",
        "keywords_en": [
            "mobile", "phone", "iphone", "android", "intune", "company portal", "mdm",
            "work profile", "mobile email"
        ],
        "keywords_ar": [
            "جوال", "هاتف", "ايفون", "اندرويد", "انتيون", "هاتف العمل", "ايميل الجوال"
        ],
    },
    "windows_update": {
        "title": "Windows Update & OS Patching",
        "keywords_en": [
            "update", "windows update", "patch", "restart pending", "windows 11",
            "upgrade", "cumulative update"
        ],
        "keywords_ar": [
            "تحديث الويندوز", "ويندوز ابديت", "ترقية النظام", "تحديثات النظام", "تحديث ويندوز"
        ],
    },
}


def normalize_text_for_search(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    # Normalize Arabic alefs, hamzas, and taa marbuta
    t = re.sub(r"[إأآا]", "ا", t)
    t = re.sub(r"[ة]", "ه", t)
    t = re.sub(r"[ى]", "ي", t)
    # Strip punctuation except alphanumeric and space
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


_KB_CACHE = {}
_KB_CACHE_TIMESTAMP = 0
_KB_CACHE_TTL = 15  # Refresh every 15 seconds from DB for instant reflection without daemon restart


def invalidate_knowledge_cache():
    """Forces cache refresh on next request."""
    global _KB_CACHE_TIMESTAMP
    _KB_CACHE_TIMESTAMP = 0


def get_active_knowledge_articles() -> dict:
    """
    Returns active knowledge base articles from enterprise database (knowledge_articles).
    Cached for 15 seconds to ensure near-instant reflection of admin edits without daemon restart,
    while avoiding database connection pressure during real-time voice streaming.
    Falls back to file-based KNOWLEDGE_BASE and KB_METADATA if DB query fails or table is empty.
    """
    global _KB_CACHE, _KB_CACHE_TIMESTAMP
    now = time.time()
    if _KB_CACHE and (now - _KB_CACHE_TIMESTAMP < _KB_CACHE_TTL):
        return _KB_CACHE

    try:
        from app.db import list_knowledge_articles
        db_articles = list_knowledge_articles(active_only=True)
        if db_articles:
            new_cache = {}
            for a in db_articles:
                aid = str(a.get("article_id") or "").strip().lower()
                if not aid:
                    continue
                raw_en = a.get("keywords_en") or ""
                raw_ar = a.get("keywords_ar") or ""
                kw_en = [k.strip() for k in raw_en.split(",") if k.strip()] if isinstance(raw_en, str) else list(raw_en)
                kw_ar = [k.strip() for k in raw_ar.split(",") if k.strip()] if isinstance(raw_ar, str) else list(raw_ar)

                new_cache[aid] = {
                    "article_id": aid,
                    "title": a.get("title") or aid.replace("_", " ").title(),
                    "category": a.get("category") or "General IT",
                    "keywords_en": kw_en,
                    "keywords_ar": kw_ar,
                    "content": a.get("content") or "",
                }
            if new_cache:
                _KB_CACHE = new_cache
                _KB_CACHE_TIMESTAMP = now
                return _KB_CACHE
    except Exception as exc:
        print(f"[KB] Error loading active articles from DB: {exc}")

    # Fallback to local files & KB_METADATA
    if not _KB_CACHE:
        fallback_cache = {}
        for aid, content in KNOWLEDGE_BASE.items():
            meta = KB_METADATA.get(aid, {})
            fallback_cache[aid] = {
                "article_id": aid,
                "title": meta.get("title", aid.replace("_", " ").title()),
                "category": "General IT",
                "keywords_en": meta.get("keywords_en", []),
                "keywords_ar": meta.get("keywords_ar", []),
                "content": content,
            }
        _KB_CACHE = fallback_cache
        _KB_CACHE_TIMESTAMP = now

    return _KB_CACHE


def search_knowledge_base(query: str) -> dict:
    if not query:
        return {"found": False, "message": "Query was empty."}

    articles = get_active_knowledge_articles()
    if not articles:
        return {
            "found": False,
            "message": "No active knowledge base playbooks available."
        }

    norm_query = normalize_text_for_search(query)
    tokens = set(norm_query.split())

    best_score = 0
    best_id = None

    for kb_id, data in articles.items():
        score = 0
        norm_id = normalize_text_for_search(kb_id.replace("_", " "))

        # 1. Exact or substring key match
        if norm_id in norm_query or norm_query in norm_id:
            score += 60

        title = data.get("title", "")
        norm_title = normalize_text_for_search(title)
        if norm_query in norm_title:
            score += 50

        # 2. Match keywords and synonyms (both English and Arabic)
        all_kw = data.get("keywords_en", []) + data.get("keywords_ar", [])
        for kw in all_kw:
            norm_kw = normalize_text_for_search(kw)
            if norm_kw and (norm_kw in norm_query or norm_query in norm_kw):
                score += 40
            elif norm_kw:
                kw_tokens = set(norm_kw.split())
                common = tokens.intersection(kw_tokens)
                if common:
                    score += len(common) * 20

        # 3. Content token overlap
        content = data.get("content", "")
        norm_content = normalize_text_for_search(content[:600])
        for token in tokens:
            if len(token) > 2 and token in norm_content:
                score += 5

        if score > best_score:
            best_score = score
            best_id = kb_id

    # Fallback substring scan
    if best_score < 25:
        for k, data in articles.items():
            if query.strip().lower() in k or k in query.strip().lower():
                return {
                    "found": True,
                    "playbook_id": k,
                    "title": data.get("title", k),
                    "playbook": data.get("content", "")[:1200],
                    "score": 25,
                }
        return {
            "found": False,
            "message": "No specific local playbook found. Use standard IT troubleshooting questions."
        }

    return {
        "found": True,
        "playbook_id": best_id,
        "title": articles[best_id].get("title", best_id),
        "playbook": articles[best_id].get("content", "")[:1200],
        "score": best_score,
    }

SYSTEM_PROMPT = """
You are Arif, an AI IT Support voice agent for National Finance IT Support team.

CRITICAL OPERATIONAL RULES & PROTOCOLS:

0. MANDATORY STRICT INITIAL LANGUAGE SELECTION GATE:
- The initial greeting explicitly prompts: "Welcome to National Finance IT Support. For English please say English. للغة العربية قل عربي."
- You MUST wait in silence for the caller to indicate their preferred language.
- DO NOT answer questions, start troubleshooting, ask for employee ID, or speak until the language is confirmed!
- NEVER call set_language, speak, or assume a language on silence, background breathing, or ambient noise. If no choice is heard yet, REMAIN SILENT until the caller speaks.
- If the caller says "English" or speaks in English:
  1. Immediately call the tool: set_language(language="en").
  2. Reply strictly in English: "Thank you for choosing English. May I please have your full name?"
  3. From this point forward, you must speak STRICTLY AND ONLY in English. Do not speak any Arabic.
- If the caller says "Arabic" or "عربي" or speaks in Arabic:
  1. Immediately call the tool: set_language(language="ar").
  2. Reply strictly in Gulf White Arabic: "أهلاً وسهلاً بك في الدعم الفني. تفضل بالاسم الكامل لو سمحت؟"
  3. From this point forward, you must speak STRICTLY AND ONLY in Arabic. Do not speak English except standard IT acronyms (VPN, Outlook, Teams).
- If the caller's language selection is ambiguous or they immediately describe a problem without picking a language:
  Ask once more in both languages: "For English please say English. للغة العربية قل عربي."

1. PATIENCE, TURN-TAKING & NO OVERLAPPING (MANDATORY):
- The caller is the priority speaker. You must NEVER speak over the caller or interrupt them.
- Always wait until the caller has completely finished speaking their entire thought before generating a response.
- Do NOT jump in if the caller pauses briefly while searching for information, thinking, or checking their employee ID.
- Listen carefully to the full sentence and allow natural pauses before responding.
- BACKGROUND NOISE & VOICES: You MUST IGNORE all background voices, ambient conversation, TV/radio noise, side conversations, and any audio that is NOT the primary caller speaking directly to you. Do NOT respond to, acknowledge, or act on any background audio. Only react to the caller's direct, intentional speech addressed to you. If you are unsure whether a voice is the caller or background noise, stay silent and wait for the caller to clearly address you.
- Once the caller finishes their sentence, respond IMMEDIATELY with a natural acknowledgment — do NOT leave dead air or stay silent for more than 1-2 seconds after their turn ends.

2. STRICT SECURITY VERIFICATION GATE FOR TRANSFERS & TICKETS (MANDATORY):
- Company security policy STRICTLY PROHIBITS transferring unverified callers to human IT support, queues, or supervisors under ANY circumstances.
- NEVER call transfer_to_agent for unverified callers!
- If an unverified caller asks to speak to an agent, be transferred, or speak with a supervisor:
  - You MUST REFUSE the transfer politely.
  - In English: "I apologize, but for company security reasons, only verified National Finance employees can be transferred to our IT support team. Please provide your full name and 4-digit employee ID first so I can verify your identity."
  - In Arabic: "أعتذر منك، لدواعي الأمان المتبعة في ناشيونال فاينانس لا يمكن تحويل أي مكالمة للدعم الفني إلا بعد التحقق من الهوية الوظيفية أولاً. يرجى تزويدي بالاسم الكامل والرقم الوظيفي أولاً لنتمكن من مساعدتك."
- Callers have a strict MAXIMUM of 3 verification attempts. After 3 failed attempts, the call will be disconnected automatically.
- Only callers who have been successfully verified via verify_user (returning verified: true) can be transferred or have tickets created.

3. BILINGUAL & ARABIC EXCELLENCE PROTOCOL:
- National Finance is based in the Sultanate of Oman. The majority of employees are Arabic speaking.
- When Arabic is chosen, speak in natural, warm, and professional Gulf White Arabic / Simplified Modern Standard Arabic (لهجة خليجية بيضاء مهنية ومبسطة مقبولة في بيئة العمل العمانية).
- Use natural, respectful phrasing ("أهلاً وسهلاً بك", "حياك الله", "تفضل أخي الكريم", "أبشر، الحين أساعدك").
- Technical Fluency: In IT conversations, employees naturally use terms like "الباسوورد / كلمة المرور", "الـ VPN", "الآوتلوك", "التيمز", "ريستارت / إعادة تشغيل", "اللاب توب", "الشاحن". Understand and acknowledge these technical words naturally.
- Ask one question at a time and wait for the caller's answer.
- Do not speak in broken mixed languages. Once Arabic is confirmed, maintain clean, fluent Arabic throughout.
- If a caller starts in English and asks for Arabic (or vice versa), switch immediately using set_language.
- Be phone-friendly, calm, polite, and concise.

4. EXECUTIVE / MANAGEMENT PRIORITY (CEO, CFO, C-SUITE):
- If the caller is identified as an Executive (CEO, CFO, C-Level) via pre-verified Caller ID:
  - Greet them with utmost respect: "Welcome to National Finance IT Support. I am transferring you directly to our Senior Executive Support Desk right now."
  - Call transfer_to_agent with queue_type="executive".
  - Do not subject pre-verified executives to routine diagnostic troubleshooting.

5. EMERGENCY / SEV-1 CRITICAL INCIDENT PROTOCOL:
- If the caller reports a major emergency or system outage (e.g. core banking down, branch offline, ransomware, payment gateway failure, fire, data center alert, network outage):
- Do not perform slow troubleshooting or ask routine questions.
- You MUST speak to the caller FIRST before any transfer happens:
  - Say: "Understood. This is flagged as a critical incident. I am transferring you immediately to our on-call emergency engineering team and raising an emergency ticket."
  - In Arabic: "تم استلام البلاغ. هذا مصنّف كحادثة حرجة. أحولك فوراً لفريق الطوارئ والمهندسين المناوبين وأرفع لك تذكرة طوارئ."
- Then call escalate_emergency with reason and incident_summary. You must NEVER silently transfer without speaking to the caller first.

6. EXPERT CORPORATE IT ENGINEER PERSONA & CONVERSATIONAL FILLERS:
- You are Arif, a senior, highly skilled Tier-1 Corporate IT Support Engineer for National Finance. Think and speak like an elite Service Desk professional in a major corporate enterprise.
- Your primary mission is FIRST-CONTACT RESOLUTION: diagnosing and solving technical issues directly on the call through structured troubleshooting.
- NEVER ASK "Shall I create a ticket for you?" or offer a ticket when a caller first explains an issue! Premature ticketing is strictly prohibited.
- NATURAL CONVERSATIONAL FILLERS & REASSURANCE:
  - When the caller explains their technical problem, you MUST acknowledge IMMEDIATELY with natural conversational fillers and professional empathy. Do NOT stay silent or leave dead air after the caller finishes explaining:
    - English: "Umm, I got it. Let's troubleshoot that together right now. Let me check the diagnostic steps for your WiFi...", "Understood, let's get that sorted out for you right away. Let's try the first step...", "I see, let's take a look at that together..."
    - Arabic: "تمام، فهمت عليك تماماً. ولا تشيل هم بنحل المشكلة معك خطوة بخطوة. أولاً...", "أفهمك تماماً، خلني أشيك على خطوات حل مشكلة الواي فاي الحين...", "واضح جداً، خلنا نجرب خطوة أولى بسيطة مع بعض..."
  - This reassures the employee immediately that you understand and are taking ownership of the issue.
  - CRITICAL: You must start speaking within 1-2 seconds after the caller finishes their sentence. Silence or long pauses are unacceptable and make the caller think the line is disconnected.

7. MANDATORY 3 TO 4 STEPS TROUBLESHOOTING PROTOCOL:
- When a caller reports ANY technical problem (Wi-Fi/Network, Outlook, Teams, VPN, Printer, Slow PC, MFA, etc.):
  1. Immediately call record_issue_detail and lookup_knowledge_base to retrieve the technical playbook.
  2. Deliver ONE step at a time, clearly and calmly.
  3. You MUST guide the caller through 3 to 4 sequential diagnostic steps before ever considering raising an unresolved ticket:
     - Step 1 (Physical / Basic checks): e.g. For WiFi: Check physical WiFi switch / Airplane mode toggle, and verify connected to corporate SSID ('NF-Corporate') not guest network. Ask: "Could you check that right now and let me know what you see?" / "جرب معي هالخطوة الحين وقولي وش يطلع معك؟"
     - Step 2 (Reset / Re-authenticate): If Step 1 didn't resolve it, move to Step 2: Disconnect and reconnect to the network, or 'Forget Network' and re-enter corporate credentials, or disable/re-enable the network adapter. Ask them to test.
     - Step 3 (Diagnostic / IP Refresh): If Step 2 didn't resolve it, move to Step 3: Run command prompt to release and renew IP (`ipconfig /renew` and `ipconfig /flushdns`), or check if colleagues nearby have the same issue. Ask them to test.
     - Step 4 (Device Reboot / Advanced Isolation): If Step 3 didn't resolve it, move to Step 4: Perform a clean reboot of the laptop/PC, or test with a phone hotspot to isolate hardware versus network.
- NEVER dump all steps at once. Provide one instruction, then wait for the caller to test and reply.

8. MANDATORY RESOLUTION VERIFICATION:
- After providing each troubleshooting step, you MUST ask the caller to test it and verify the outcome:
  - In English: "Did that resolve the issue for you?"
  - In Arabic: "هل اشتغلت معك الحين؟"
- Wait for the caller's confirmation before proceeding.

9. RESOLUTION VS. ESCALATION TICKETING PROTOCOL:
- SCENARIO A — ISSUE RESOLVED:
  - If the caller confirms the issue is RESOLVED:
  - Congratulate them: "Excellent! Glad we could get that resolved for you." / "ممتاز جداً! الحمد لله إنها اشتغلت معك تمام."
  - Call record_resolution immediately. This creates a ticket marked 'Resolved' closed under the AI Agent in the IT Helpdesk.
  - Recite the reference ticket number slowly digit-by-digit.
- SCENARIO B — ISSUE UNRESOLVED AFTER 3-4 STEPS (OR CALLER DEMANDS ESCALATION):
  - If the issue is NOT resolved after trying 3 to 4 steps, or if the problem requires IT administrator rights / physical hardware replacement:
  - Say:
    - In English: "Since those steps haven't resolved the issue, I will now create an official IT support ticket for our desktop engineering team to investigate. Let me log that for you right away..."
    - In Arabic: "بما إن الخطوات السابقة ما حلت المشكلة، راح أفتح لك تذكرة رسمية لفريق الدعم الفني لمتابعة الموضوع معك فوراً..."
  - Call create_ticket compiling all symptoms, error messages, and troubleshooting steps attempted.
  - MANDATORY TICKET NUMBER REPETITION:
    - You MUST recite the ticket number slowly and clearly, and then REPEAT it once more:
    - In English: "Your ticket has been logged under reference number [TICKET NUMBER]. Let me repeat that for you: [TICKET NUMBER]. Our IT support team will follow up with you shortly."
    - In Arabic: "تم تسجيل تذكرتك برقم مرجعي [TICKET NUMBER]. أكرر لك الرقم: [TICKET NUMBER]. سيتواصل معك فريق الدعم الفني قريباً."

10. EXISTING TICKET STATUS TRACKING PROTOCOL:
- If the caller asks about an existing ticket, asks for an update, or provides a ticket number (e.g. "What is the status of ticket HD-2026-0012?" or "Has my ticket been approved?"):
- First ensure caller identity is verified.
- Call check_ticket_status with the ticket number.
- Report the ticket status clearly:
  - State the status (e.g. Open, In Progress, Pending Manager Approval, Resolved, Closed).
  - If the ticket requires Department Manager approval, explain clearly: "Your ticket is currently Pending Manager Approval by your Department Manager."
  - If the ticket is resolved, read the resolution notes.
- Recite the ticket number slowly digit-by-digit and repeat it.
- Ask if they need any further assistance with this ticket.

11. HARDWARE REQUESTS & MANAGER APPROVAL POLICY:
- If the caller reports damaged hardware, broken accessories, or requests replacement/new equipment (laptop, monitor, keyboard, mouse, dock, charger, phone, headset):
- Call create_ticket with group="Hardware Request".
- The ticket is automatically registered with status 'Pending Approval'.
- State clearly to the caller: "Your hardware request has been logged under ticket [number] with status 'Pending Manager Approval'. Per National Finance policy, your Department Manager must approve this in the IT Helpdesk before our IT team can dispatch the equipment."
- Hand over the ticket number clearly digit-by-digit and repeat it.

12. TICKET RECITAL & REPETITION RULE:
- Whenever you share any ticket reference number, recite it clearly and slowly, digit by digit (e.g. "H D 2 0 2 6 0 0 1 2"), and repeat it once for clarity.
- If the caller asks to repeat the ticket number or asks "what was my ticket number?", call repeat_ticket_number immediately and recite it slowly digit-by-digit.
- Never invent or fabricate ticket numbers.

13. TRANSFER RECOVERY & SCHEDULED CALLBACK:
- If a transfer to a human queue cannot be completed or lines are busy, DO NOT drop the call or leave dead air.
- Apologize politely, confirm their reference ticket number, and offer to schedule a callback using request_callback.
- If the caller says they cannot wait on hold or asks for a callback, call request_callback with their preferred time and contact number.

14. NON-IT INQUIRY HANDLING (LOANS, BANKING, CAR FINANCE):
- If the caller asks about non-IT topics (personal loans, auto finance, interest rates, credit cards, bank balances, or HR payroll):
- Do NOT create an IT ticket or escalate to IT queues.
- Politely explain: "This line is strictly dedicated to National Finance internal IT Support. For loan applications or banking inquiries, please reach out to our Customer Care team."

15. ZERO VENDOR LEAKAGE & CONFIDENTIALITY:
- NEVER mention the names of backend software, tools, databases, or vendors to the caller.
- Do NOT say Frappe, ERPNext, Zammad, OpenAI, Asterisk, PostgreSQL, SQLite, Python, etc.
- Always refer to the system simply as "the IT Helpdesk" or "IT Support" or "our ticketing system".

16. ANTI-HALLUCINATION & BOUNDARY INTEGRITY:
- You are an internal IT Support voice agent exclusively for National Finance employees.
- NEVER invent ticket numbers. Only recite ticket numbers returned directly by create_ticket, record_resolution, check_ticket_status, or request_callback.
- NEVER claim you directly unlocked an Active Directory account or changed a password on the server yourself. You provide the self-service steps from lookup_knowledge_base or log a service desk ticket for IT administrators.
- Ground all technical troubleshooting strictly in verified playbooks via lookup_knowledge_base.
- Never create more than one ticket per issue.

17. KEYPAD / DTMF FALLBACK PROTOCOL:
- If the caller is calling from a noisy environment, has poor audio, or if verbal verification of employee ID fails, inform the caller:
  - In Arabic: "يمكنك أيضاً إدخال رقمك الوظيفي المكون من 4 أرقام عبر لوحة المفاتيح متبوعاً بمربع (#)."
  - In English: "You can also enter your 4-digit employee ID using your telephone keypad followed by the hash key (#)."
- When the caller speaks or submits keypad digits, handle them via submit_dtmf_keypad or verify_user.

STANDARD CALL FLOW:
1. Greet caller: "Welcome to National Finance IT Support. For English please say English. للغة العربية قل عربي."
2. Caller selects language -> call set_language.
   - If English chosen: Speak 100% in professional corporate English.
   - If Arabic chosen: Speak 100% in natural Gulf White Arabic.
3. If caller is not pre-identified:
   - Ask caller full name -> call capture_name.
   - Ask employee ID -> call capture_employee_id.
   - Call verify_user.
4. If unverified caller requests transfer to agent or supervisor:
   - Refuse politely and require identity verification first.
   - Callers have maximum 3 verification attempts. After 3 failed attempts, call drops automatically.
5. If verified, ask: "How can I assist you with your IT support today?"
6. Classify caller intent:
   - If inquiry on existing ticket -> call check_ticket_status.
   - If technical issue -> Acknowledge with natural filler, call record_issue_detail, then lookup_knowledge_base, deliver Step 1, and troubleshoot through 3-4 steps. DO NOT offer a ticket upfront!
   - If hardware replacement/damage -> call create_ticket with group="Hardware Request" and explain manager approval policy.
   - If critical outage -> call escalate_emergency.
7. Outcome:
   - If resolved through troubleshooting -> call record_resolution, praise caller, and recite ticket number.
   - If unresolved after 3-4 steps -> announce ticket creation, call create_ticket, and REPEAT ticket number clearly.
8. Close cleanly with close_call.
"""

TOOLS = [
    {
        "type": "function",
        "name": "set_language",
        "description": "Set caller language for this call.",
        "parameters": {
            "type": "object",
            "properties": {"language": {"type": "string", "enum": ["en", "ar"]}},
            "required": ["language"],
        },
    },
    {
        "type": "function",
        "name": "capture_name",
        "description": "Capture caller full name before verification.",
        "parameters": {
            "type": "object",
            "properties": {"employee_name": {"type": "string"}},
            "required": ["employee_name"],
        },
    },
    {
        "type": "function",
        "name": "capture_employee_id",
        "description": "Capture caller employee ID before verification.",
        "parameters": {
            "type": "object",
            "properties": {"employee_id": {"type": "string"}},
            "required": ["employee_id"],
        },
    },
    {
        "type": "function",
        "name": "verify_user",
        "description": "Verify caller identity using full name and employee ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
                "employee_name": {"type": "string"},
            },
            "required": ["employee_id", "employee_name"],
        },
    },
    {
        "type": "function",
        "name": "lookup_knowledge_base",
        "description": "Fetch verified enterprise IT troubleshooting playbook for WiFi, network, Outlook, Teams, VPN, account lockouts, passwords, printers, or PC issues. Call this as soon as the caller reports a technical problem to get diagnostic steps.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Issue topic keyword e.g. wifi_issue, network, vpn_issue, outlook_issue, printer_issue, account_locked, password_reset, hardware"
                }
            },
            "required": ["topic"],
        },
    },
    {
        "type": "function",
        "name": "record_issue_detail",
        "description": "Record caller issue details and troubleshooting answers.",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_category": {"type": "string"},
                "field": {"type": "string"},
                "value": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["field", "value"],
        },
    },
    {
        "type": "function",
        "name": "record_resolution",
        "description": "Call this immediately when the caller confirms their technical issue is resolved and working. Creates a resolved ticket closed under the AI Agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "resolution_summary": {"type": "string"},
            },
            "required": ["title", "resolution_summary"],
        },
    },
    {
        "type": "function",
        "name": "create_ticket",
        "description": "Create an IT support ticket in the IT Helpdesk. ONLY call this after attempting 3-4 troubleshooting steps with the caller or if the caller explicitly demands escalation, or for hardware requests. NEVER offer or call this at the start of an issue.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["1 low", "2 normal", "3 high", "4 urgent"]},
                "group": {"type": "string"},
                "caller_insisted": {"type": "boolean", "description": "Set to true if caller explicitly insisted on immediate ticket logging without troubleshooting."},
            },
            "required": ["title", "description"],
        },
    },
    {
        "type": "function",
        "name": "escalate_emergency",
        "description": "Immediately escalate Sev-1 critical outages or emergency incidents to on-call emergency queue.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "incident_summary": {"type": "string"},
            },
            "required": ["reason", "incident_summary"],
        },
    },
    {
        "type": "function",
        "name": "transfer_to_agent",
        "description": "Transfer call to human IT support queue (standard, executive, or emergency). Caller MUST be verified first via verify_user. Unverified callers cannot be transferred.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "queue_type": {
                    "type": "string",
                    "enum": ["standard", "executive", "emergency"],
                    "description": "Routing target: standard (7001), executive (7002), or emergency (7003)"
                },
            },
            "required": ["reason"],
        },
    },
    {
        "type": "function",
        "name": "report_audio_issue",
        "description": "Report unclear audio, background voice, or multiple speakers detected.",
        "parameters": {
            "type": "object",
            "properties": {"issue": {"type": "string"}},
            "required": ["issue"],
        },
    },
    {
        "type": "function",
        "name": "repeat_ticket_number",
        "description": "Repeat the created or resolved reference ticket number clearly to the caller digit-by-digit.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "type": "function",
        "name": "request_callback",
        "description": "Schedule a callback from human IT support when caller does not want to wait on hold or when transfer fails.",
        "parameters": {
            "type": "object",
            "properties": {
                "preferred_time": {
                    "type": "string",
                    "description": "Preferred callback time or urgency (e.g. 'ASAP', 'within 1 hour', 'morning')",
                },
                "contact_number": {
                    "type": "string",
                    "description": "Phone number for callback if different from caller ID",
                },
                "notes": {
                    "type": "string",
                    "description": "Brief notes on why callback was requested and issue context",
                },
            },
            "required": ["preferred_time"],
        },
    },
    {
        "type": "function",
        "name": "check_ticket_status",
        "description": "Look up the live status, manager approval state, and resolution notes of an existing IT support ticket.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_number": {
                    "type": "string",
                    "description": "The ticket reference number to look up (e.g. 'HD-2026-0012' or spoken digits)",
                }
            },
            "required": ["ticket_number"],
        },
    },
    {
        "type": "function",
        "name": "submit_dtmf_keypad",
        "description": "Submit or process employee ID numbers entered on the telephone keypad by the caller.",
        "parameters": {
            "type": "object",
            "properties": {
                "digits": {
                    "type": "string",
                    "description": "The keypad digit sequence entered by caller (e.g. '1002')",
                }
            },
            "required": ["digits"],
        },
    },
    {
        "type": "function",
        "name": "close_call",
        "description": "Close call cleanly with goodbye.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": [
                        "resolved",
                        "ticket_created",
                        "verification_failed",
                        "caller_requested",
                        "audio_unclear",
                        "timeout",
                    ],
                }
            },
            "required": ["reason"],
        },
    },
]


def build_session_config():
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": SYSTEM_PROMPT,
            "output_modalities": ["audio"],
            "tools": TOOLS,
            "tool_choice": "auto",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcmu"},
                    "transcription": {
                        "model": "whisper-1",
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": VAD_THRESHOLD,
                        "prefix_padding_ms": 150,
                        "silence_duration_ms": VAD_SILENCE_MS,
                        "create_response": True,
                        "interrupt_response": True,  # Full-duplex barge-in enabled
                        "idle_timeout_ms": VAD_IDLE_TIMEOUT_MS,
                        "eagerness": "medium",  # Balanced response timing — avoids triggering on background noise while keeping latency low
                    },
                },
                "output": {
                    "format": {"type": "audio/pcmu"},
                    "voice": "alloy",
                },
            },
        },
    }


async def send_response(openai_ws, instructions):
    await openai_ws.send(
        json.dumps(
            {
                "type": "response.create",
                "response": {
                    "output_modalities": ["audio"],
                    "instructions": instructions,
                },
            }
        )
    )


async def connect_openai():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is empty or missing in .env")

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}

    ws = await websockets.connect(
        OPENAI_WS_URL,
        additional_headers=headers,
        max_size=None,
    )

    await ws.send(json.dumps(build_session_config()))

    await send_response(
        ws,
        (
            "Say exactly this and nothing else: "
            "Welcome to National Finance IT Support. For English please say English. للغة العربية قل عربي."
        ),
    )

    return ws


def digit_by_digit(value):
    return " ".join(list(str(value)))


def language_prefix(state):
    if state.get("language") == "ar":
        return "Respond only in Arabic."
    return "Respond only in English."


class AudioSocketChannel:
    """
    Adapter that wraps Asterisk AudioSocket TCP connection to match WebSocket API.
    Converts Asterisk 16-bit 8kHz linear PCM (ast_format_slin) <-> OpenAI Realtime audio/pcmu (G.711 u-law).
    Handles Asterisk AudioSocket frame headers (0x01 UUID, 0x10 Audio, 0x03 DTMF, 0x00 Hangup).
    Implements real-time 20ms pacing to Asterisk to eliminate jitter, stuttering, and dropped packets.
    """

    def __init__(self, reader, writer, call_uuid):
        self.reader = reader
        self.writer = writer
        self.call_uuid = call_uuid
        self._closed = False
        self.outbound_queue = asyncio.Queue(maxsize=1500)
        self.playback_task = asyncio.create_task(self._playback_loop())

    async def _playback_loop(self):
        """Paces audio frames to Asterisk at exactly 20ms per 320-byte SLIN frame."""
        interval = 0.020
        while not self._closed:
            try:
                frame = await self.outbound_queue.get()
                if self._closed or self.writer.is_closing():
                    break
                slin = audioop.ulaw2lin(frame, 2)
                packet = struct.pack("!BH", 0x10, len(slin)) + slin
                self.writer.write(packet)
                await self.writer.drain()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    def clear_outbound_queue(self):
        """Instantly flush all pending outbound audio frames on barge-in / speech interruption."""
        while not self.outbound_queue.empty():
            try:
                self.outbound_queue.get_nowait()
            except Exception:
                break

    async def __aiter__(self):
        # Look up Asterisk incoming channel name & caller ID via AMI
        caller_info = await asyncio.to_thread(get_active_channel_info, self.call_uuid)
        chan_arg = f" channel:{caller_info['channel']}" if caller_info and caller_info.get("channel") else ""
        caller_arg = f" caller:{caller_info['caller_num']}" if caller_info and caller_info.get("caller_num") else ""
        yield f"MEDIA_START channel_id:{self.call_uuid}{chan_arg}{caller_arg}"

        while not self._closed:
            try:
                hdr = await self.reader.readexactly(3)
            except (asyncio.IncompleteReadError, ConnectionResetError, Exception):
                break
            kind, length = struct.unpack("!BH", hdr)
            payload = await self.reader.readexactly(length) if length > 0 else b""

            if kind == 0x10 and len(payload) > 0:  # Audio (16-bit 8kHz slin PCM)
                try:
                    ulaw = audioop.lin2ulaw(payload, 2)
                    if len(ulaw) > 0:
                        yield ulaw
                except Exception:
                    pass
            elif kind == 0x03:  # DTMF digit
                digit = payload.decode("ascii", errors="ignore")
                yield f"DTMF:{digit}"
            elif kind == 0x00:  # Hangup
                break

    async def send(self, data):
        """Enqueue u-law audio data chunked into 160-byte (20ms) frames for smooth paced playback."""
        if self._closed or self.writer.is_closing():
            return
        if isinstance(data, bytes) and len(data) > 0:
            frame_size = 160
            for i in range(0, len(data), frame_size):
                chunk = data[i:i + frame_size]
                if len(chunk) < frame_size:
                    chunk = chunk + b"\xff" * (frame_size - len(chunk))
                try:
                    self.outbound_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    try:
                        self.outbound_queue.get_nowait()
                        self.outbound_queue.put_nowait(chunk)
                    except Exception:
                        pass

    async def close(self):
        if not self._closed:
            self._closed = True
            if self.playback_task and not self.playback_task.done():
                self.playback_task.cancel()
            self.clear_outbound_queue()
            try:
                self.writer.write(struct.pack("!BH", 0x00, 0))
                await self.writer.drain()
            except Exception:
                pass
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass


async def handle_audiosocket_connection(reader, writer):
    """Entry point for native Asterisk AudioSocket TCP calls."""
    try:
        hdr = await reader.readexactly(3)
        kind, length = struct.unpack("!BH", hdr)
        if kind == 0x01 and length == 16:
            uuid_bytes = await reader.readexactly(16)
            call_uuid = str(uuid.UUID(bytes=uuid_bytes))
        else:
            call_uuid = str(int(time.time() * 1000))
    except Exception as exc:
        print(f"[AUDIOSOCKET] Handshake error: {exc}")
        writer.close()
        return

    channel = AudioSocketChannel(reader, writer, call_uuid)
    await handle_asterisk_call(channel)


async def handle_asterisk_call(asterisk_ws):
    global ACTIVE_CALLS

    async with ACTIVE_CALLS_LOCK:
        if ACTIVE_CALLS >= MAX_CONCURRENT_CALLS:
            print("[CALL] Max concurrent calls reached. Rejecting call.")
            await asterisk_ws.close()
            return

        ACTIVE_CALLS += 1
        print(f"[CALL] Accepted. Active calls: {ACTIVE_CALLS}")

    try:
        await handle_single_call(asterisk_ws)
    finally:
        async with ACTIVE_CALLS_LOCK:
            ACTIVE_CALLS -= 1
            print(f"[CALL] Handler exiting. Active calls: {ACTIVE_CALLS}")


async def handle_single_call(asterisk_ws):
    state = {
        "call_id": str(int(time.time() * 1000)),
        "current_state": "greeting",
        "language": None,
        "greeted": True,
        "caller_name": None,
        "employee_id": None,
        "verified_user": None,
        "verification_attempts": 0,
        "is_vip": False,
        "is_executive": False,
        "tier": "STANDARD",
        "issue_category": None,
        "issue_summary": None,
        "last_question": None,
        "awaiting_answer": False,
        "question_count": 0,
        "answers_received": [],
        "troubleshooting_steps": [],
        "simple_issue_detected": False,
        "ticket_confirmed": True,
        "ticket_creation_attempted": False,
        "last_ticket_number": None,
        "ticket_created": False,
        "active_response": False,
        "pending_response_instruction": None,
        "pending_goodbye_instruction": None,
        "close_after_next_response_done": False,
        "tool_in_progress": False,
        "call_ending": False,
        "closing": False,
        "background_noise_warning_count": 0,
        "asterisk_channel": None,
        "caller_number": None,
        "transfer_attempted": False,
        "transfer_target": ASTERISK_QUEUE_STANDARD,
        "started_monotonic": time.monotonic(),
        "dtmf_buffer": "",
        "dtmf_last_time": 0.0,
        "transcript_lines": [],
        "call_logged_closed": False,
        "pending_transfer_after_announcement": False,
    }

    create_call(state["call_id"])
    print("[ASTERISK] New call connected")

    def generate_call_summary(status="completed"):
        caller = state.get("caller_name") or "Unverified Caller"
        emp = state.get("employee_id") or "N/A"
        cat = state.get("issue_category") or "General IT"
        issue = state.get("issue_summary") or "Technical inquiry"
        steps = "; ".join(state.get("troubleshooting_steps", []))
        ticket = state.get("last_ticket_number")

        summary_parts = [f"Caller: {caller} (Emp ID: {emp})", f"Category: {cat}", f"Issue: {issue}"]
        if steps:
            summary_parts.append(f"Steps: {steps}")
        if ticket:
            summary_parts.append(f"Ticket: {ticket}")
        if state.get("transferred"):
            summary_parts.append(f"Transferred: Queue {state.get('transfer_target', '7001')}")
        return " | ".join(summary_parts)

    def ensure_ticket_logged(close_status="completed"):
        """Guarantee every call has a ticket created in Frappe Helpdesk:
        - If resolved: status='Resolved', marked as deflected, closed under AI agent Arif.
        - If not resolved: status='Open', unassigned to team 'IT Support'.
        """
        if state.get("ticket_created") or state.get("last_ticket_number"):
            return state.get("last_ticket_number")

        try:
            verified_user = state.get("verified_user") or {}
            caller_name = verified_user.get("name") or state.get("caller_name") or f"Caller ({state.get('caller_phone', 'Direct')})"
            caller_phone = verified_user.get("phone") or state.get("caller_phone", "")
            employee_id = verified_user.get("employee_id") or state.get("employee_id", "")
            department = verified_user.get("department", "General")
            caller_email = verified_user.get("email") or f"caller_{state['call_id'][:8]}@nationalfinance.com"

            is_resolved = (
                state.get("resolution_recorded")
                or state.get("current_state") == "resolved"
                or close_status == "resolved"
            )

            ticket_status = "Resolved" if is_resolved else "Open"
            issue_cat = state.get("issue_category") or "IT Support"
            ticket_category = "Resolved on Call" if is_resolved else issue_cat

            summary_text = state.get("issue_summary") or state.get("summary") or "Internal IT Support Inquiry"
            ticket_title = f"{issue_cat}: {summary_text}" if summary_text else f"IT Call - {state['call_id'][:8]}"
            if len(ticket_title) > 80:
                ticket_title = ticket_title[:77] + "..."

            key_points = "\n".join([f"- {item['field']}: {item['value']}" for item in state.get("answers_received", [])[-10:]])
            troubleshooting = "\n".join([f"- {step}" for step in state.get("troubleshooting_steps", [])[-10:]])
            recent_transcript = "\n".join(state.get("transcript_lines", [])[-12:])

            resolution_note = (
                "Issue resolved directly on call via AI Service Desk Agent Arif (First-Contact Resolution)."
                if is_resolved else
                "Call concluded without complete resolution on the voice channel. Status: Open (Unassigned) for IT Support Desk follow-up."
            )

            ticket_body = (
                f"Caller: {caller_name}\n"
                f"Phone: {caller_phone if caller_phone else 'N/A'}\n"
                f"Employee ID: {employee_id if employee_id else 'Unverified'}\n"
                f"Department: {department}\n"
                f"Call ID: {state['call_id']}\n"
                f"Call Outcome: {close_status}\n\n"
                f"Issue Summary:\n{summary_text}\n\n"
                f"Key Details:\n{key_points if key_points else '- None'}\n\n"
                f"Troubleshooting Steps Attempted:\n{troubleshooting if troubleshooting else '- None recorded on call'}\n\n"
                f"Transcript Snippet:\n{recent_transcript if recent_transcript else '- No transcript available'}\n\n"
                f"Agent: AI Voice Agent Arif\n"
                f"Resolution / Action:\n{resolution_note}"
            )

            client = get_ticketing_client()
            res = client.create_ticket(
                customer_email=caller_email,
                title=ticket_title,
                body=ticket_body,
                priority="normal",
                category=ticket_category,
                caller_info={
                    "name": caller_name,
                    "phone": caller_phone,
                    "employee_id": employee_id,
                    "department": department,
                },
                custom_fields={
                    "call_id": state["call_id"],
                },
                status=ticket_status,
            )
            ticket_number = res.get("ticket_number")
            if ticket_number:
                state["last_ticket_number"] = ticket_number
                state["ticket_created"] = True
                update_call(
                    state["call_id"],
                    ticket_number=ticket_number,
                    ticket_created=1,
                    ai_deflected=1 if is_resolved else 0,
                    resolution_type="AI_Resolved" if is_resolved else "Escalated_Open",
                    status="resolved" if is_resolved else "ticket_created",
                )
                print(f"[AUTO-TICKET] Created {ticket_status} ticket {ticket_number} for call {state['call_id']}")
                return ticket_number
        except Exception as exc:
            print(f"[AUTO-TICKET ERROR] Could not ensure ticket logged: {exc!r}")
        return None

    def wrap_up_call(status="completed"):
        if state.get("call_logged_closed"):
            return
        state["call_logged_closed"] = True
        ensure_ticket_logged(status)
        full_transcript = "\n".join(state["transcript_lines"]) if state["transcript_lines"] else None
        auto_summary = generate_call_summary(status)
        log_close_call(state["call_id"], status=status, summary=auto_summary, transcript=full_transcript)
        print(f"[CALL CLOSED] ID: {state['call_id']}, Status: {status}, Transcript lines: {len(state['transcript_lines'])}")

    async def handle_dtmf_submission(code: str):
        print(f"[DTMF] Processing submitted employee ID: {code}")
        state["employee_id"] = code
        spoken = digit_by_digit(code)
        res = await execute_tool("verify_user", {"employee_id": code})

        if res.get("verified"):
            v_name = res.get("caller_name") or ""
            if state["language"] == "ar":
                queue_response(
                    f"Respond only in Arabic. Say: شكراً لك. تم تأكيد هويتك بنجاح عبر الرقم الوظيفي {spoken} للموظف {v_name}. كيف يمكنني مساعدتك اليوم؟"
                )
            else:
                queue_response(
                    f"Respond only in English. Say: Thank you. Your identity has been verified successfully with employee ID {spoken} for {v_name}. How can I assist you today?"
                )
        else:
            attempts_left = res.get("attempts_left", 0)
            if state["language"] == "ar":
                queue_response(
                    f"Respond only in Arabic. Say: عذراً، الرقم الوظيفي {spoken} غير مطابق في سجلاتنا. تبقى لديك {attempts_left} محاولات."
                )
            else:
                queue_response(
                    f"Respond only in English. Say: I'm sorry, employee ID {spoken} was not found in our records. You have {attempts_left} attempts remaining."
                )
        await send_queued_response_if_any()

    async def process_dtmf_digit(digit: str):
        digit = digit.strip()
        if not digit:
            return

        print(f"[DTMF] Received keypress: '{digit}' (Current state: {state.get('current_state')})")

        # Initial language selection via telephone keypad: 1 for English, 2 for Arabic
        if state["language"] is None:
            if digit == "1":
                await execute_tool("set_language", {"language": "en"})
                queue_response("Respond only in English. Say: Thank you for choosing English. May I please have your full name?")
                await send_queued_response_if_any()
                return
            elif digit == "2":
                await execute_tool("set_language", {"language": "ar"})
                queue_response("Respond only in Arabic. Say: أهلاً وسهلاً بك في الدعم الفني. تفضل بالاسم الكامل لو سمحت؟")
                await send_queued_response_if_any()
                return

        if digit == "*":
            state["dtmf_buffer"] = ""
            return

        if digit == "#":
            code = state["dtmf_buffer"]
            state["dtmf_buffer"] = ""
            if code:
                await handle_dtmf_submission(code)
            return

        state["dtmf_buffer"] += digit
        state["dtmf_last_time"] = time.monotonic()

        # If exactly 4 digits entered during verification or inquiry, auto-submit
        if len(state["dtmf_buffer"]) == 4 and state.get("current_state") in ("greeting", "ask_name", "ask_employee_id", "verification"):
            code = state["dtmf_buffer"]
            state["dtmf_buffer"] = ""
            await handle_dtmf_submission(code)

    try:
        openai_ws = await connect_openai()
        print("[OPENAI] Connected")
    except Exception as exc:
        print(f"[OPENAI] Connection failed: {exc!r}")
        update_call(state["call_id"], status="openai_connection_failed", summary=str(exc))
        await asterisk_ws.close()
        return

    def queue_response(instruction):
        state["pending_response_instruction"] = instruction

    def queue_goodbye(reason):
        if state["closing"]:
            return

        state["closing"] = True
        state["current_state"] = "closing"

        if reason == "verification_failed":
            if state.get("language") == "ar":
                state["pending_goodbye_instruction"] = (
                    "Respond only in Arabic. Say exactly: "
                    "عذراً، تعذر التحقق من هويتك الوظيفية بعد 3 محاولات. لدواعي الأمان المتبعة، سيتم إنهاء هذه المكالمة. يرجى مراجعة مديرك المباشر أو إدارة تقنية المعلومات، أو المحاولة لاحقاً. مع السلامة."
                )
            else:
                state["pending_goodbye_instruction"] = (
                    "Respond only in English. Say exactly: "
                    "I apologize, but we are unable to verify your employee identity after 3 attempts. For security reasons, this call will now be disconnected. Please contact your department manager or IT administrator directly, or try again later. Goodbye."
                )
        elif reason == "account_deactivated":
            if state.get("language") == "ar":
                state["pending_goodbye_instruction"] = (
                    "Respond only in Arabic. Say exactly: "
                    "هذا الحساب الوظيفي غير مفعّل في النظام. لدواعي الأمان، سيتم إنهاء المكالمة. يرجى مراجعة إدارة الموارد البشرية أو مسؤول تقنية المعلومات. مع السلامة."
                )
            else:
                state["pending_goodbye_instruction"] = (
                    "Respond only in English. Say exactly: "
                    "This employee account is deactivated or offboarded. For security reasons, this call will now be disconnected. Please contact HR or your IT administrator. Goodbye."
                )
        elif reason == "audio_unclear":
            if state.get("language") == "ar":
                state["pending_goodbye_instruction"] = (
                    "Respond only in Arabic. Say exactly: "
                    "عذراً، جودة الصوت غير واضحة تماماً. يرجى معاودة الاتصال من مكان هادئ. مع السلامة."
                )
            else:
                state["pending_goodbye_instruction"] = (
                    "Respond only in English. Say exactly: "
                    "I am having difficulty hearing you due to line noise. Please call us back from a quiet area. Goodbye."
                )
        else:
            if state.get("language") == "ar":
                state["pending_goodbye_instruction"] = (
                    "Respond only in Arabic. Say exactly: "
                    "شكراً لاتصالك بدعم تقنية المعلومات في ناشيونال فاينانس. مع السلامة."
                )
            else:
                state["pending_goodbye_instruction"] = (
                    "Respond only in English. Say exactly: "
                    "Thank you for calling National Finance IT Support. Goodbye."
                )

        update_call(state["call_id"], status=reason)
        print(f"[CALL] Goodbye queued. Reason: {reason}")

    async def send_queued_response_if_any(default_fallback=False):
        if state["call_ending"]:
            return

        if state["active_response"]:
            return

        if state["pending_goodbye_instruction"]:
            instruction = state["pending_goodbye_instruction"]
            state["pending_goodbye_instruction"] = None
            state["close_after_next_response_done"] = True
            state["active_response"] = True
            await send_response(openai_ws, instruction)
            return

        if state["pending_response_instruction"]:
            instruction = state["pending_response_instruction"]
            state["pending_response_instruction"] = None
            state["active_response"] = True
            await send_response(openai_ws, instruction)
            return

        if default_fallback:
            state["active_response"] = True
            await openai_ws.send(json.dumps({"type": "response.create"}))

    async def execute_tool(tool_name, arguments):
        state["tool_in_progress"] = True

        try:
            if tool_name == "set_language":
                language = arguments.get("language")
                if language not in ("en", "ar"):
                    language = "en"

                state["language"] = language
                state["current_state"] = "ask_name"
                update_call(state["call_id"], language=language, status="language_selected")
                print(f"[LANGUAGE] Selected: {language}")

                # If caller was pre-identified by Caller ID, fast-track!
                if state.get("is_executive") and state.get("verified_user"):
                    exec_name = state["verified_user"]["name"]
                    return {
                        "success": True,
                        "language": language,
                        "is_executive": True,
                        "caller_name": exec_name,
                        "action": "executive_fast_track",
                        "message": f"Caller is executive {exec_name}. Direct transfer to executive queue.",
                    }

                return {"success": True, "language": language, "next_state": state["current_state"]}

            if tool_name == "capture_name":
                employee_name = arguments.get("employee_name", "").strip()
                if not employee_name:
                    return {"success": False, "error": "Name was empty. Ask caller to repeat full name."}

                state["caller_name"] = employee_name
                state["current_state"] = "ask_employee_id"
                return {"success": True, "employee_name": employee_name, "next_state": state["current_state"]}

            if tool_name == "capture_employee_id":
                employee_id = arguments.get("employee_id", "").strip()
                if not employee_id:
                    return {"success": False, "error": "Employee ID was empty. Ask caller to repeat employee ID."}

                state["employee_id"] = employee_id
                state["current_state"] = "verification"
                return {"success": True, "employee_id": employee_id, "next_state": state["current_state"]}

            if tool_name == "verify_user":
                employee_id = arguments.get("employee_id", "").strip() or state.get("employee_id")
                employee_name = arguments.get("employee_name", "").strip() or state.get("caller_name")

                if not employee_name or not employee_id:
                    return {
                        "verified": False,
                        "error": "Both employee name and employee ID are required before verification.",
                    }

                state["caller_name"] = employee_name
                state["employee_id"] = employee_id

                verify_key = f"verify:{state.get('caller_number') or employee_id}"
                lock_remaining = await asyncio.to_thread(is_locked, verify_key)
                if lock_remaining > 0:
                    log_security_event("verification_blocked", verify_key, f"retry_after={lock_remaining}")
                    state["current_state"] = "closing"
                    update_call(state["call_id"], status="verification_blocked")
                    queue_goodbye("verification_failed")
                    return {
                        "verified": False,
                        "action": "call_will_end",
                        "message": "Too many failed verification attempts. Please try again later.",
                    }

                result = await asyncio.to_thread(verify_user, employee_id, employee_name)

                if result.get("verified"):
                    await asyncio.to_thread(reset_rate_limit, verify_key)
                    state["verified_user"] = result
                    state["verification_attempts"] = 0
                    state["is_vip"] = bool(result.get("vip", False))
                    state["is_executive"] = bool(result.get("is_executive", False))
                    state["tier"] = result.get("tier", "STANDARD")
                    state["current_state"] = "verified"

                    update_call(
                        state["call_id"],
                        employee_id=result.get("employee_id"),
                        verified_name=result.get("name"),
                        status="verified",
                        is_vip=1 if state["is_vip"] else 0,
                        tier=state["tier"],
                    )

                    print(f"[VERIFY] Verified: {result.get('name')} ({employee_id}) Tier={state['tier']}")

                    return {
                        "verified": True,
                        "name": result.get("name"),
                        "email": result.get("email"),
                        "employee_id": result.get("employee_id"),
                        "department": result.get("department"),
                        "vip": state["is_vip"],
                        "role": result.get("role"),
                        "tier": state["tier"],
                        "is_executive": state["is_executive"],
                        "next_state": state["current_state"],
                    }

                if result.get("reason") == "account_deactivated":
                    log_security_event("verification_deactivated_account", verify_key, f"employee_id={employee_id}")
                    state["current_state"] = "closing"
                    update_call(state["call_id"], status="verification_failed")
                    queue_goodbye("account_deactivated")
                    return {
                        "verified": False,
                        "reason": "account_deactivated",
                        "attempts_left": 0,
                        "action": "call_will_end",
                        "message": "This employee account is deactivated or offboarded. Call will now end.",
                    }

                state["verification_attempts"] += 1
                attempts_left = max(0, 3 - state["verification_attempts"])

                await asyncio.to_thread(
                    check_rate_limit, verify_key, VERIFY_FAIL_LIMIT, VERIFY_FAIL_WINDOW, VERIFY_FAIL_LOCK
                )
                log_security_event("verification_failed", verify_key, f"employee_id={employee_id}")

                if state["verification_attempts"] >= 3:
                    state["current_state"] = "closing"
                    update_call(state["call_id"], status="verification_failed")
                    queue_goodbye("verification_failed")
                    return {
                        "verified": False,
                        "attempts_left": 0,
                        "action": "call_will_end",
                        "message": "Maximum verification attempts (3) reached. Security policy requires disconnecting the call.",
                    }

                state["current_state"] = "ask_name"
                return {
                    "verified": False,
                    "attempts_left": attempts_left,
                    "message": f"Name and employee ID did not match. {attempts_left} attempt(s) remaining.",
                    "next_state": state["current_state"],
                }

            if tool_name == "submit_dtmf_keypad":
                digits = arguments.get("digits", "").strip()
                if not digits:
                    return {"success": False, "error": "No keypad digits provided."}
                digits = re.sub(r"[^\d]", "", digits)
                state["employee_id"] = digits

                verify_key = f"verify:{state.get('caller_number') or digits}"
                v_res = await asyncio.to_thread(verify_user, digits, state.get("caller_name"))
                if v_res.get("verified"):
                    user = v_res["user"]
                    state["verified_user"] = user
                    state["caller_name"] = user["name"]
                    state["is_vip"] = bool(user.get("vip"))
                    state["is_executive"] = bool(user.get("is_executive"))
                    state["tier"] = user.get("tier", "STANDARD")
                    state["current_state"] = "verified"
                    update_call(
                        state["call_id"],
                        employee_id=digits,
                        verified_name=user["name"],
                        is_vip=1 if state["is_vip"] else 0,
                        tier=state["tier"],
                        status="verified",
                    )
                    return {
                        "verified": True,
                        "method": "dtmf_keypad",
                        "employee_id": digits,
                        "caller_name": user["name"],
                        "role": user.get("role"),
                        "tier": state["tier"],
                        "is_executive": state["is_executive"],
                        "message": f"Successfully verified employee ID {digits} for {user['name']} via keypad.",
                    }

                return {
                    "verified": False,
                    "method": "dtmf_keypad",
                    "employee_id": digits,
                    "message": f"Employee ID {digits} not found or deactivated.",
                }

            if tool_name == "lookup_knowledge_base":
                topic = arguments.get("topic", "").strip()
                search_res = search_knowledge_base(topic)
                if search_res.get("found"):
                    print(f"[KB MATCH] Query='{topic}' -> Playbook='{search_res.get('playbook_id')}' (Score: {search_res.get('score')})")
                    search_res["instruction"] = (
                        "Deliver Step 1 to the caller now. Acknowledge with a natural professional filler (e.g., 'Umm, I understand. Let's troubleshoot that together right now.'). "
                        "Ask the caller to test Step 1 and wait for their response. Do NOT create a ticket yet! Guide through 3 to 4 sequential diagnostic steps."
                    )
                    return search_res
                return {
                    "found": False,
                    "message": "No specific local playbook found. Use standard corporate IT troubleshooting steps.",
                    "instruction": (
                        "Acknowledge naturally with a professional filler (e.g. 'Umm, I got it. Let's troubleshoot that together right away.'). "
                        "Guide the caller through standard Step 1 diagnostics for this issue (e.g., check connections, restart application, or toggle network). "
                        "Ask the caller to test it and wait for their response. Do NOT offer to open a ticket yet! Troubleshoot through 3 to 4 sequential steps first."
                    )
                }

            if tool_name == "record_issue_detail":
                issue_category = arguments.get("issue_category", "").strip().lower()
                field = arguments.get("field", "").strip()
                value = arguments.get("value", "").strip()
                summary = arguments.get("summary", "").strip()

                if issue_category:
                    state["issue_category"] = issue_category
                if summary:
                    state["issue_summary"] = summary
                elif not state["issue_summary"] and value:
                    state["issue_summary"] = value

                if field and value:
                    state["answers_received"].append({"field": field, "value": value})
                    state["troubleshooting_steps"].append(f"{field}: {value}")

                state["question_count"] += 1
                state["current_state"] = "troubleshooting"

                update_call(
                    state["call_id"],
                    status="troubleshooting",
                    summary=state.get("issue_summary") or value,
                )

                return {
                    "success": True,
                    "question_count": state["question_count"],
                    "next_state": state["current_state"],
                }

            if tool_name == "record_resolution":
                verified_user = state.get("verified_user") or {}
                if not verified_user.get("employee_id"):
                    return {
                        "success": False,
                        "error": "Caller must be verified with full name and employee ID before recording a resolution.",
                    }

                if state.get("resolution_recorded") or state.get("ticket_created"):
                    existing_ticket = state.get("last_ticket_number") or "recorded"
                    return {
                        "success": True,
                        "ticket_number": existing_ticket,
                        "message": f"Resolution already logged for this call (Ticket: {existing_ticket}).",
                    }

                title = arguments.get("title", "IT Issue Resolved on Call")
                resolution_summary = arguments.get("resolution_summary", "Issue resolved via AI diagnostics.")

                customer_email = verified_user.get("email") or f"caller_{state['call_id']}@nationalfinance.com"

                client = get_ticketing_client()
                ticket_body = (
                    f"Caller: {verified_user.get('name', 'Direct Caller')}\n"
                    f"Employee ID: {verified_user.get('employee_id', 'N/A')}\n"
                    f"Department: {verified_user.get('department', 'N/A')}\n\n"
                    f"Resolution Summary:\n{resolution_summary}\n\n"
                    f"Status: Resolved on Call by AI Agent Arif (First-Contact Resolution)"
                )

                result = await asyncio.to_thread(
                    client.create_ticket,
                    customer_email=customer_email,
                    title=title,
                    body=ticket_body,
                    priority="low",
                    category="Resolved on Call",
                    caller_info=verified_user,
                    custom_fields={"call_id": state["call_id"]},
                    status="Resolved",
                )

                ticket_number = result.get("ticket_number")
                state["resolution_recorded"] = True
                state["last_ticket_number"] = ticket_number

                update_call(
                    state["call_id"],
                    status="resolved",
                    ai_deflected=1,
                    resolution_type="AI_Resolved",
                    ticket_number=ticket_number,
                    summary=resolution_summary,
                )
                print(f"[TICKET RESOLVED] Auto-ticket {ticket_number} created with status Resolved")

                return {
                    "success": True,
                    "ticket_number": ticket_number,
                    "ticket_number_spoken": digit_by_digit(ticket_number),
                    "message": (
                        f"Recorded resolution and closed ticket {ticket_number} successfully under AI Agent Arif. "
                        f"You MUST recite this ticket number slowly and clearly, and repeat it to the caller: "
                        f"'Your issue has been resolved and logged under reference ticket {digit_by_digit(ticket_number)}. Let me repeat that: {digit_by_digit(ticket_number)}.'"
                    )
                }

            if tool_name == "create_ticket":
                verified_user = state.get("verified_user") or {}

                # 1. Verification Gate: Unverified callers cannot create tickets
                if not verified_user.get("employee_id"):
                    return {
                        "success": False,
                        "unverified": True,
                        "error": "Caller identity must be verified before creating an IT ticket. Ask for the caller's full name and employee ID first.",
                    }

                # 2. Duplicate Prevention Gate: Only 1 ticket per call session
                if state.get("ticket_created") and state.get("last_ticket_number"):
                    return {
                        "success": False,
                        "duplicate_prevented": True,
                        "ticket_number": state["last_ticket_number"],
                        "ticket_number_spoken": digit_by_digit(state["last_ticket_number"]),
                        "message": f"A ticket ({state['last_ticket_number']}) was already created during this call session. Do not create duplicate tickets.",
                    }

                title = arguments.get("title", "IT Support Request").strip()
                description = arguments.get("description", state.get("issue_summary") or "").strip()
                priority = arguments.get("priority", "2 normal")
                group = arguments.get("group", "Service Desk")

                combined_text = f"{title} {description}".lower()

                # 3. Non-IT Scope Gate: Block customer loans and personal finance
                non_it_terms = {
                    "loan", "personal loan", "car loan", "auto loan", "vehicle finance",
                    "interest rate", "credit card", "debit card", "account balance",
                    "bank statement", "branch location", "salary advance", "payroll",
                    "قرض", "تمويل", "سلفة", "كشف حساب", "بطاقة ائتمان"
                }
                if any(term in combined_text for term in non_it_terms):
                    return {
                        "success": False,
                        "out_of_scope": True,
                        "error": "This inquiry relates to customer loans or banking rather than IT Support. Do not create an IT ticket. Politely inform the caller that this line is for internal IT Support only.",
                    }

                # 4. Description Quality Gate: Require substantive detail
                if len(description) < 10 or description.lower() in {"hi", "hello", "test", "issue", "problem", "help", "broken", "it issue", "none", "n/a"}:
                    return {
                        "success": False,
                        "error": "Issue description is too brief or vague. Please ask the caller to describe the specific technical issue or error message before raising a ticket.",
                    }

                # 5. Hardware Detection & Manager Approval Flag
                hardware_keywords = {
                    "laptop", "desktop", "computer", "pc", "monitor", "screen", "keyboard",
                    "mouse", "headset", "headphone", "dock", "docking", "charger", "adapter",
                    "printer", "toner", "scanner", "phone", "hardware", "device", "cables",
                    "replacement", "new laptop", "لوحة مفاتيح", "فأرة", "شاشة", "كمبيوتر", "شاحن", "طابعة"
                }
                is_hardware = (
                    group == "Hardware Request"
                    or any(kw in combined_text for kw in hardware_keywords)
                )
                if is_hardware:
                    group = "Hardware Request"

                # 6. Premature Ticketing Gate: Corporate policy requires 3-4 steps troubleshooting first
                caller_insisted = arguments.get("caller_insisted", False) or arguments.get("escalation_requested", False)
                troubleshooting_count = len(state.get("troubleshooting_steps", [])) + state.get("question_count", 0)

                if not is_hardware and not caller_insisted and troubleshooting_count < 2:
                    return {
                        "success": False,
                        "premature_ticket": True,
                        "error": (
                            "Corporate IT Service Desk policy requires troubleshooting 3 to 4 diagnostic steps with the caller first before creating an unresolved ticket. "
                            "Please provide Step 1 or the next diagnostic step to the caller, ask them to test it, and verify the outcome."
                        )
                    }

                customer_email = verified_user.get("email") or f"caller_{state['call_id']}@nationalfinance.com"

                if state.get("is_vip") or state.get("tier") in ("P0_EXECUTIVE", "P1_VIP"):
                    priority = "3 high"

                client = get_ticketing_client()

                key_points = "\n".join([f"- {item['field']}: {item['value']}" for item in state["answers_received"][-10:]])
                troubleshooting = "\n".join([f"- {step}" for step in state["troubleshooting_steps"][-10:]])

                ticket_body = (
                    f"Caller: {verified_user.get('name', 'Caller')}\n"
                    f"Employee ID: {verified_user.get('employee_id', 'N/A')}\n"
                    f"Department: {verified_user.get('department', 'N/A')}\n"
                    f"Tier: {state.get('tier', 'STANDARD')}\n\n"
                    f"Issue Summary:\n{description}\n\n"
                    f"Key Points Collected:\n{key_points if key_points else '- None'}\n\n"
                    f"Troubleshooting / Questions:\n{troubleshooting if troubleshooting else '- None'}\n\n"
                    f"Created by: AI Voice Agent Arif"
                )

                try:
                    result = await asyncio.to_thread(
                        client.create_ticket,
                        customer_email=customer_email,
                        title=title,
                        body=ticket_body,
                        priority=priority,
                        category=group,
                        caller_info=verified_user,
                        custom_fields={
                            "call_id": state["call_id"],
                            "requires_approval": is_hardware,
                        },
                        status="Open",
                    )

                    ticket_number = result.get("ticket_number")
                    if ticket_number:
                        state["last_ticket_number"] = ticket_number
                        state["ticket_created"] = True
                        state["current_state"] = "wrap_up"

                        ticket_status = result.get("status", "Open")
                        update_call(
                            state["call_id"],
                            ticket_number=ticket_number,
                            ticket_created=1,
                            status="ticket_created",
                            summary=description,
                        )

                        if result.get("requires_approval"):
                            return {
                                "success": True,
                                "ticket_number": ticket_number,
                                "ticket_number_spoken": digit_by_digit(ticket_number),
                                "status": ticket_status,
                                "requires_approval": True,
                                "message": (
                                    f"Hardware ticket {ticket_number} created with status 'Pending Manager Approval'. "
                                    f"Explain clearly to the caller: 'I have submitted your hardware request under reference {ticket_number}. "
                                    f"Per company policy, your Department Manager will need to approve this in the IT Helpdesk before IT can dispatch the equipment.'"
                                )
                            }

                        return {
                            "success": True,
                            "ticket_number": ticket_number,
                            "ticket_number_spoken": digit_by_digit(ticket_number),
                            "status": ticket_status,
                            "message": (
                                f"Ticket created successfully with reference number {ticket_number}. "
                                f"You MUST recite this ticket number slowly and clearly, and repeat it once more to the caller: "
                                f"'Your ticket has been logged under reference number {digit_by_digit(ticket_number)}. Let me repeat that: {digit_by_digit(ticket_number)}.'"
                            ),
                        }

                    return {"success": False, "error": "Ticketing backend did not return ticket number."}

                except Exception as exc:
                    print(f"[TICKETING ERROR] {exc!r}")
                    update_call(state["call_id"], status="ticket_failed", summary=str(exc))
                    return {"success": False, "error": str(exc)}

            if tool_name == "escalate_emergency":
                reason = arguments.get("reason", "Sev-1 Incident")
                incident_summary = arguments.get("incident_summary", "Critical outage reported.")

                print(f"[EMERGENCY ESCALATION] Reason: {reason}")
                update_call(
                    state["call_id"],
                    status="emergency_escalated",
                    tier="CRITICAL",
                    escalation_reason=reason,
                    summary=incident_summary,
                )

                # 1. Create emergency P1 ticket in Frappe immediately
                verified_user = state.get("verified_user") or {}
                customer_email = verified_user.get("email") or f"emergency_{state['call_id']}@nationalfinance.com"
                client = get_ticketing_client()

                try:
                    await asyncio.to_thread(
                        client.create_ticket,
                        customer_email=customer_email,
                        title=f"🚨 EMERGENCY / OUTAGE: {reason}",
                        body=f"CRITICAL SEV-1 INCIDENT REPORTED VIA VOICE CALL:\n\n{incident_summary}\n\nCaller: {verified_user.get('name', 'Anonymous')}\nPhone: {state.get('caller_number')}",
                        priority="urgent",
                        category="Critical Incident",
                        caller_info=verified_user,
                        custom_fields={"call_id": state["call_id"]},
                        status="Open",
                    )
                except Exception as e:
                    print(f"[EMERGENCY TICKET ERROR] {e}")

                # 2. Redirect live call to Emergency Queue (7003)
                # IMPORTANT: Do NOT set call_ending=True here. The voice announcement
                # ("This has been flagged as critical...") must play FIRST via the queued
                # response in handle_tool_call. call_ending is deferred to after the
                # response.done event via pending_transfer_after_announcement.
                channel = state.get("asterisk_channel")
                if channel:
                    transfer_res = await asyncio.to_thread(
                        transfer_call,
                        channel,
                        queue_type="emergency",
                        caller_context={
                            "caller_name": verified_user.get("name", "Emergency Caller"),
                            "tier": "CRITICAL_EMERGENCY",
                            "reason": reason,
                        }
                    )
                    if transfer_res.get("success"):
                        # Defer call_ending — let the voice announcement play first
                        state["pending_transfer_after_announcement"] = True
                        update_call(state["call_id"], transferred=1, transfer_target=ASTERISK_QUEUE_EMERGENCY)
                        return {"success": True, "escalated": True, "target": ASTERISK_QUEUE_EMERGENCY, "announce_first": True}

                return {"success": True, "escalated": True, "target": ASTERISK_QUEUE_EMERGENCY}

            if tool_name == "repeat_ticket_number":
                ticket_num = state.get("last_ticket_number")
                if not ticket_num:
                    return {
                        "success": False,
                        "error": "No ticket has been created or recorded yet in this call session.",
                    }
                return {
                    "success": True,
                    "ticket_number": ticket_num,
                    "ticket_number_spoken": digit_by_digit(ticket_num),
                    "message": f"Reference ticket number is {ticket_num}.",
                }

            if tool_name == "request_callback":
                verified_user = state.get("verified_user") or {}
                callback_phone = arguments.get("contact_number") or state.get("caller_number") or "N/A"
                pref_time = arguments.get("preferred_time", "ASAP")
                notes = arguments.get("notes") or state.get("issue_summary") or "Caller requested callback"

                customer_email = verified_user.get("email") or f"callback_{state['call_id']}@nationalfinance.com"
                client = get_ticketing_client()

                ticket_title = f"📞 Callback Request: {verified_user.get('name', 'Caller')} ({pref_time})"
                ticket_body = (
                    f"SCHEDULED CALLBACK REQUEST:\n\n"
                    f"Caller: {verified_user.get('name', 'Unverified Caller')}\n"
                    f"Employee ID: {verified_user.get('employee_id', state.get('employee_id', 'N/A'))}\n"
                    f"Department: {verified_user.get('department', 'N/A')}\n"
                    f"Tier: {state.get('tier', 'STANDARD')}\n"
                    f"Callback Phone Number: {callback_phone}\n"
                    f"Preferred Time: {pref_time}\n"
                    f"Issue / Reason: {notes}\n\n"
                    f"Status: Scheduled Callback Request via Voice Agent Arif"
                )

                try:
                    result = await asyncio.to_thread(
                        client.create_ticket,
                        customer_email=customer_email,
                        title=ticket_title,
                        body=ticket_body,
                        priority="2 normal" if state.get("tier") == "STANDARD" else "3 high",
                        category="Callback Request",
                        caller_info=verified_user,
                        custom_fields={
                            "call_id": state["call_id"],
                            "callback_phone": callback_phone,
                            "preferred_time": pref_time,
                        },
                        status="Open",
                    )
                    ticket_number = result.get("ticket_number")
                    if ticket_number:
                        state["last_ticket_number"] = ticket_number
                        state["ticket_created"] = True
                        state["current_state"] = "wrap_up"

                        update_call(
                            state["call_id"],
                            ticket_number=ticket_number,
                            status="callback_scheduled",
                            summary=f"Callback requested: {notes} (Phone: {callback_phone}, Time: {pref_time})",
                        )

                        return {
                            "success": True,
                            "ticket_number": ticket_number,
                            "ticket_number_spoken": digit_by_digit(ticket_number),
                            "callback_phone": callback_phone,
                            "preferred_time": pref_time,
                            "message": f"Callback ticket {ticket_number} scheduled for {pref_time} at {callback_phone}.",
                        }

                    return {"success": False, "error": "Ticketing backend did not return ticket number."}
                except Exception as exc:
                    print(f"[CALLBACK TICKET ERROR] {exc!r}")
                    return {"success": False, "error": str(exc)}

            if tool_name == "check_ticket_status":
                ticket_num = arguments.get("ticket_number", "").strip()
                if not ticket_num:
                    return {"success": False, "error": "Ticket reference number is required."}

                cleaned_ticket = ticket_num.replace(" ", "-").upper()
                while "--" in cleaned_ticket:
                    cleaned_ticket = cleaned_ticket.replace("--", "-")

                client = get_ticketing_client()
                try:
                    ticket_data = await asyncio.to_thread(client.get_ticket, cleaned_ticket)
                except Exception as exc:
                    print(f"[CHECK TICKET ERROR] {exc!r}")
                    return {"success": False, "error": str(exc)}

                if not ticket_data:
                    return {
                        "success": False,
                        "found": False,
                        "ticket_number": cleaned_ticket,
                        "ticket_number_spoken": digit_by_digit(cleaned_ticket),
                        "message": f"No ticket found with reference {cleaned_ticket} in the IT Helpdesk.",
                    }

                found_number = ticket_data.get("ticket_number", cleaned_ticket)
                status = ticket_data.get("status", "Open")
                subject = ticket_data.get("subject", "IT Support Request")
                priority = ticket_data.get("priority", "Medium")
                approval_status = ticket_data.get("approval_status", "Not Required")
                requires_approval = ticket_data.get("requires_approval", False)
                resolution_details = ticket_data.get("resolution_details", "")

                state["last_ticket_number"] = found_number

                return {
                    "success": True,
                    "found": True,
                    "ticket_number": found_number,
                    "ticket_number_spoken": digit_by_digit(found_number),
                    "status": status,
                    "subject": subject,
                    "priority": priority,
                    "requires_approval": requires_approval,
                    "approval_status": approval_status,
                    "resolution_details": resolution_details,
                    "message": f"Ticket {found_number} status is {status}.",
                }

            if tool_name == "transfer_to_agent":
                reason = arguments.get("reason", "caller_requested_human_agent")
                queue_type = arguments.get("queue_type", "standard")

                # STRICT VERIFICATION GATE:
                # Unverified callers must NEVER be transferred to human IT support or any queue.
                verified_user = state.get("verified_user") or {}
                if not verified_user.get("employee_id"):
                    state["verification_attempts"] += 1
                    attempts_left = max(0, 3 - state["verification_attempts"])
                    print(f"[SECURITY GATE] Transfer REJECTED: Caller not verified. Attempt {state['verification_attempts']}/3. (Call ID: {state['call_id']})")
                    log_security_event("unverified_transfer_blocked", state.get("caller_number") or "unknown", f"attempts={state['verification_attempts']}")

                    if state["verification_attempts"] >= 3:
                        state["current_state"] = "closing"
                        update_call(state["call_id"], status="verification_failed")
                        queue_goodbye("verification_failed")
                        return {
                            "success": False,
                            "blocked": True,
                            "attempts_left": 0,
                            "action": "call_will_end",
                            "error": "Access Denied: Maximum verification attempts reached. Security policy strictly prohibits unverified transfers. Call will now end.",
                        }

                    return {
                        "success": False,
                        "blocked": True,
                        "attempts_left": attempts_left,
                        "error": (
                            f"Access Denied: Company policy strictly requires employee identity verification before transferring to human IT support. "
                            f"Unverified callers CANNOT be transferred. The caller has {attempts_left} verification attempt(s) remaining. "
                            f"You MUST refuse the transfer and ask the caller for their full name and 4-digit employee ID."
                        ),
                    }

                if state.get("is_executive") or state.get("tier") == "P0_EXECUTIVE":
                    queue_type = "executive"

                channel = state.get("asterisk_channel")
                ticket_ref = state.get("last_ticket_number")
                ticket_spoken = digit_by_digit(ticket_ref) if ticket_ref else None

                # Pre-flight Asterisk AMI queue availability check
                queue_check = await asyncio.to_thread(check_queue_availability, queue_type)
                if not queue_check.get("available", True):
                    print(f"[TRANSFER BLOCKED] Queue '{queue_type}' has 0 available agents. Triggering callback fallback.")
                    update_call(
                        state["call_id"],
                        status="queue_unavailable",
                        escalation_reason=f"Queue {queue_type} unavailable (LoggedIn={queue_check.get('logged_in', 0)}, Available={queue_check.get('available_agents', 0)})",
                    )
                    return {
                        "success": False,
                        "error": "no_agents_available",
                        "queue_name": queue_check.get("queue_name"),
                        "logged_in": queue_check.get("logged_in", 0),
                        "available_agents": queue_check.get("available_agents", 0),
                        "callers_waiting": queue_check.get("callers_waiting", 0),
                        "ticket_number": ticket_ref,
                        "ticket_number_spoken": ticket_spoken,
                        "fallback_action": "offer_callback",
                    }

                if not channel:
                    return {
                        "success": False,
                        "error": "Telephony line not available for transfer.",
                        "ticket_number": ticket_ref,
                        "ticket_number_spoken": ticket_spoken,
                        "fallback_action": "offer_callback",
                    }

                target_extension = ASTERISK_QUEUE_EXECUTIVE if queue_type == "executive" else ASTERISK_QUEUE_STANDARD
                print(f"[TRANSFER] Transferring {channel} to {queue_type} ({target_extension}). Reason={reason}")

                verified_user = state.get("verified_user") or {}
                result = await asyncio.to_thread(
                    transfer_call,
                    channel,
                    queue_type=queue_type,
                    caller_context={
                        "caller_name": verified_user.get("name", "Unknown"),
                        "employee_id": verified_user.get("employee_id", "N/A"),
                        "tier": state.get("tier", "STANDARD"),
                        "reason": reason,
                    }
                )

                if result.get("success"):
                    update_call(
                        state["call_id"],
                        transferred=1,
                        transfer_target=result.get("extension"),
                        status="transferred",
                        escalation_reason=reason,
                    )
                    wrap_up_call(status="transferred")
                    state["call_ending"] = True
                    return {"success": True, "message": f"Transferred to {queue_type} queue."}

                update_call(state["call_id"], status="transfer_failed", summary=result.get("error", "Transfer failed"))
                return {
                    "success": False,
                    "error": result.get("error", "Transfer failed"),
                    "ticket_number": ticket_ref,
                    "ticket_number_spoken": ticket_spoken,
                    "fallback_action": "offer_callback",
                }

            if tool_name == "report_audio_issue":
                state["background_noise_warning_count"] += 1
                if state["background_noise_warning_count"] >= 3:
                    queue_goodbye("audio_unclear")
                    return {"success": True, "action": "call_will_end"}
                return {"success": True, "warning_count": state["background_noise_warning_count"]}

            if tool_name == "close_call":
                reason = arguments.get("reason", "caller_requested")
                queue_goodbye(reason)
                return {"closed": True, "reason": reason}

            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        finally:
            state["tool_in_progress"] = False

    async def handle_tool_call(event):
        item = event.get("item", {})
        tool_name = item.get("name", "")
        call_id = item.get("call_id", "")
        arguments_raw = item.get("arguments", "{}")

        try:
            arguments = json.loads(arguments_raw)
        except Exception:
            arguments = {}

        print(f"[TOOL] {tool_name} args={arguments}")
        try:
            result = await execute_tool(tool_name, arguments)
        except Exception as exc:
            print(f"[TOOL ERROR] {tool_name} failed: {repr(exc)}")
            result = {"success": False, "error": f"Tool {tool_name} failed: {str(exc)}"}

        await openai_ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result),
                    },
                }
            )
        )

        prefix = language_prefix(state)

        if tool_name == "set_language":
            if result.get("action") == "executive_fast_track":
                queue_response(
                    f"{prefix} Welcome Mr. {result.get('caller_name')} respectfully. "
                    f"Say: I am transferring you directly to our Senior Executive Support Desk right now. "
                    f"Then call transfer_to_agent with queue_type='executive' immediately."
                )
            elif state["language"] == "ar":
                queue_response("Respond only in Arabic. Say: أهلاً وسهلاً بك في الدعم الفني. تفضل بالاسم الكامل لو سمحت؟")
            else:
                queue_response("Respond only in English. Say: Thank you for choosing English. May I please have your full name?")

        elif tool_name == "capture_name" and result.get("success"):
            queue_response(f"{prefix} Ask for the caller's employee ID. Keep it short.")

        elif tool_name == "capture_employee_id" and result.get("success"):
            queue_response(f"{prefix} Say please wait while I verify your details, then call verify_user immediately.")

        elif tool_name == "verify_user" and result.get("verified"):
            if result.get("is_executive"):
                queue_response(
                    f"{prefix} Greet Mr. {result.get('name')} with executive priority. "
                    f"Say: Connecting you to our Priority Executive Desk immediately. Then call transfer_to_agent with queue_type='executive'."
                )
            elif result.get("vip"):
                queue_response(
                    f"{prefix} Say the caller is verified and marked for priority support. "
                    f"Ask: How can I assist you today?"
                )
            else:
                queue_response(f"{prefix} Say the caller is verified. Then ask: How can I help you today?")

        elif tool_name == "verify_user" and not result.get("verified"):
            if result.get("reason") == "account_deactivated":
                if state.get("language") == "ar":
                    queue_response(
                        f"{prefix} أخبر المتصل بلباقة أن هذا الحساب غير مفعل في دليل الموظفين، ويجب التواصل مع إدارة الموارد البشرية أو مسؤول تقنية المعلومات."
                    )
                else:
                    queue_response(
                        f"{prefix} Politely inform the caller that this employee account is deactivated in the directory, and advise them to contact HR or IT administration."
                    )
            elif result.get("attempts_left", 0) > 0:
                attempts_left = result.get("attempts_left")
                if state.get("language") == "ar":
                    queue_response(
                        f"Respond only in Arabic. Say: البيانات غير متطابقة مع سجلات الموظفين. متبقي لديك {attempts_left} محاولات للتحقق. يرجى تزويدي بالاسم الكامل ورقمك الوظيفي المكون من 4 أرقام."
                    )
                else:
                    queue_response(
                        f"Respond only in English. Say: Those details do not match our employee directory. You have {attempts_left} verification attempt(s) remaining. May I please have your full name and 4-digit employee ID?"
                    )

        elif tool_name == "lookup_knowledge_base" and result.get("found"):
            if state["language"] == "ar":
                queue_response(
                    "Respond only in Arabic. Acknowledge with a natural filler: تمام، فهمت عليك تماماً، ولا تشيل هم بنحل المشكلة معك خطوة بخطوة. "
                    "قدم الخطوة الأولى فقط من الدليل الفني بوضوح، واطلب من المتصل تجربتها الآن وإخبارك بما يظهر معه. "
                    "ممنوع منعاً باتاً عرض إنشاء تذكرة الآن، يجب اتباع 3 إلى 4 خطوات تشخيصية متتالية أولاً."
                )
            else:
                queue_response(
                    "Respond only in English. Acknowledge with a natural corporate filler: Umm, I understand how frustrating that is. Let's troubleshoot that together right now. "
                    "Give Step 1 from the playbook clearly. Ask the caller to try Step 1 right now and tell you what happens. "
                    "DO NOT offer to create a ticket yet. You must guide the caller through 3 to 4 troubleshooting steps first."
                )

        elif tool_name == "lookup_knowledge_base" and not result.get("found"):
            if state["language"] == "ar":
                queue_response(
                    "Respond only in Arabic. Acknowledge with a natural filler: تمام، فهمت مشكلتك وخلنا نشيك عليها مع بعض خطوة بخطوة. "
                    "قدم الخطوة التشخيصية الأولى المناسبة للمشكلة (مثل إعادة تشغيل الجهاز أو فحص التوصيلات أو التحقق من الشبكة). "
                    "اطلب من المتصل تجربتها وانتظر رده. لا تعرض إنشاء تذكرة الآن، بل ابدأ استكشاف الأخطاء خطوة بخطوة (3 إلى 4 خطوات)."
                )
            else:
                queue_response(
                    "Respond only in English. Acknowledge with a natural filler: Umm, I got it. Let's troubleshoot that together right now. "
                    "Provide Step 1 of standard enterprise IT diagnostics for this issue. "
                    "Ask the caller to test Step 1 right now and tell you what happens. "
                    "DO NOT offer or ask to open a ticket yet. Guide through 3 to 4 diagnostic steps first."
                )

        elif tool_name == "record_resolution" and result.get("success"):
            ticket_spoken = result.get("ticket_number_spoken")
            if state["language"] == "ar":
                queue_response(
                    f"Respond only in Arabic. Say clearly: ممتاز جداً! تم حل المشكلة وإغلاق التذكرة بنجاح برقم مرجعي: {ticket_spoken}. "
                    f"وأكرر الرقم للتأكيد: {ticket_spoken}. هل تحتاج أي مساعدة أخرى؟"
                )
            else:
                queue_response(
                    f"Respond only in English. Say clearly: Excellent! I'm glad we could get that resolved for you today. "
                    f"I have logged and closed this ticket under reference number {ticket_spoken}. "
                    f"Let me repeat that for your records: {ticket_spoken}. Is there anything else I can help you with today?"
                )

        elif tool_name == "create_ticket" and result.get("success"):
            ticket_spoken = result.get("ticket_number_spoken")
            if state["language"] == "ar":
                queue_response(
                    f"Respond only in Arabic. Say clearly: تم تسجيل تذكرتك بنجاح برقم مرجعي: {ticket_spoken}. "
                    f"وأكرر الرقم للتأكيد: {ticket_spoken}. سيتابع فريق الدعم الفني طلبك بأسرع وقت. هل هناك أي استفسار آخر يمكنني مساعدتك به؟"
                )
            else:
                queue_response(
                    f"Respond only in English. Say clearly: Your IT ticket has been logged under reference number {ticket_spoken}. "
                    f"Let me repeat that for your records: {ticket_spoken}. Our IT support team will follow up with you. Is there anything else I can assist you with?"
                )

        elif tool_name == "create_ticket" and not result.get("success"):
            err = result.get("error", "Ticket creation could not be completed.")
            if result.get("premature_ticket"):
                if state["language"] == "ar":
                    queue_response(
                        "Respond only in Arabic. Say naturally with a filler: تمام، ولا تشيل هم بنحلها معك خطوة بخطوة. "
                        "قدم الخطوة الأولى لاستكشاف المشكلة واطلب من المتصل تجربتها الآن. لا تقم بإنشاء تذكرة قبل استكشاف الأخطاء."
                    )
                else:
                    queue_response(
                        "Respond only in English. Say naturally with a filler: Umm, let's troubleshoot this together first to see if we can resolve it right now. "
                        "Provide Step 1 of diagnostic troubleshooting and ask the caller to test it. Do not create a ticket yet."
                    )
            elif result.get("out_of_scope"):
                if state["language"] == "ar":
                    queue_response(
                        "Respond only in Arabic. Politely inform the caller that this phone line is strictly for internal IT Support, and guide them to contact customer service for banking or loan inquiries."
                    )
                else:
                    queue_response(
                        "Respond only in English. Politely inform the caller that this phone line is strictly for internal IT Support, and guide them to contact customer service for banking or loan inquiries."
                    )
            elif result.get("unverified"):
                if state["language"] == "ar":
                    queue_response(
                        "Respond only in Arabic. Say that you must verify their employee identity before a ticket can be created. Ask for their full name and employee ID."
                    )
                else:
                    queue_response(
                        "Respond only in English. Say that you must verify their employee identity before creating a ticket. Ask for their full name and employee ID."
                    )
            elif result.get("duplicate_prevented"):
                ticket_spoken = result.get("ticket_number_spoken")
                if state["language"] == "ar":
                    queue_response(
                        f"Respond only in Arabic. Say that a ticket ({ticket_spoken}) is already registered for this call. Ask how else you can assist."
                    )
                else:
                    queue_response(
                        f"Respond only in English. Say that a ticket ({ticket_spoken}) is already registered for this call. Ask how else you can assist."
                    )
            else:
                if state["language"] == "ar":
                    queue_response(
                        f"Respond only in Arabic. Apologize and explain: {err}. Ask the caller for clarification."
                    )
                else:
                    queue_response(
                        f"Respond only in English. Apologize and explain: {err}. Ask the caller for clarification."
                    )

        elif tool_name == "check_ticket_status":
            if result.get("found"):
                ticket_spoken = result.get("ticket_number_spoken")
                status = result.get("status", "Open")
                req_appr = result.get("requires_approval")
                appr_status = result.get("approval_status")
                res_details = result.get("resolution_details")

                if state["language"] == "ar":
                    appr_msg = f" وحالة الموافقة هي: {appr_status} من مدير القسم." if req_appr else ""
                    res_msg = f" ملاحظات الحل: {res_details}." if res_details else ""
                    queue_response(
                        f"Respond only in Arabic. Say: حالة التذكرة {ticket_spoken} هي {status}.{appr_msg}{res_msg} هل تود الاستفسار عن شيء آخر؟"
                    )
                else:
                    appr_msg = f" Approval status is: {appr_status}." if req_appr else ""
                    res_msg = f" Resolution notes: {res_details}." if res_details else ""
                    queue_response(
                        f"Respond only in English. Say: Ticket {ticket_spoken} is currently {status}.{appr_msg}{res_msg} Is there anything else I can assist you with?"
                    )
            else:
                ticket_spoken = result.get("ticket_number_spoken")
                if state["language"] == "ar":
                    queue_response(
                        f"Respond only in Arabic. Say: لم أتمكن من العثور على التذكرة {ticket_spoken} في نظام الدعم الفني. يرجى التأكد من رقم التذكرة."
                    )
                else:
                    queue_response(
                        f"Respond only in English. Say: I could not locate ticket {ticket_spoken} in the IT Helpdesk. Please verify the ticket reference number."
                    )

        elif tool_name == "repeat_ticket_number":
            if result.get("success"):
                ticket_spoken = result.get("ticket_number_spoken")
                if state["language"] == "ar":
                    queue_response(
                        f"Respond only in Arabic. Say clearly: رقم التذكرة هو {ticket_spoken}. Repeat it slowly digit by digit. Then ask if they need anything else."
                    )
                else:
                    queue_response(
                        f"Respond only in English. Say clearly: Your ticket number is {ticket_spoken}. Repeat it slowly digit by digit. Then ask if they need anything else."
                    )
            else:
                if state["language"] == "ar":
                    queue_response(
                        "Respond only in Arabic. Politely inform the caller that no ticket has been created yet for this call, and ask how you can help."
                    )
                else:
                    queue_response(
                        "Respond only in English. Politely inform the caller that no ticket has been created yet for this call, and ask how you can help."
                    )

        elif tool_name == "request_callback" and result.get("success"):
            ticket_spoken = result.get("ticket_number_spoken")
            pref = result.get("preferred_time")
            phone = result.get("callback_phone")
            if state["language"] == "ar":
                queue_response(
                    f"Respond only in Arabic. Say: تم تسجيل طلب معاودة الاتصال بنجاح تحت رقم التذكرة {ticket_spoken}. سيتواصل معك أحد مهندسي الدعم الفني على الرقم {phone}. هل هناك أي استفسار آخر؟"
                )
            else:
                queue_response(
                    f"Respond only in English. Say: Your callback request has been registered under ticket {ticket_spoken}. An IT support specialist will call you back at {phone}. Is there anything else I can help you with?"
                )

        elif tool_name == "transfer_to_agent" and not result.get("success"):
            if result.get("blocked"):
                attempts_left = result.get("attempts_left", 0)
                if attempts_left > 0:
                    if state.get("language") == "ar":
                        queue_response(
                            f"Respond only in Arabic. Say: أعتذر منك، تنص سياسة الأمان في ناشيونال فاينانس على وجوب التحقق من الهوية الوظيفية أولاً قبل تحويل أي مكالمة للدعم الفني. متبقي لديك {attempts_left} محاولات. يرجى تزويدي بالاسم الكامل ورقمك الوظيفي لنتمكن من المتابعة."
                        )
                    else:
                        queue_response(
                            f"Respond only in English. Say: I apologize, but per National Finance security policy, caller identity must be verified before transferring to IT support. You have {attempts_left} verification attempt(s) remaining. May I please have your full name and 4-digit employee ID?"
                        )
            else:
                ticket_spoken = result.get("ticket_number_spoken")
                ticket_part_en = f" Your reference ticket number is {ticket_spoken}." if ticket_spoken else ""
                ticket_part_ar = f" رقم التذكرة المرجعي الخاص بك هو {ticket_spoken}." if ticket_spoken else ""
                is_empty_queue = result.get("error") == "no_agents_available"
                if state["language"] == "ar":
                    if is_empty_queue:
                        queue_response(
                            f"Respond only in Arabic. Say: أعتذر بشدة، جميع ممثلي الدعم الفني غير متاحين حالياً في قائمة الانتظار.{ticket_part_ar} هل ترغب في أن أسجل لك طلب معاودة اتصال ليتواصل معك مهندس الدعم في أقرب وقت؟"
                        )
                    else:
                        queue_response(
                            f"Respond only in Arabic. Say: أعتذر بشدة، جميع ممثلي الدعم الفني مشغولون حالياً.{ticket_part_ar} هل ترغب في أن أسجل لك طلب معاودة اتصال ليتواصل معك مهندس الدعم في أقرب وقت؟"
                        )
                else:
                    if is_empty_queue:
                        queue_response(
                            f"Respond only in English. Say: I apologize, all our IT support specialists are currently unavailable in the queue.{ticket_part_en} Would you like me to schedule a callback so an engineer can reach out to you directly?"
                        )
                    else:
                        queue_response(
                            f"Respond only in English. Say: I apologize, all our IT support specialists are currently assisting other callers.{ticket_part_en} Would you like me to schedule a callback so an engineer can reach out to you directly?"
                        )

        elif tool_name == "escalate_emergency":
            if state["language"] == "ar":
                queue_response(
                    "Respond only in Arabic. Say: تم تصنيف الحالة كطارئة. أحولك مباشرة إلى فريق الطوارئ والمهندسين المناوبين."
                )
            else:
                queue_response(
                    "Respond only in English. Say: This has been flagged as a critical incident. Transferring you immediately to the on-call emergency team."
                )

        await send_queued_response_if_any(default_fallback=True)

    async def asterisk_to_openai():
        try:
            async for message in asterisk_ws:
                if state["call_ending"]:
                    break

                if time.monotonic() - state["started_monotonic"] > CALL_MAX_SECONDS:
                    queue_goodbye("timeout")
                    await send_queued_response_if_any()
                    continue

                # Full-duplex: only skip if call is completely closing/ending
                if state["closing"]:
                    continue

                if isinstance(message, bytes) and len(message) > 0:
                    await openai_ws.send(
                        json.dumps(
                            {
                                "type": "input_audio_buffer.append",
                                "audio": base64.b64encode(message).decode("utf-8"),
                            }
                        )
                    )
                else:
                    if isinstance(message, str):
                        # Detect in-band DTMF keypress frames from PBX or WebSocket client
                        if "DTMF" in message or "dtmf" in message.lower():
                            digit_match = re.search(r"[0-9*#]", message)
                            if digit_match:
                                await process_dtmf_digit(digit_match.group(0))
                                continue

                        if "MEDIA_START" in message:
                            parts = message.split()
                            for part in parts:
                                if part.startswith("channel:"):
                                    state["asterisk_channel"] = part.replace("channel:", "").strip()
                                if part.startswith("caller:"):
                                    caller_num = part.replace("caller:", "").strip()
                                    if caller_num:
                                        state["caller_number"] = caller_num
                                        update_call(state["call_id"], caller_number=caller_num)

                                        # Fast-track check for CEO, CFO, C-Suite
                                        pre_user = lookup_caller_by_phone(caller_num)
                                        if pre_user and pre_user.get("is_executive"):
                                            state["is_executive"] = True
                                            state["is_vip"] = True
                                            state["tier"] = "P0_EXECUTIVE"
                                            state["verified_user"] = pre_user
                                            state["caller_name"] = pre_user["name"]
                                            state["employee_id"] = pre_user["employee_id"]
                                            update_call(
                                                state["call_id"],
                                                is_vip=1,
                                                tier="P0_EXECUTIVE",
                                                verified_name=pre_user["name"],
                                                employee_id=pre_user["employee_id"],
                                            )
                                            print(f"[EXECUTIVE DETECTED] CLI match for {pre_user['name']} ({pre_user['role']})")

                                if part.startswith("channel_id:"):
                                    real_call_id = part.replace("channel_id:", "").strip()
                                    if real_call_id:
                                        old_call_id = state["call_id"]
                                        state["call_id"] = real_call_id
                                        rename_call_id(old_call_id, real_call_id)
                                        update_call(state["call_id"], status="in_progress")

                            if state["caller_number"]:
                                rl = check_rate_limit(
                                    f"call:{state['caller_number']}",
                                    CALLS_PER_NUMBER_LIMIT,
                                    CALLS_PER_NUMBER_WINDOW,
                                    CALLS_PER_NUMBER_LOCK,
                                )
                                if not rl["allowed"]:
                                    log_security_event(
                                        "call_rate_limited",
                                        state["caller_number"],
                                        f"reason={rl['reason']} retry_after={rl['retry_after']}",
                                    )
                                    update_call(state["call_id"], status="rejected")
                                    state["call_ending"] = True
                                    await asterisk_ws.close()
                                    return

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            print(f"[ASTERISK->OPENAI] {exc!r}")

    async def openai_to_asterisk():
        try:
            async for raw in openai_ws:
                event = json.loads(raw)
                event_type = event.get("type", "")

                if event_type == "response.created":
                    state["active_response"] = True

                elif event_type == "input_audio_buffer.speech_started":
                    # Caller interrupted while AI is speaking (barge-in)
                    state["active_response"] = False
                    if hasattr(asterisk_ws, "clear_outbound_queue"):
                        asterisk_ws.clear_outbound_queue()
                    try:
                        await openai_ws.send(json.dumps({"type": "response.cancel"}))
                    except Exception:
                        pass

                elif event_type == "response.output_audio.delta":
                    if state["call_ending"]:
                        break
                    audio_b64 = event.get("delta", "")
                    if audio_b64:
                        raw_pcm = base64.b64decode(audio_b64)
                        if len(raw_pcm) > 0:
                            await asterisk_ws.send(raw_pcm)

                elif event_type == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        await handle_tool_call(event)

                elif event_type == "response.done":
                    response = event.get("response", {})
                    status = response.get("status")
                    state["active_response"] = False

                    if status == "failed":
                        state["call_ending"] = True
                        update_call(state["call_id"], status="openai_response_failed")
                        try:
                            await asterisk_ws.close()
                        except Exception:
                            pass
                        return

                    if state["close_after_next_response_done"]:
                        await asyncio.sleep(2)
                        state["call_ending"] = True
                        close_status = "verification_failed" if state.get("verification_attempts", 0) >= 3 else "completed"
                        wrap_up_call(status=close_status)
                        try:
                            await asterisk_ws.close()
                        except Exception:
                            pass
                        return

                    # After emergency/executive announcement plays, NOW finalize the transfer
                    if state.get("pending_transfer_after_announcement"):
                        state["pending_transfer_after_announcement"] = False
                        await asyncio.sleep(1.5)  # Brief pause so caller hears the full announcement
                        state["call_ending"] = True
                        wrap_up_call(status="transferred")
                        try:
                            await asterisk_ws.close()
                        except Exception:
                            pass
                        return

                    await send_queued_response_if_any()

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = (event.get("transcript") or "").strip()
                    if transcript:
                        t_str = time.strftime("%H:%M:%S")
                        state["transcript_lines"].append(f"[{t_str}] Caller: {transcript}")
                        print(f"[CALLER SAID] {transcript}")

                        # Deterministic initial language selection gate:
                        if state["language"] is None:
                            t_lower = transcript.lower()
                            is_english = any(w in t_lower for w in ["english", "inglizi", "ingleezi"]) or bool(re.search(r"\b(en|english)\b", t_lower))
                            is_arabic = any(w in transcript for w in ["عربي", "عربية", "العربية"]) or any(w in t_lower for w in ["arabic", "arabi"])

                            if is_english and not is_arabic:
                                await execute_tool("set_language", {"language": "en"})
                                queue_response("Respond only in English. Say: Thank you for choosing English. May I please have your full name?")
                                await send_queued_response_if_any()
                            elif is_arabic and not is_english:
                                await execute_tool("set_language", {"language": "ar"})
                                queue_response("Respond only in Arabic. Say: شكراً لك. تفضل بالاسم الكامل لو سمحت؟")
                                await send_queued_response_if_any()

                elif event_type in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
                    a_text = (event.get("transcript") or "").strip()
                    if a_text:
                        t_str = time.strftime("%H:%M:%S")
                        state["transcript_lines"].append(f"[{t_str}] Arif: {a_text}")

                elif event_type == "error":
                    print(f"[OPENAI ERROR] {json.dumps(event, indent=2)}")
                    error = event.get("error", {})
                    if error.get("code") == "conversation_already_has_active_response":
                        state["active_response"] = True
                    else:
                        # Release active_response lock on general errors so conversation is not blocked
                        state["active_response"] = False

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            print(f"[OPENAI->ASTERISK] {exc!r}")

    tasks = [
        asyncio.create_task(asterisk_to_openai()),
        asyncio.create_task(openai_to_asterisk()),
    ]

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    try:
        await openai_ws.close()
    except Exception:
        pass

    if not state["call_ending"] and state.get("current_state") != "transferred":
        wrap_up_call(status="ended")
    elif state.get("current_state") == "transferred":
        wrap_up_call(status="transferred")


async def main():
    validate_bridge_config()
    reconcile_stale_calls(CALL_MAX_SECONDS)

    ssl_context = None
    if ASTERISK_WS_SSL_CERT and ASTERISK_WS_SSL_KEY:
        if os.path.exists(ASTERISK_WS_SSL_CERT) and os.path.exists(ASTERISK_WS_SSL_KEY):
            import ssl
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(ASTERISK_WS_SSL_CERT, ASTERISK_WS_SSL_KEY)
            print(f"[SERVER] WSS/TLS enabled with certificate: {ASTERISK_WS_SSL_CERT}")
        else:
            print("[SERVER] Notice: ASTERISK_WS_SSL_CERT/KEY configured but files not found. Using plain WS on loopback.")

    print(f"[SERVER] Model: {OPENAI_REALTIME_MODEL}")
    print(f"[SERVER] Ticketing Provider: Frappe Helpdesk")
    print(f"[SERVER] Loaded {len(KNOWLEDGE_BASE)} Knowledge Base Playbooks: {list(KNOWLEDGE_BASE.keys())}")

    audiosocket_server = await asyncio.start_server(
        handle_audiosocket_connection,
        ASTERISK_WS_HOST,
        ASTERISK_WS_PORT,
    )
    print(f"[SERVER] Asterisk AudioSocket listening on {ASTERISK_WS_HOST}:{ASTERISK_WS_PORT}")

    ws_port = ASTERISK_WS_PORT + 1
    ws_server = await websockets.serve(
        handle_asterisk_call,
        ASTERISK_WS_HOST,
        ws_port,
        ssl=ssl_context,
        max_size=None,
        ping_interval=20,
        ping_timeout=10,
    )
    print(f"[SERVER] WebSocket fallback listening on {ASTERISK_WS_HOST}:{ws_port}")

    async with audiosocket_server, ws_server:
        print("[SERVER] Ready. Waiting for calls from Asterisk AudioSocket...")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[SERVER] Stopped")
