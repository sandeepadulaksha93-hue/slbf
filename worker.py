"""
SLBFE Worker v2
================
- Master URL එකක් විතරයි ඕනෑ
- CSV නෑ — data master එකෙන් pull කරනවා
- GO signal ආවාම auto submit
- Result master ට report කරනවා
"""

import logging
import os
import sys
import time
import threading
import socket

import requests
from bs4 import BeautifulSoup

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By
except ImportError:
    print("pip install selenium")
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────
MASTER_URL    = "https://interfrontal-inexpressibly-verla.ngrok-free.dev"
WORKER_ID     = socket.gethostname()
PAGE_URL      = "https://services.slbfe.lk/Israel/WebPortal"
SUBMIT_URL    = "https://services.slbfe.lk/Israel/SubmitData"
RECAPTCHA_KEY = "6Le95mgsAAAAAMSHb9YdMAuwE2Mo6uQ9ETT4lqfB"
POLL_INTERVAL = 5

# ── Logging ────────────────────────────────────────────────────
def get_safe_log_path():
    """exe එක run වෙන තැනට log file එක හදනවා - System32 issue fix"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    log_path = os.path.join(base, "worker_log.txt")
    try:
        # Write permission test
        with open(log_path, "a", encoding="utf-8") as f:
            pass
        return log_path
    except (PermissionError, OSError):
        # Fallback: AppData/Local (always writable)
        fallback_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SLBFEWorker")
        os.makedirs(fallback_dir, exist_ok=True)
        return os.path.join(fallback_dir, "worker_log.txt")

def setup_logging():
    log_path = get_safe_log_path()
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.insert(0, logging.FileHandler(log_path, encoding="utf-8"))
    except Exception:
        pass  # File logging fail වුණත් console logging continue වෙනවා
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )

# ── Poll Master ────────────────────────────────────────────────
def poll_master():
    try:
        r = requests.get(
            f"{MASTER_URL}/api/status",
            params={"id": WORKER_ID},
            timeout=10
        )
        return r.json()
    except:
        return None

# ── Report Result ──────────────────────────────────────────────
def report_result(name, nic, status, ref=""):
    try:
        requests.post(
            f"{MASTER_URL}/api/result",
            json={"name": name, "nic": nic, "status": status,
                  "ref": ref, "worker_id": WORKER_ID},
            timeout=10
        )
    except Exception as e:
        logging.warning(f"Report failed: {e}")

# ── Chrome Driver (fast + cached) ───────────────────────────────
_CACHED_DRIVER_PATH = None

def get_chromedriver_path():
    """ChromeDriver path එක cache කරලා, fresh download එක skip කරනවා"""
    global _CACHED_DRIVER_PATH
    if _CACHED_DRIVER_PATH and os.path.exists(_CACHED_DRIVER_PATH):
        return _CACHED_DRIVER_PATH

    # Persistent cache location (AppData) — exe එක run වෙන location එක මත depend නොවී
    cache_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SLBFEWorker", "wdm")
    os.makedirs(cache_dir, exist_ok=True)
    os.environ["WDM_LOCAL"] = "1"
    os.environ["WDM_LOG_LEVEL"] = "0"

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from webdriver_manager.core.os_manager import ChromeType
        path = ChromeDriverManager(path=cache_dir).install()
        _CACHED_DRIVER_PATH = path
        return path
    except Exception as e:
        logging.warning(f"webdriver_manager failed: {e} — trying Selenium Manager fallback")
        return None  # Selenium 4.10+ auto-resolves driver if None

def get_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    driver_path = get_chromedriver_path()
    try:
        from selenium.webdriver.chrome.service import Service
        if driver_path:
            return webdriver.Chrome(service=Service(driver_path), options=options)
        else:
            # Selenium 4.10+ Selenium Manager — built-in driver resolver, no network call needed if cached
            return webdriver.Chrome(options=options)
    except Exception as e:
        logging.error(f"Chrome driver init failed: {e}")
        raise

# ── reCAPTCHA ──────────────────────────────────────────────────
def get_recaptcha():
    driver = None
    try:
        driver = get_driver()
        driver.get(PAGE_URL)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.NAME, "nic"))
        )
        time.sleep(8)
        for _ in range(10):
            if driver.execute_script(
                "return typeof grecaptcha !== 'undefined' && typeof grecaptcha.execute === 'function';"
            ): break
            time.sleep(1)

        driver.execute_script("""
            window._rc = null;
            grecaptcha.execute(arguments[0], {action:'submit'})
            .then(function(t){ window._rc = t; })
            .catch(function(e){ window._rc = 'ERR:'+e; });
        """, RECAPTCHA_KEY)

        for _ in range(30):
            time.sleep(1)
            t = driver.execute_script("return window._rc;")
            if t and not t.startswith("ERR:"):
                driver.quit()
                return t
            if t and t.startswith("ERR:"):
                break
        driver.quit()
        return None
    except Exception as e:
        if driver:
            try: driver.quit()
            except: pass
        logging.error(f"reCAPTCHA: {e}")
        return None

# ── CSRF ───────────────────────────────────────────────────────
def get_csrf():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    })
    for _ in range(10):
        try:
            resp = session.get(PAGE_URL, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            tok  = soup.find("input", {"name": "__RequestVerificationToken"})
            if tok: return session, tok["value"]
            time.sleep(2)
        except: time.sleep(2)
    return None, None

# ── Submit ─────────────────────────────────────────────────────
def submit_application(data):
    name = f"{data.get('fname','')} {data.get('lname','')}".strip()
    logging.info(f"🚀 Submitting: {name}")

    csrf_res = [None, None]
    rc_res   = [None]

    def gc(): s,t = get_csrf(); csrf_res[0]=s; csrf_res[1]=t
    def gr(): rc_res[0] = get_recaptcha()

    t1 = threading.Thread(target=gc)
    t2 = threading.Thread(target=gr)
    t1.start(); t2.start()
    t1.join(); t2.join()

    session, csrf, rc = csrf_res[0], csrf_res[1], rc_res[0]

    if not session or not csrf or not rc:
        logging.error("❌ Token failed!")
        report_result(name, data.get("nic",""), "FAILED")
        return

    post = {
        "__RequestVerificationToken": csrf,
        "website"       : "",
        "nic"           : data.get("nic",""),
        "pp_no"         : data.get("pp_no",""),
        "dob"           : data.get("dob",""),
        "gender"        : data.get("gender",""),
        "lname"         : data.get("lname",""),
        "fname"         : data.get("fname",""),
        "farthers_name" : data.get("farthers_name",""),
        "pp_expire_date": data.get("pp_expire_date",""),
        "civil_status"  : data.get("civil_status",""),
        "mobile1"       : data.get("mobile1",""),
        "mobile2"       : data.get("mobile2",""),
        "add1"          : data.get("add1",""),
        "add2"          : data.get("add2",""),
        "town"          : data.get("town",""),
        "district"      : data.get("district",""),
        "Sector"        : data.get("sector","S002"),
        "JobCate"       : data.get("job_cate",""),
        "submitted"     : "false",
        "gRecaptchaResponse": rc,
    }

    if data.get("civil_status") == "M":
        post.update({
            "Partner_nic"   : data.get("partner_nic",""),
            "Partner_ppno"  : data.get("partner_ppno",""),
            "Partner_dob"   : data.get("partner_dob",""),
            "Partner_lname" : data.get("partner_lname",""),
            "Partner_fname" : data.get("partner_fname",""),
        })

    try:
        resp = session.post(
            SUBMIT_URL, data=post,
            headers={
                "Referer":"https://services.slbfe.lk",
                "Origin" :"https://services.slbfe.lk",
                "Content-Type":"application/x-www-form-urlencoded",
                "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=30, allow_redirects=True,
        )
        url  = resp.url
        page = resp.text.lower()
        ref  = ""
        if "Ref%20No%3A%20" in url:
            try: ref = url.split("Ref%20No%3A%20")[-1].split("&")[0]
            except: pass

        logging.info(f"  HTTP Status: {resp.status_code}")
        logging.info(f"  Final URL  : {url[:200]}")

        # Debug — response save කරනවා
        try:
            base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(base, "last_response.html"), "w", encoding="utf-8") as f:
                f.write(resp.text)
        except Exception as e:
            logging.warning(f"Could not save response: {e}")

        if any(w in page for w in ["success","thank","submitted"]):
            logging.info(f"🎉 SUCCESS: {name} | Ref: {ref}")
            report_result(name, data.get("nic",""), "SUCCESS", ref)
        elif "duplicate" in url or "duplicate" in page:
            logging.warning(f"⚠️ DUPLICATE: {name}")
            report_result(name, data.get("nic",""), "DUPLICATE", ref)
        elif "msg_err" in url:
            # msg_err URL එකේ ඇත්තටම error message එක තියෙනවා
            err_msg = ""
            if "msg=" in url:
                try: err_msg = url.split("msg=")[-1].split("&")[0]
                except: pass
            logging.warning(f"⚠️ SERVER ERROR: {name} | {err_msg}")
            report_result(name, data.get("nic",""), "FAILED", err_msg[:100])
        else:
            logging.warning(f"⚠️ UNKNOWN: {name} — check last_response.html")
            report_result(name, data.get("nic",""), "UNKNOWN")
    except Exception as e:
        logging.error(f"Submit error: {e}")
        report_result(name, data.get("nic",""), "FAILED")

# ── Windows Startup ────────────────────────────────────────────
def register_startup():
    if sys.platform != "win32": return
    try:
        import winreg
        exe = sys.executable if getattr(sys,"frozen",False) else os.path.abspath(__file__)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",0,winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key,"SLBFEWorker",0,winreg.REG_SZ,f'"{exe}"')
        winreg.CloseKey(key)
        logging.info("✅ Auto-startup registered")
    except: pass

# ── Main ───────────────────────────────────────────────────────
def main():
    setup_logging()
    logging.info("="*50)
    logging.info("  SLBFE Worker v2")
    logging.info(f"  Worker ID : {WORKER_ID}")
    logging.info(f"  Master    : {MASTER_URL}")
    logging.info("="*50)

    register_startup()
    submitted = False
    data_assigned = False

    # Pre-warm — ChromeDriver download/cache කරනවා GO signal එනකන් කලින්ම
    # ඒකෙන් GO click කළ ගමන් submit instant වෙනවා
    logging.info("🔧 Pre-warming ChromeDriver (one-time setup)...")
    try:
        path = get_chromedriver_path()
        if path:
            logging.info(f"✅ ChromeDriver ready (cached): {path}")
        else:
            logging.info("✅ Selenium Manager will resolve driver on first use")
    except Exception as e:
        logging.warning(f"⚠️ Pre-warm failed (will retry on submit): {e}")

    logging.info("⏳ Polling master — waiting for data + GO signal...")

    while True:
        status = poll_master()

        if status is None:
            logging.warning("  ⚠️ Master unreachable...")
            time.sleep(POLL_INTERVAL)
            continue

        my_data = status.get("data")

        if not data_assigned and my_data:
            name = f"{my_data.get('fname','')} {my_data.get('lname','')}".strip()
            logging.info(f"  ✅ Data received: {name} | NIC: {my_data.get('nic','')}")
            data_assigned = True

        if status.get("go") and my_data and not submitted:
            logging.info("🚀 GO signal! Submitting...")
            submitted = True
            submit_application(my_data)
            logging.info("✅ Done!")

        elif not status.get("go") and submitted:
            submitted = False
            data_assigned = False
            logging.info("🔄 Reset — ready for next round")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
