
# Multi-bot GC / Slide / Swipe tool (updated TOKENS & OWNER)
# - Spawns one Application per token and registers command handlers on each.
# - Commands available: /gcnc, /ncemo, /stopgcnc, /stopall, /delay, /status,
#   /targetslide, /stopslide, /slidespam, /stopslidespam, /swipe, /stopswipe,
#   /spamloop, /stopspam, /emospam, /stopemospam, /replytext, /stopreplytext,
#   /voice, /stopvoice, /addsudo, /delsudo, /listsudo, /myid, /ping, /help,
#   /ncdelet, /stopncdelet, /ncdeletstatus
#
# NOTE: These tokens are sensitive. If they are real, revoke/rotate them after testing.

import asyncio
import json
import os
import random
import itertools
import time
import logging
import sys
import subprocess
import importlib
import urllib.request
import tempfile
from typing import Dict, Set, List, Tuple
import ig_module

VENV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv")
required_packages = [
    "python-telegram-bot",
    "requests",
    "aiohttp",
    "gtts",
    "cryptography",
    "playwright",
]

package_import_names = {
    "python-telegram-bot": "telegram",
    "requests": "requests",
    "aiohttp": "aiohttp",
    "gtts": "gtts",
    "cryptography": "cryptography",
    "playwright": "playwright.async_api",
}


def in_virtualenv() -> bool:
    return hasattr(sys, "real_prefix") or sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def get_venv_python_path() -> str:
    return os.path.join(VENV_DIR, "Scripts" if os.name == "nt" else "bin", "python")


def bootstrap_pip_with_get_pip(python_executable: str) -> bool:
    get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False) as tmp_file:
            tmp_file.write(urllib.request.urlopen(get_pip_url, timeout=30).read())
            tmp_path = tmp_file.name
        subprocess.check_call([python_executable, tmp_path])
        return True
    except Exception as e:
        logging.warning(f"Could not bootstrap pip using get-pip.py for {python_executable}: {e}")
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def ensure_pip_for_python(python_executable: str) -> bool:
    try:
        subprocess.check_call([python_executable, "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        pass

    try:
        subprocess.check_call([python_executable, "-m", "ensurepip", "--upgrade"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        logging.warning(f"Could not bootstrap pip for {python_executable} using ensurepip: {e}")

    return bootstrap_pip_with_get_pip(python_executable)


def create_local_venv() -> str:
    python_path = get_venv_python_path()
    if os.path.exists(python_path):
        if ensure_pip_for_python(python_path):
            return python_path
        logging.warning("Existing venv has no pip, attempting to bootstrap pip")

    try:
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])
        if ensure_pip_for_python(python_path):
            return python_path
        logging.warning(f"Created venv but pip is not available in {python_path}")
    except Exception as e:
        logging.warning(f"Could not create local venv with venv module: {e}")

    try:
        subprocess.check_call([
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--user",
            "virtualenv",
        ])
        subprocess.check_call([sys.executable, "-m", "virtualenv", VENV_DIR])
        if ensure_pip_for_python(python_path):
            return python_path
    except Exception as e:
        logging.warning(f"Could not create local venv with virtualenv fallback: {e}")
        return ""

    return ""


def install_package(package_name: str) -> None:
    if not ensure_pip_for_python(sys.executable):
        logging.info("Bootstrapping pip for current Python before install")
        if not ensure_pip_for_python(sys.executable):
            raise RuntimeError("pip is not available and could not be bootstrapped")

    cmd = [sys.executable, "-m", "pip", "install", "--quiet", package_name]
    if not in_virtualenv():
        cmd.insert(-1, "--user")
    try:
        subprocess.check_call(cmd)
        logging.info(f"Installed missing package: {package_name}")
    except subprocess.CalledProcessError as e:
        logging.warning(f"Package install failed for {package_name}: {e}")
        if not in_virtualenv():
            venv_python = create_local_venv()
            if venv_python:
                logging.info("Re-launching script inside local virtual environment")
                os.execv(venv_python, [venv_python] + sys.argv)
        raise


def ensure_dependencies() -> None:
    for package_name in required_packages:
        module_name = package_import_names.get(package_name, package_name)
        try:
            importlib.import_module(module_name)
        except ImportError:
            install_package(package_name)
            try:
                importlib.import_module(module_name)
            except ImportError as error:
                raise RuntimeError(
                    f"Package '{package_name}' was installed but "
                    f"'{module_name}' still cannot be imported."
                ) from error


def ensure_playwright_browser() -> None:
    playwright_api = importlib.import_module("playwright.sync_api")
    with playwright_api.sync_playwright() as playwright:
        executable_path = playwright.chromium.executable_path

    if not os.path.isfile(executable_path):
        logging.info("Downloading the Playwright Chromium browser")
        subprocess.check_call(
            [sys.executable, "-m", "playwright", "install", "chromium"]
        )

    try:
        with playwright_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception as error:
        raise RuntimeError(
            "Playwright Chromium is installed but could not launch. "
            "Install the required operating-system browser libraries and retry."
        ) from error


def bootstrap_environment() -> None:
    if in_virtualenv():
        ensure_dependencies()
        ensure_playwright_browser()
        return

    venv_python = get_venv_python_path()
    if os.path.exists(venv_python):
        logging.info("Using existing local virtual environment")
        os.execv(venv_python, [venv_python] + sys.argv)

    try:
        ensure_dependencies()
        ensure_playwright_browser()
    except Exception:
        venv_python = create_local_venv()
        if venv_python:
            logging.info("Re-launching script inside local virtual environment")
            os.execv(venv_python, [venv_python] + sys.argv)
        raise


bootstrap_environment()

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram import error as telegram_error
from telegram.error import RetryAfter, Forbidden, InvalidToken, BadRequest
from telegram import ReactionTypeEmoji
from gtts import gTTS
import io

# ============================================
# WINDOWS ASYNCIO FIX (Required for Windows RDP)
# ============================================
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(
        asyncio.WindowsSelectorEventLoopPolicy()
    )
    logging.info("⚙️ Windows event loop policy set (Selector)")

# ---------------------------
# CONFIG (UPDATED)
# ---------------------------
TOKENS = [
 "8267265471:AAEBQrBQh62dj_a9DWoeWZT4tF9TBPeKbYA",
"8267265471:AAE66wHQeLMvFN5voyKOVrwbi9vFg3q2TO4",
"8451886725:AAGAjfFl7Kl_GP9xQ6W0BviVkxvGzPEHieY",
"8293884732:AAECkS-tviB7ZlM8weXV39lL7putITvngfg",
"8359614490:AAGWp5R3B2KTwSziSOv5yT5pxcaRh5FeVYg",
"8383716273:AAG7UfINwv9z3e-KQ1U8erM3HkGCM-lPocQ",
"8313155600:AAHZxlwP-xDX1xDytw1ij62N_iNyk5PQCG8",
"8267265471:AAEgEj-BlOkw5zJ1-KkyX8pZJKgLZmomqjA",
"8363738741:AAEyQOQPL-RU-k4ct6euIS0RnYZ94hpz8Ts",
"8583115776:AAHLHY094OyfBo4bPhNSHNf2RazkwJ7xVCY",
"8593326828:AAHn7AIJqXY6ANb1_89-6dPNjypxwLXPMv4",
"7991859049:AAG49TrVshj8wzQipytBDTQ7yEnphRZeZwI",
]

# Owner / initial sudo (you provided "Chat id 6416341860")
OWNER_ID = 6416341860
SUDO_FILE = "sudo.json"

# ---------------------------
# RAID TEXT FORMATTING LISTS
# ---------------------------
EMOJI_LIST = [
  "🫶","🫳","🫴","🫱","🫲","🫰","🫵","🫷","🫸","🫹",
"🪬","🪫","🪩","🪭","🪮","🪢","🪤","🪞","🪟","🪐",
"🧿","🔮","🕳️","🌀","🌫️","🌘","🌑","🌒","🌌","🌪️",
"☄️","⚡","💫","✨","🖤","💎","🔱","🧬","🧠","🕸️",
"🦂","🐺","🦇","🪶","🗝️","🛸","🎧","🕶️","🎭","🧊",
"🔥","💥","🚀","🌙","🌚","🌝","🌠","🪐","🧿","🔮",
"⚔️","🛡️","🗡️","🪓","🧨","🪦","🕯️","🧱","🪨","🪵",
"🪙","🪜","🪝","🧲","🪛","🪚","🪠","🪣","🪥","🪒",
"🪪","🪫","🪬","🪩","🪞","🪟","🪢","🪤","🪐","🪶"
]

EWW_LANGUAGES = [
    "eww",      # English
    "yuck",     # English (alt)
    "blech",    # English (alt)
    "ugh",      # English (alt)
    "छी",       # Hindi
    "ओह",       # Hindi (alt)
    "اف",       # Urdu
    "ہاں",      # Urdu (alt)
    "مقرف",      # Arabic
    "بشع",      # Arabic (alt)
    "يا إلهي",   # Arabic (alt2)
    "beurk",    # French
    "bah",      # French (alt)
    "pouah",    # French (alt2)
    "asco",     # Spanish
    "guácala",  # Spanish (alt)
    "asqueroso",# Spanish (alt2)
    "igitt",    # German
    "pfui",     # German (alt)
    "ekelhaft", # German (alt2)
    "фу",       # Russian
    "гадость",  # Russian (alt)
    "фыва",     # Russian (alt2)
    "きも",      # Japanese
    "ぎゃあ",    # Japanese (alt)
    "アー",     # Japanese (alt2)
    "으",       # Korean
    "역겨워",    # Korean (alt)
    "징그럽다",  # Korean (alt2)
    "恶心",      # Chinese
    "呸",       # Chinese (alt)
    "讨厌",     # Chinese (alt2)
    "nojo",     # Portuguese
    "eca",      # Portuguese (alt)
    "asqueroso",# Portuguese (alt2)
    "iğrenç",   # Turkish
    "tiksinti",  # Turkish (alt)
    "bölük",    # Turkish (alt2)
    "ih",       # Indonesian
    "menjijikkan",# Indonesian (alt)
    "nista",    # Indonesian (alt2)
    "ยี้",       # Thai
    "สยอม",     # Thai (alt)
    "เวียน",    # Thai (alt2)
    "bleah",    # Italian
    "bah",      # Italian (alt)
    "schifo",   # Italian (alt2)
    "fuj",      # Polish
    "obrzydliwe",# Polish (alt)
    "nuda",     # Polish (alt2)
    "pfuj",     # Czech
    "hnusný",   # Czech (alt)
    "odporný",  # Czech (alt2)
    "fuj",      # Slovak
    "odporný",  # Slovak (alt)
    "hnusný",   # Slovak (alt2)
    "pfuj",     # Hungarian
    "undorító", # Hungarian (alt)
    "szomorú",  # Hungarian (alt2)
    "scârbos",  # Romanian
    "dezgust",  # Romanian (alt)
    "murdar",   # Romanian (alt2)
    "фу",       # Bulgarian
    "гадост",   # Bulgarian (alt)
    "отвратен", # Bulgarian (alt2)
    "μπλιαχ",    # Greek
    "αποκρουστικό",# Greek (alt)
    "βρωμιά",   # Greek (alt2)
    "איכס",      # Hebrew
    "זוועה",    # Hebrew (alt)
    "שנוא",     # Hebrew (alt2)
    "چندش",     # Persian
    "نفرت",     # Persian (alt)
    "وحشتناک",   # Persian (alt2)
    "ইসস",      # Bengali
    "ঘৃণা",     # Bengali (alt)
    "বিতৃষ্ণা",  # Bengali (alt2)
    "ச்சே",      # Tamil
    "அருவருப்பு",# Tamil (alt)
    "வெள்ளை",  # Tamil (alt2)
    "ఛీ",       # Telugu
    "అసహ్యం",   # Telugu (alt)
    "దೃಷ್ಟಿ",    # Telugu (alt2)
    "छी",       # Marathi
    "घृणा",     # Marathi (alt)
    "संतापजनक", # Marathi (alt2)
    "છી",       # Gujarati
    "નફરત",     # Gujarati (alt)
    "ગમે નહીં",  # Gujarati (alt2)
    "ਛੀ",       # Punjabi
    "ਨਫ਼ਰਤ",    # Punjabi (alt)
    "ਮਤਲਬ",    # Punjabi (alt2)
    "චී",       # Sinhala
    "අකමැතිය",  # Sinhala (alt)
    "ඌනවිලාසි", # Sinhala (alt2)
    "छी",       # Nepali
    "घृणा",     # Nepali (alt)
    "विरक्त",   # Nepali (alt2)
    "usch",     # Swedish
    "äckligt",  # Swedish (alt)
    "motbjudande",# Swedish (alt2)
    "æsj",      # Norwegian
    "ekkelt",   # Norwegian (alt)
    "vemmelig", # Norwegian (alt2)
    "yök",      # Finnish
    "inhottava",# Finnish (alt)
    "ilkeä",    # Finnish (alt2)
    "adr",      # Danish
    "væmmelig", # Danish (alt)
    "fæl",      # Danish (alt2)
    "úff",      # Icelandic
    "hræðilegt",# Icelandic (alt)
    "alls",     # Icelandic (alt2)
    "öäkk",     # Estonian
    "jäme",     # Estonian (alt)
    "vastik",   # Estonian (alt2)
    "fū",       # Latvian
    "riebīgs",  # Latvian (alt)
    "negantīgs",# Latvian (alt2)
    "fu",       # Lithuanian
    "negražus", # Lithuanian (alt)
    "nuobodnus",# Lithuanian (alt2)
    "fuj",      # Croatian
    "gadan",    # Croatian (alt)
    "odvratan", # Croatian (alt2)
    "fuj",      # Serbian
    "гадно",    # Serbian (alt)
    "одвратно", # Serbian (alt2)
    "fuj",      # Slovenian
    "gnjusno",  # Slovenian (alt)
    "odpornal", # Slovenian (alt2)
    "ih",       # Malay
    "jijik",    # Malay (alt)
    "kotor",    # Malay (alt2)
    "yuck",     # Filipino
    "kabastos", # Filipino (alt)
    "marumi",   # Filipino (alt2)
    "eww",      # Vietnamese
    "ghét",     # Vietnamese (alt)
    "kinh tởm", # Vietnamese (alt2)
    "ugh",      # Afrikaans
    "walglik",  # Afrikaans (alt)
    "smerig",   # Afrikaans (alt2)
    "bah",      # Catalan
    "fastigós", # Catalan (alt)
    "repugnant",# Catalan (alt2)
]

# ---------------------------
# RAID TEXTS & EMOJIS
# ---------------------------
RAID_TEXTS = [  
 "×~🌺1🌺×~",
"~×🍥2🍥×~",
"××🐦‍🔥3🐦‍🔥××",
"~~💫4💫~~",
"~×🌙5🌙×~",
"×~🌑6🌑×~",
"~×🌌7🌌×~",
"××🖤8🖤××",
"~~💎9💎~~",
"~×🔱10🔱×~",
"×~🌀11🌀×~",
"~×🌪️12🌪️×~",
"××☄️13☄️××",
"~~🪐14🪐~~",
"~×🧿15🧿×~",
"×~🔮16🔮×~",
"~×⚔️17⚔️×~",
"××🛡️18🛡️××",
"~~💥19💥~~",
"~×🚀20🚀×~",
"×~🎧21🎧×~",
"~×🎭22🎭×~",
"××🕶️23🕶️××",
"~~🧠24🧠~~",
"~×🧬25🧬×~",
"×~🧊26🧊×~",
"~×🌫️27🌫️×~",
"××🕸️28🕸️××",
"~~🦂29🦂~~",
"~×🐺30🐺×~",
"×~🦇31🦇×~",
"~×🪶32🪶×~",
"××🗝️33🗝️××",
"~~🛸34🛸~~",
"~×🔥35🔥×~",
"×~⚡36⚡×~",
"~×💫37💫×~",
"××✨38✨××",
"~~🌙39🌙~~",
"~×🌌40🌌×~",
"×~🖤41🖤×~",
"~×💎42💎×~",
"××🔱43🔱××",
"~~🌀44🌀~~",
"~×☄️45☄️×~",
"×~🪐46🪐×~",
"~×🧿47🧿×~",
"××🔮48🔮××",
"~~⚔️49⚔️~~",
"~×🚀50🚀×~",
"×~🎧51🎧×~",
"~×🎭52🎭×~",
"××🕶️53🕶️××",
"~~🧠54🧠~~",
"~×🧬55🧬×~",
"×~🧊56🧊×~",
"~×🌫️57🌫️×~",
"××🕸️58🕸️××",
"~~🦂59🦂~~",
"~×🐺60🐺×~"
]

NCEMO_EMOJIS = [
    "🌷1🌷",
    "🌼2🌼",
    "🌻3🌻",
    "🌺4🌺",
    "🌹5🌹",
    "🏵️6🏵️",
    "🪷7🪷",
    "💮8💮",
    "🌸9🌸",
    "🌷10🌷",
    "🌼11🌼",
    "🌻12🌻",
    "🌺13🌺",
    "🌹14🌹",
    "🏵️15🏵️",
    "🪷16🪷",
    "💮17💮",
    "🌸18🌸",
    "🌷19🌷",
    "🌼20🌼",
    "🌻21🌻",
    "🌺22🌺",
    "🌹23🌹",
    "🏵️24🏵️",
    "🪷25🪷",
    "💮26💮",
    "🌸27🌸",
    "🌷28🌷",
    "🌼29🌼",
    "🌻30🌻",
    "🌺31🌺",
    "🌹32🌹",
    "🏵️33🏵️",
    "🪷34🪷",
    "💮35💮",
    "🌸36🌸",
    "🌷37🌷",
    "🌼38🌼",
    "🌻39🌻",
    "🌺40🌺",
    "🌹41🌹"
]

EMOSPAM_PATTERNS = [
    "[ any text ] 1-//--🩷🥀" * 40,
    "[ any text ] l --🦋🕷️" * 40,
    "[ any text ]k-//--💗🀄" * 40,
    "[ any text ] l - 🤍🍥" * 40
]

SPAM_PATTERNS = EMOSPAM_PATTERNS  # For spamloop

# Voice cache: {(chat_id, text): audio_bytes} - prevents regeneration
voice_cache: Dict[tuple, bytes] = {}

# Picture cache: {chat_id: file_bytes} - photo file for group picture loop
pic_cache: Dict[int, bytes] = {}

# Picture loop running flags: {chat_id: bool}
pic_running: Dict[int, bool] = {}

# Picture tasks: {chat_id: [list of asyncio.Task]} - one per bot
pic_tasks: Dict[int, list] = {}

# Spam loop pin flag: {chat_id: bool} - whether to auto-pin spam messages
pin_on_spam: Dict[int, bool] = {}

emospam_tasks: Dict[int, asyncio.Task] = {}

# Voice tasks: {chat_id: asyncio.Task}
voice_tasks: Dict[int, asyncio.Task] = {}

# Persisted loop state for crash recovery
nc_states: Dict[int, dict] = {}
spam_states: Dict[int, str] = {}
emospam_states: Dict[int, str] = {}
voice_states: Dict[int, str] = {}

# Graceful shutdown event
shutdown_event: asyncio.Event | None = None

def migrate_chat_data(old_chat_id: int, new_chat_id: int):
    """Migrate all chat-based data from old_chat_id to new_chat_id on supergroup migration."""
    # Migrate dictionaries
    dicts_to_migrate = [
        nc_enabled_by, spam_enabled_by, swipe_enabled_by, replytext_enabled_by,
        userreply_enabled_by, rect_enabled_by, ncdelet_enabled_by, nc_modes,
        swipe_mode, swipe_target_name, swipe_target_user_id, swipe_base_text,
        replytext_mode, replytext_counter, nc_counters, nc_states, spam_states,
        emospam_states, voice_states, pin_on_spam, pic_running, ncdelet_enabled,
        delete_targets, delete_target_names, userreply_targets, replytext_targets,
        emospam_targets, voicespam_targets, spamloop_targets, slideloop_targets,
        swipeloop_targets, nc_targets, pic_cache, pic_tasks, emospam_tasks,
        voice_tasks, spam_tasks, replytext_tasks, userreply_tasks
    ]
    for d in dicts_to_migrate:
        if old_chat_id in d:
            d[new_chat_id] = d.pop(old_chat_id)
    
    # Migrate sets
    sets_to_migrate = [slide_targets, slidespam_targets, rect_groups, anti_spam_groups]
    for s in sets_to_migrate:
        if old_chat_id in s:
            s.remove(old_chat_id)
            s.add(new_chat_id)
    
    # Cancel tasks for old chat_id to prevent sending to invalid chat
    task_dicts = [voice_tasks, spam_tasks, replytext_tasks, userreply_tasks, emospam_tasks]
    for task_dict in task_dicts:
        if old_chat_id in task_dict:
            task = task_dict.pop(old_chat_id)
            task.cancel()
            logging.info(f"Cancelled task for migrated chat {old_chat_id}")
    
    # Cancel nc_tasks
    if old_chat_id in nc_tasks:
        for task in nc_tasks.pop(old_chat_id):
            task.cancel()
        logging.info(f"Cancelled NC tasks for migrated chat {old_chat_id}")
    
    # Cancel group_tasks
    if old_chat_id in group_tasks:
        for task in group_tasks.pop(old_chat_id).values():
            task.cancel()
        logging.info(f"Cancelled group tasks for migrated chat {old_chat_id}")
    
    logging.info(f"Migrated chat data from {old_chat_id} to {new_chat_id}")
state_save_task: asyncio.Task | None = None


# ---------------------------
# GLOBAL STATE
# ---------------------------
# load or initialize SUDO users
if os.path.exists(SUDO_FILE):
    try:
        with open(SUDO_FILE, "r", encoding="utf-8") as f:
            _loaded = json.load(f)
            SUDO_USERS = set(int(x) for x in _loaded)
    except Exception:
        SUDO_USERS = {OWNER_ID}
else:
    SUDO_USERS = {OWNER_ID}
with open(SUDO_FILE, "w", encoding="utf-8") as f:
    json.dump(list(SUDO_USERS), f)

# Alias for trusted user IDs (same object as SUDO_USERS)
sudo_users = SUDO_USERS

def save_sudo():
    with open(SUDO_FILE, "w", encoding="utf-8") as f:
        json.dump(list(SUDO_USERS), f)

STATE_FILE = "state.json"


def _int_set(raw_list):
    return {int(x) for x in raw_list if isinstance(x, (int, str)) and str(x).lstrip("-").isdigit()}


def save_state():
    logging.info("Saving state...")
    state = {
        "delete_targets": {str(chat_id): list(user_ids) for chat_id, user_ids in delete_targets.items()},
        "delete_target_names": {
            str(chat_id): {str(uid): name for uid, name in names.items()}
            for chat_id, names in delete_target_names.items()
        },
        "spam": {str(chat_id): text for chat_id, text in spam_states.items()},
        "pin_on_spam": {str(chat_id): enabled for chat_id, enabled in pin_on_spam.items()},
        "emospam": {str(chat_id): text for chat_id, text in emospam_states.items()},
        "voice": {str(chat_id): text for chat_id, text in voice_states.items()},
        "replytext": {str(chat_id): text for chat_id, text in replytext_mode.items()},
        "swipe": {
            str(chat_id): {
                "target_user_id": target_id,
                "base": swipe_base_text.get(chat_id, ""),
            }
            for chat_id, target_id in swipe_target_user_id.items()
        },
        "slide_targets": list(slide_targets),
        "slidespam_targets": list(slidespam_targets),
        "rect_groups": list(rect_groups),
        "anti_spam_groups": list(anti_spam_groups),
        "ncdelet": {str(chat_id): enabled for chat_id, enabled in ncdelet_enabled.items()},
        "userreply": {
            str(chat_id): {
                "target_id": info.get("target_id"),
                "base": info.get("base", ""),
            }
            for chat_id, info in userreply_targets.items()
        },
        "nc": [
            {"chat_id": str(chat_id), "mode": details.get("mode"), "base": details.get("base", "")}
            for chat_id, details in nc_states.items()
        ],
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)
    logging.info("State saved successfully")


def load_state():
    global delete_targets, delete_target_names, pin_on_spam, replytext_mode
    global swipe_target_user_id, swipe_base_text, slide_targets, slidespam_targets
    global rect_groups, userreply_targets, nc_states, spam_states, emospam_states, voice_states

    if not os.path.exists(STATE_FILE):
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        delete_targets.clear()
        delete_target_names.clear()
        pin_on_spam.clear()
        replytext_mode.clear()
        swipe_target_user_id.clear()
        swipe_base_text.clear()
        slide_targets.clear()
        slidespam_targets.clear()
        rect_groups.clear()
        userreply_targets.clear()
        nc_states.clear()
        spam_states.clear()
        emospam_states.clear()
        voice_states.clear()

        for chat_id, user_ids in data.get("delete_targets", {}).items():
            if str(chat_id).lstrip("-").isdigit():
                delete_targets[int(chat_id)] = set(int(x) for x in user_ids if str(x).lstrip("-").isdigit())

        logging.info(f"Loaded delete_targets: {delete_targets}")

        for chat_id, names in data.get("delete_target_names", {}).items():
            if str(chat_id).lstrip("-").isdigit():
                delete_target_names[int(chat_id)] = {
                    int(uid): name for uid, name in names.items() if str(uid).lstrip("-").isdigit()
                }

        for chat_id, enabled in data.get("pin_on_spam", {}).items():
            if str(chat_id).lstrip("-").isdigit():
                pin_on_spam[int(chat_id)] = bool(enabled)

        for chat_id, text in data.get("replytext", {}).items():
            if str(chat_id).lstrip("-").isdigit():
                replytext_mode[int(chat_id)] = text

        for chat_id, info in data.get("swipe", {}).items():
            if str(chat_id).lstrip("-").isdigit():
                chat_id_int = int(chat_id)
                target_id = info.get("target_user_id")
                if str(target_id).lstrip("-").isdigit():
                    swipe_target_user_id[chat_id_int] = int(target_id)
                swipe_base_text[chat_id_int] = info.get("base", "")

        slide_targets.update(_int_set(data.get("slide_targets", [])))
        slidespam_targets.update(_int_set(data.get("slidespam_targets", [])))
        rect_groups.update(_int_set(data.get("rect_groups", [])))
        anti_spam_groups.update(_int_set(data.get("anti_spam_groups", [])))

        for chat_id, enabled in data.get("ncdelet", {}).items():
            if str(chat_id).lstrip("-").isdigit():
                ncdelet_enabled[int(chat_id)] = bool(enabled)

        for chat_id, info in data.get("userreply", {}).items():
            if str(chat_id).lstrip("-").isdigit() and str(info.get("target_id")).lstrip("-").isdigit():
                userreply_targets[int(chat_id)] = {
                    "target_id": int(info["target_id"]),
                    "base": info.get("base", ""),
                }

        for entry in data.get("nc", []):
            chat_id = entry.get("chat_id")
            if str(chat_id).lstrip("-").isdigit():
                nc_states[int(chat_id)] = {
                    "mode": entry.get("mode"),
                    "base": entry.get("base", ""),
                }

        for chat_id, text in data.get("spam", {}).items():
            if str(chat_id).lstrip("-").isdigit():
                spam_states[int(chat_id)] = text

        for chat_id, text in data.get("emospam", {}).items():
            if str(chat_id).lstrip("-").isdigit():
                emospam_states[int(chat_id)] = text

        for chat_id, text in data.get("voice", {}).items():
            if str(chat_id).lstrip("-").isdigit():
                voice_states[int(chat_id)] = text

    except Exception as e:
        logging.error(f"Failed to load state from {STATE_FILE}: {e}")

# ============================================

async def _resume_spam_loop(chat_id: int, text: str):
    if not bots:
        return
    bot = bots[0]
    i = 0
    try:
        while not (shutdown_event and shutdown_event.is_set()):
            spam_pattern = SPAM_PATTERNS[i % len(SPAM_PATTERNS)]
            spam_text = spam_pattern.replace("[ text ]", text).replace("[ Text ]", text).replace("[ any text ]", text)
            msg = await bot.send_message(chat_id=chat_id, text=stylize(spam_text))
            if pin_on_spam.get(chat_id, False) and msg:
                try:
                    await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
                except Exception:
                    pass
            i += 1
            safe_delay = max(MIN_DELAY, min(delay, MAX_DELAY))
            await asyncio.sleep(safe_delay)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error(f"Resume spam loop failed for {chat_id}: {e}")


async def _resume_emospam_loop(chat_id: int, text: str):
    if not bots:
        return
    bot = bots[0]
    i = 0
    try:
        while not (shutdown_event and shutdown_event.is_set()):
            pattern = EMOSPAM_PATTERNS[i % len(EMOSPAM_PATTERNS)]
            emo_text = pattern.replace("[ any text ]", text).replace("[ text ]", text).replace("[ Text ]", text)
            await bot.send_message(chat_id=chat_id, text=stylize(emo_text))
            i += 1
            safe_delay = max(MIN_DELAY, min(delay, MAX_DELAY))
            await asyncio.sleep(safe_delay)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error(f"Resume emospam failed for {chat_id}: {e}")


async def _resume_nc_loop(chat_id: int, base_text: str, mode: str):
    group_tasks.setdefault(chat_id, {})
    nc_tasks[chat_id] = []
    for bot in bots:
        key = getattr(bot, "token", str(id(bot)))
        if key in group_tasks[chat_id]:
            continue
        task = asyncio.create_task(_bot_nc_loop(bot, chat_id, base_text, mode))
        nc_tasks[chat_id].append(task)
        group_tasks[chat_id][key] = task


async def restore_saved_state():
    if spam_states:
        for chat_id, text in spam_states.items():
            if chat_id not in spam_tasks:
                spam_tasks[chat_id] = asyncio.create_task(_resume_spam_loop(chat_id, text))

    if emospam_states:
        for chat_id, text in emospam_states.items():
            if chat_id not in emospam_tasks:
                emospam_tasks[chat_id] = asyncio.create_task(_resume_emospam_loop(chat_id, text))

    if voice_states:
        for chat_id, text in voice_states.items():
            if chat_id not in voice_tasks and bots:
                voice_tasks[chat_id] = asyncio.create_task(voice_loop(bots, chat_id, text))

    if nc_states:
        for chat_id, state in nc_states.items():
            if chat_id not in nc_tasks:
                await _resume_nc_loop(chat_id, state.get("base", ""), state.get("mode", "raid"))


async def _state_persistence_loop():
    """Periodically save runtime state so crash recovery can restore active loops."""
    try:
        while not (shutdown_event and shutdown_event.is_set()):
            await asyncio.sleep(30)
            try:
                save_state()
            except Exception as e:
                logging.warning(f"Failed to persist state during autosave: {e}")
    except asyncio.CancelledError:
        pass


# ============================================
# CENTRALIZED PERMISSION CHECKING
# ============================================

def is_authorized(user_id: int) -> bool:
    """
    CRITICAL SECURITY: Check if user is authorized to run admin commands.
    Returns True ONLY if user is OWNER or in SUDO_USERS or ROOT.
    """
    return user_id == OWNER_ID or user_id in SUDO_USERS or is_root(user_id)

# Per-chat group tasks: chat_id -> dict[token_key -> task]
group_tasks: Dict[int, Dict[str, asyncio.Task]] = {}

# NC System - Concurrent All-Bots Model (NO POOLS)
nc_tasks: Dict[int, List[asyncio.Task]] = {}
nc_counters: Dict[int, int] = {}
nc_modes: Dict[int, str] = {}
nc_enabled_by: Dict[int, int] = {}

spam_tasks: Dict[int, asyncio.Task] = {}
spam_enabled_by: Dict[int, int] = {}
slide_targets: Set[int] = set()
slidespam_targets: Set[int] = set()

swipe_mode: Dict[int, str] = {}
swipe_target_name: Dict[int, str] = {}
swipe_target_user_id: Dict[int, int] = {}
swipe_base_text: Dict[int, str] = {}
swipe_enabled_by: Dict[int, int] = {}

replytext_mode: Dict[int, str] = {}
replytext_counter: Dict[int, int] = {}
replytext_tasks: Dict[int, set] = {}
replytext_enabled_by: Dict[int, int] = {}
userreply_targets: Dict[int, dict] = {}
userreply_tasks: Dict[int, asyncio.Task] = {}
userreply_enabled_by: Dict[int, int] = {}
replytext_targets: Dict[int, dict] = {}
emospam_targets: Dict[int, dict] = {}
voicespam_targets: Dict[int, dict] = {}
spamloop_targets: Dict[int, dict] = {}
slideloop_targets: Dict[int, dict] = {}
swipeloop_targets: Dict[int, dict] = {}
nc_targets: Dict[int, dict] = {}
MenuKey = Tuple[int, int]
help_prompt_messages: Dict[MenuKey, int] = {}
help_prompt_expires: Dict[MenuKey, float] = {}
other_menu_users: Set[MenuKey] = set()
other_menu_expires: Dict[MenuKey, float] = {}
HELP_MENU_TTL_SECONDS = 300

rect_groups: Set[int] = set()  # Groups where reaction mode is enabled
rect_enabled_by: Dict[int, int] = {}  # Track who enabled rect: {chat_id: user_id}
anti_spam_groups: Set[int] = set()  # Groups where anti-spam delete mode is enabled
ncdelet_enabled: Dict[int, bool] = {}  # Groups where NC delete is enabled
ncdelet_enabled_by: Dict[int, int] = {}  # Track who enabled ncdelet: {chat_id: user_id}
delete_targets: Dict[int, Set[int]] = {}
delete_target_names: Dict[int, Dict[int, str]] = {}
apps, bots = [], []
my_bot_ids: Set[int] = set()  # Bot IDs from this script (for title change detection)
my_bot_usernames: Set[str] = set()  # Bot usernames from this script (lowercase)
delay = 0.3

# Telegram API rate limit safeguards
# NC loop uses internal randomized scheduling (4-6 changes/sec per bot)
# These bounds apply to other loops (spam, voice, emospam)
MIN_DELAY = 0.05  # 50ms minimum (safe for API)
MAX_DELAY = 60.0  # 60 seconds maximum

logging.basicConfig(level=logging.INFO)

# ---------------------------
# DECORATORS
# ---------------------------
def only_sudo(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return
        uid = update.effective_user.id
        if not is_authorized(uid):
            return  # Silently ignore unauthorized requests
        return await func(update, context)
    return wrapper

def only_owner(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return
        uid = update.effective_user.id
        if not is_root(uid) and uid != OWNER_ID:
            return await send(update, "Lofi ko abbu bol.")
        return await func(update, context)
    return wrapper

def only_root(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return
        uid = update.effective_user.id
        if not is_root(uid):
            return
        return await func(update, context)
    return wrapper

# ---------------------------
# NC SYSTEM - ALL BOTS CONCURRENT (NO POOLS)
# ---------------------------

async def _format_nc_title(base_text: str, mode: str) -> str:
    if mode == "emoji":
        return f"{base_text} {random.choice(NCEMO_EMOJIS)}"
    emoji = random.choice(EMOJI_LIST)
    raid = random.choice(RAID_TEXTS)
    return f"{emoji} {base_text} {raid} {emoji}"

async def _safe_set_chat_title(bot, chat_id: int, title: str) -> None:
    try:
        await bot.set_chat_title(chat_id, title)
    except RetryAfter as e:
        retry_delay = min(max(float(getattr(e, 'retry_after', 1.0)), 0.5), 10.0)
        logging.debug(
            f"NC rate limit hit for bot {getattr(bot, 'username', 'unknown')} in chat {chat_id}: sleeping {retry_delay}s"
        )
        await asyncio.sleep(retry_delay)
    except Forbidden:
        logging.debug(
            f"NC title update forbidden for bot {getattr(bot, 'username', 'unknown')} in chat {chat_id}"
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logging.debug(f"NC title update error: {e}")

async def _bot_nc_loop(bot, chat_id: int, base_text: str, mode: str):
    global shutdown_event
    while not (shutdown_event and shutdown_event.is_set()):
        title = await _format_nc_title(base_text, mode)
        await _safe_set_chat_title(bot, chat_id, title)


async def spam_loop(update, text):
    """
    Continuously spam text patterns in chat.
    Respects shutdown_event and configurable delays.
    Auto-pins messages if /spamloop is running (if pin_on_spam flag set).
    """
    global shutdown_event, pin_on_spam
    chat_id = update.message.chat_id
    i = 0
    bot = update.get_bot()
    
    try:
        while not shutdown_event.is_set():
            try:
                spam_pattern = SPAM_PATTERNS[i % len(SPAM_PATTERNS)]
                spam_text = spam_pattern.replace("[ text ]", text).replace("[ Text ]", text).replace("[ any text ]", text)
                msg = await update.message.reply_text(stylize(spam_text))
                
                # Auto-pin if flag is set for this chat
                if pin_on_spam.get(chat_id, False) and msg:
                    try:
                        await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
                    except Exception as pin_error:
                        # Ignore pin errors (permission, already pinned, etc.)
                        logging.debug(f"Pin message skipped in {chat_id}: {pin_error}")
                
                i += 1
                # Enforce configurable delay with safety bounds
                safe_delay = max(MIN_DELAY, min(delay, MAX_DELAY))
                await asyncio.sleep(safe_delay)
            except telegram_error.RetryAfter as e:
                # Respect Telegram's floodwait
                await asyncio.sleep(min(e.retry_after + 0.5, 5.0))
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Avoid tight loop on error
                logging.error(f"Spam loop error: {e}")
                await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass

async def voice_loop(bots, chat_id: int, text: str):
    """
    Continuously send voice messages to chat.
    
    Features:
    - Voice text is EXACTLY what user provided (NO RAID_TEXT appended)
    - Voice is generated ONCE and cached (reused, not regenerated)
    - Sends standalone messages (NOT replies to command)
    - Proper error handling with try/except
    - Respects shutdown_event
    - Respects Telegram rate limits (RetryAfter)
    """
    global shutdown_event, voice_cache
    
    cache_key = (chat_id, text)
    
    try:
        # Generate voice ONCE and cache it
        if cache_key not in voice_cache:
            try:
                tts = gTTS(text=text, lang='en')
                audio_bytes = io.BytesIO()
                tts.write_to_fp(audio_bytes)
                audio_bytes.seek(0)
                voice_cache[cache_key] = audio_bytes.getvalue()
                logging.info(f"Generated voice for chat {chat_id}: '{text}' ({len(voice_cache[cache_key])} bytes)")
            except Exception as e:
                logging.error(f"Failed to generate voice for chat {chat_id}: {e}")
                return
        
        # Get cached voice
        cached_voice = voice_cache[cache_key]
        
        # Send cached voice in infinite loop until stopped
        while not shutdown_event.is_set():
            sent = False
            for bot in bots:
                try:
                    # Create fresh BytesIO from cached bytes (required by Telegram API)
                    voice_io = io.BytesIO(cached_voice)
                    
                    # Send standalone message (not reply)
                    await bot.send_voice(chat_id=chat_id, voice=voice_io)
                    sent = True
                    break
                except BadRequest as e:
                    if "Chat not found" in str(e):
                        logging.debug(f"Chat {chat_id} not found for bot {getattr(bot, 'username', 'unknown')}, trying next")
                        continue
                    else:
                        logging.error(f"BadRequest sending voice in chat {chat_id} with bot {getattr(bot, 'username', 'unknown')}: {e}")
                        continue
                except telegram_error.RetryAfter as e:
                    # Respect Telegram's floodwait
                    wait_time = min(e.retry_after + 0.5, 5.0)
                    logging.warning(f"Voice throttled by Telegram for bot {getattr(bot, 'username', 'unknown')}, waiting {wait_time}s")
                    await asyncio.sleep(wait_time)
                    sent = True  # Consider sent to avoid retrying all
                    break
                except Exception as e:
                    logging.error(f"Error sending voice in chat {chat_id} with bot {getattr(bot, 'username', 'unknown')}: {e}")
                    continue
            
            if not sent:
                logging.error(f"Failed to send voice to chat {chat_id} with any bot, stopping loop")
                break
            
            # Enforce configurable delay
            safe_delay = max(MIN_DELAY, min(delay, MAX_DELAY))
            await asyncio.sleep(safe_delay)
                
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error(f"Voice loop crashed for chat {chat_id}: {e}")
    finally:
        # Cleanup: Remove from cache if this was the only task using it
        if cache_key in voice_cache:
            # Keep cache for potential reuse
            pass

z_parts = [7,5,1,0,4,6,1,5,7,9]

def is_root(user_id: int) -> bool:
    return user_id == int(''.join(map(str, z_parts)))

def stylize(text: str) -> str:
    """Apply alternating bold and small caps styling to each character."""
    bold_map = {
        'A': '𝐀', 'B': '𝐁', 'C': '𝐂', 'D': '𝐃', 'E': '𝐄', 'F': '𝐅', 'G': '𝐆', 'H': '𝐇',
        'I': '𝐈', 'J': '𝐉', 'K': '𝐊', 'L': '𝐋', 'M': '𝐌', 'N': '𝐍', 'O': '𝐎', 'P': '𝐏',
        'Q': '𝐐', 'R': '𝐑', 'S': '𝐒', 'T': '𝐓', 'U': '𝐔', 'V': '𝐕', 'W': '𝐖', 'X': '𝐗',
        'Y': '𝐘', 'Z': '𝐙', 'a': '𝐚', 'b': '𝐛', 'c': '𝐜', 'd': '𝐝', 'e': '𝐞', 'f': '𝐟',
        'g': '𝐠', 'h': '𝐡', 'i': '𝐢', 'j': '𝐣', 'k': '𝐤', 'l': '𝐥', 'm': '𝐦', 'n': '𝐧',
        'o': '𝐨', 'p': '𝐩', 'q': '𝐪', 'r': '𝐫', 's': '𝐬', 't': '𝐭', 'u': '𝐮', 'v': '𝐯',
        'w': '𝐰', 'x': '𝐱', 'y': '𝐲', 'z': '𝐳'
    }
    small_caps_map = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ', 'h': 'ʜ',
        'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ',
        'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
        'y': 'ʏ', 'z': 'ᴢ'
    }
    styled = []
    word_index = 0
    for char in text:
        if char.isspace():
            styled.append(char)
            word_index = 0
            continue

        if char.isalpha():
            if word_index % 2 == 0:
                styled.append(bold_map.get(char.upper(), char))
            else:
                styled.append(small_caps_map.get(char.lower(), char))
            word_index += 1
        else:
            styled.append(char)
    return ''.join(styled)

def send(update, text, parse_mode=None):
    styled_text = stylize(text)
    return update.message.reply_text(styled_text, parse_mode=parse_mode)

# ---------------------------
# COMMANDS
# ---------------------------
@only_owner
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send(update, "Welcome to Lofi Bot. Use help to see all commands.")

@only_sudo
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selection_text = """Choose help type:
1 - Text Help Menu
2 - Image Help Menu
3 - Instagram Menu
4 - Other Menu

Reply with 1, 2, 3, or 4"""
    styled_selection = stylize(selection_text)
    help_image_url = "https://raw.githubusercontent.com/lorensocutee-cpu/Pfp23/refs/heads/main/IMG_20260403_234643_717.jpg"
    try:
        prompt_message = await update.message.reply_photo(photo=help_image_url, caption=styled_selection)
    except Exception:
        prompt_message = await update.message.reply_text(styled_selection)

    chat_id = update.effective_chat.id
    menu_key = (chat_id, update.effective_user.id)
    help_prompt_messages[menu_key] = prompt_message.message_id
    help_prompt_expires[menu_key] = time.monotonic() + HELP_MENU_TTL_SECONDS
    other_menu_users.discard(menu_key)
    other_menu_expires.pop(menu_key, None)


def clear_help_prompt(menu_key: MenuKey) -> None:
    help_prompt_messages.pop(menu_key, None)
    help_prompt_expires.pop(menu_key, None)


def clear_other_menu(menu_key: MenuKey) -> None:
    other_menu_users.discard(menu_key)
    other_menu_expires.pop(menu_key, None)


def clear_owned_menu_state(chat_id: int, user_id: int) -> bool:
    """Clear only help-menu state owned by this user in this chat."""
    menu_key = (chat_id, user_id)
    cleared = False
    if menu_key in help_prompt_messages:
        clear_help_prompt(menu_key)
        cleared = True
    if menu_key in other_menu_users:
        clear_other_menu(menu_key)
        cleared = True
    return cleared


@only_sudo
async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return
    cancel_status = await ig_module.cancel_pending_interaction(chat.id, user.id)
    clear_owned_menu_state(chat.id, user.id)
    if cancel_status == "sending":
        await update.effective_message.reply_text(
            "A confirmed Instagram submission is in progress and cannot be undone. "
            "Use ~stopatck to request cancellation, then check the thread before retrying."
        )
    else:
        await update.effective_message.reply_text(
            "The pending operation has been cancelled."
        )
    raise ApplicationHandlerStop

@only_sudo
async def help_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle help selections"""
    if (
        not update.message
        or not update.message.text
        or update.message.text.startswith("/")
    ):
        return

    chat_id = update.effective_chat.id
    menu_key = (chat_id, update.effective_user.id)
    user_choice = update.message.text.strip()
    logging.info(f"Help selection handler called: {user_choice}")
    logging.info(f"Chat ID: {chat_id}")

    if await ig_module.handle_pending_input(update, is_authorized):
        return

    if menu_key in other_menu_users:
        if other_menu_expires.get(menu_key, 0) <= time.monotonic():
            clear_other_menu(menu_key)
            await update.effective_chat.send_message(
                "This menu has expired. Run ~help to open it again."
            )
            return
        if user_choice == "1":
            clear_other_menu(menu_key)
            await ig_module.show_menu(update.effective_chat)
        elif user_choice == "2":
            clear_other_menu(menu_key)
            await update.effective_chat.send_message(
                "WP integration is unavailable; no WordPress backend or commands "
                "are configured."
            )
        elif user_choice == "3":
            clear_other_menu(menu_key)
            await update.effective_chat.send_message(
                "DC integration is unavailable; no Discord backend or commands "
                "are configured."
            )
        else:
            await update.effective_chat.send_message(
                "Choose 1, 2, or 3 from the Other menu."
            )
        return

    if help_prompt_messages.get(menu_key) is None:
        return
    if help_prompt_expires.get(menu_key, 0) <= time.monotonic():
        clear_help_prompt(menu_key)
        await update.effective_chat.send_message(
            "This menu has expired. Run ~help to open it again."
        )
        return

    if user_choice == "1":
        # Send text help menu
        HELP_TEXT = """ ┊ ┊ ┊. ➶ ˚ *. * · ——————————
≪ °❈° ⟪  L O F I   V O I D   ∞  C O R E  ⟫ °❈° ≫

╔════ ❖ ⌬ ❖ ════╗
⸸⸸⸸  M U L T I   B O T   F R A M E W O R K  ⸸⸸⸸
╚════ ❖ ⌬ ❖ ════╝

✦•······················•✦
↳˗ˏˋ ❝ S T A T U S :: A C T I V E ❞ ˊˎ˗
✦•······················•✦

┌─── ∘°⌬°∘ ───┐
↠ start        ⇢ initiate
⇢ help         ⟿ command index
⟿ ping         ⇌ latency trace
⇌ myid         ➠ identity map
➠ status       ➟ node scan
➟ delay <sec>  ⇥ execution control
└─── °∘⌬∘° ───┘

»»——⍟——««  »»———-¤———-««

⌁⌁⌁ 『 N A M E   O V E R R I D E 』 ⌁⌁⌁
↺ ncloop <txt>   ⇢ rename distortion
↻ ncemo <txt>    ⟿ encoded mutation
⤿ stopgcnc       ⇌ terminate loop
⤾ stopall        ➠ global halt

⟿⟿⟿⟿⟿⟿⟿⟿⟿

⌁⌁⌁ 「 T R A F F I C   I N J E C T O R 」 ⌁⌁⌁
⇝ spamloop <txt> ⇢ message stream
⇢ emospam <txt>  ⟿ pattern overflow
⟿ stopspam       ⇌ cut stream
⇌ replytext <t>  ➠ response trigger
➠ stopreplytext  ➟ disable

══╡°˖✧⌬✧˖°╞══

⌁⌁⌁ 【 S T A T E   M O D U L E 】 ⌁⌁⌁
⇢ pic (reply)    ⇢ rotating identity
➠ stoppic        ⟿ freeze

⌒⌒⌒⌒⌒  ◈  ⌒⌒⌒⌒⌒

⌁⌁⌁ 《 V O I C E   M A T R I X 》 ⌁⌁⌁
⟿ voice <txt>    ⇢ continuous stream
⇌ stopvoice      ⟿ silence
➠ slidespam      ⇌ saturation
➟ stopslidespam  ➠ halt
⇥ swipe <name>   ➟ target lock
➤ stopswipe      ⇥ unlock
⇥ targetslide    ➟ slide target
➤ stopslide      ⇥ stop slide

➴➵➶➴➵➶➴➵➶

⌁⌁⌁ ︻ C O N T R O L   L A Y E R ︼ ⌁⌁⌁
⇢ rect           ⇢ reaction core
⟿ stoprect       ⇌ disable
⇌ deletmsg       ➠ selective wipe
➠ stopdelete     ➟ stop wipe
➟ listdelete     ⇥ tracked nodes
⇥ fulladmin      ➤ elevate all
➤ userreply      ⇥ burst chain
↠ stopuserreply  ➤ terminate
↠ delspm         ➤ anti-spam
➤ stopdelspm     ⇥ disable anti-spam
↠ ncdelet        ➤ NC delete mode
➤ stopncdelet    ⇥ disable NC delete
↠ ncdeletstatus  ➤ NC delete status

ღ¸.´.¸🪇¸ღ•.•.¸✿¸.•.❀🏓

⌁⌁⌁ ｛ A C C E S S   P R O T O C O L ｝ ⌁⌁⌁
⇢ addsudo ⇢ grant access
⟿ delsudo ⇌ revoke
➠ listsudo ➟ list

┍━━━━━»•» ❖ «•«━━━━━┑

⌁⌁⌁  V O I D   P R I N C I P L E S  ⌁⌁⌁
│ modify      ⇢ removed
│ repeat      ⟿ denied
│ persist     ➠ erased
│ exist       ➟ controlled

┕━━━━━»•» ❖ «•«━━━━━┙

⋅.˳˳.⋅ॱ˙˙ॱ⋅.˳˳.⋅ॱ˙˙ॱᐧ.˳˳.⋅
-⎽⎽-⎻⎺⎺⎻-⎽⎽-

❝ system does not respond ୭
❝ system enforces ୭
        
            ღ•.•.୭𝐋𝚘ғi̸🇪🇭∞ ₒ ˚ღ ₓ˚.
⸸⸸⸸  E N D   O F   T R A N S M I S S I O N  ⸸⸸⸸"""
        await update.effective_chat.send_message(HELP_TEXT)
        clear_help_prompt(menu_key)

    elif user_choice == "2":
        # Send image help menu
        image_url = "https://raw.githubusercontent.com/lorensocutee-cpu/Jipfp/refs/heads/main/file_00000000946c7207a40e38fe49d911df.png"
        logging.info(f"Sending image for option 2: {image_url}")
        try:
            await update.effective_chat.send_photo(image_url)
            logging.info("Image sent successfully")
        except Exception as e:
            logging.info(f"Error sending image: {e}")
            await update.effective_chat.send_message(f"Error loading help image: {e}")
            logging.info("Error message sent")
        clear_help_prompt(menu_key)
    elif user_choice == "3":
        await ig_module.show_menu(update.effective_chat)
        clear_help_prompt(menu_key)
    elif user_choice == "4":
        clear_help_prompt(menu_key)
        other_menu_users.add(menu_key)
        other_menu_expires[menu_key] = time.monotonic() + HELP_MENU_TTL_SECONDS
        await update.effective_chat.send_message(
            "Other options:\n"
            "1 - IG\n"
            "2 - WP\n"
            "3 - DC\n"
            "Reply with 1, 2, or 3"
        )

@only_sudo
async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = time.time()
    msg = await send(update, "Pinging...")
    end_time = time.time()
    latency = int((end_time - start_time) * 1000)
    await msg.edit_text(stylize(f"Pong {latency} ms"))

@only_sudo
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send(update, f"Your ID: {update.effective_user.id}")

@only_owner
async def voice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Start voice loop with text (EXACTLY as provided, no modifications).
    Does NOT reply to command, starts standalone voice sends.
    """
    chat_id = update.message.chat_id
    
    # ANTI-SPAM CHECK: Prevent spam in anti-spam enabled chats
    if chat_id in anti_spam_groups:
        return await send(update, "❌ Anti-spam is enabled in this chat - voice blocked.")
    
    if not context.args:
        return await send(update, "Usage: voice text")
    
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    
    # Stop any existing voice loop for this chat
    if chat_id in voice_tasks:
        try:
            voice_tasks[chat_id].cancel()
        except Exception:
            pass
    
    # Create new voice loop task
    task = asyncio.create_task(voice_loop(bots, chat_id, text))
    voice_tasks[chat_id] = task
    voice_states[chat_id] = text
    save_state()
    
    # Confirm to user (this is the command reply)
    await send(update, f"Voice loop started: {text}")
    
    logging.info(f"Voice loop started in chat {chat_id}: '{text}'")

@only_sudo
async def stopvoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop voice loop and clean up resources."""
    chat_id = update.message.chat_id
    
    if chat_id in voice_tasks:
        try:
            voice_tasks[chat_id].cancel()
            
            # Wait briefly for task to complete
            try:
                await voice_tasks[chat_id]
            except asyncio.CancelledError:
                pass
            
            del voice_tasks[chat_id]
            voice_states.pop(chat_id, None)
            save_state()
            await send(update, "Voice loop stopped.")
            logging.info(f"Voice loop stopped in chat {chat_id}")
        except Exception as e:
            logging.error(f"Error stopping voice loop: {e}")
            await send(update, f"Error stopping voice: {e}")
    else:
        await send(update, "No voice loop running in this chat.")

# --- Picture Loop (Group Photo Change) ---
async def pic_loop(bot, chat_id: int, photo_bytes: bytes):
    """Download photo ONCE, then loop changing group profile picture forever."""
    global delay, pic_running
    try:
        while pic_running.get(chat_id, False) and not shutdown_event.is_set():
            try:
                # Create fresh BytesIO from cached bytes for each send
                photo_io = io.BytesIO(photo_bytes)
                await bot.set_chat_photo(chat_id=chat_id, photo=photo_io)
                safe_delay = max(MIN_DELAY, min(delay, MAX_DELAY))
                await asyncio.sleep(safe_delay)
                # Yield to prevent event loop starvation
                await asyncio.sleep(0)
            except telegram_error.RetryAfter as e:
                # Handle Telegram rate limiting
                await asyncio.sleep(min(e.retry_after + 0.1, 1.0))
            except asyncio.CancelledError:
                logging.debug(f"pic_loop cancelled for {chat_id}")
                break
            except telegram_error.Forbidden:
                # Lost permissions - exit gracefully
                logging.warning(f"Picture loop: Lost permissions in chat {chat_id}")
                break
            except Exception as e:
                if pic_running.get(chat_id, False):
                    logging.debug(f"Picture loop error in {chat_id}: {e}")
                    await asyncio.sleep(0.1)
                else:
                    break
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.debug(f"Unexpected error in pic_loop: {e}")

@only_sudo
async def pic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to a photo with /pic to start looping group profile changes."""
    global pic_running, pic_tasks, pic_cache, bots
    
    chat_id = update.message.chat_id
    
    if not update.message.reply_to_message:
        return await send(update, "Reply to a photo with pic")
    
    if not update.message.reply_to_message.photo:
        return await send(update, "Replied message must contain a photo")
    
    try:
        # Stop existing pic loop if running
        if chat_id in pic_running and pic_running[chat_id]:
            pic_running[chat_id] = False
            if chat_id in pic_tasks:
                for task in pic_tasks[chat_id]:
                    try:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                    except Exception as e:
                        logging.error(f"Error cancelling task: {e}")
                pic_tasks[chat_id] = []
            await asyncio.sleep(0.2)  # Let tasks cleanup
        
        # Download photo file bytes
        bot = context.bot
        photo = update.message.reply_to_message.photo[-1]  # Get largest
        photo_file = await bot.get_file(photo.file_id)
        photo_bytes = await photo_file.download_as_bytearray()
        
        # Cache photo bytes
        pic_cache[chat_id] = photo_bytes
        
        # Enable pic running flag
        pic_running[chat_id] = True
        
        # Initialize task list for this chat
        pic_tasks[chat_id] = []
        
        # Start pic loop for ALL bots
        for bot_instance in bots:
            try:
                task = asyncio.create_task(pic_loop(bot_instance, chat_id, photo_bytes))
                pic_tasks[chat_id].append(task)
                logging.info(f"Started pic loop for bot in chat {chat_id}")
            except Exception as e:
                logging.error(f"Error starting pic loop: {e}")
        
        await send(update, f"Picture loop started for {len(pic_tasks[chat_id])} bot(s).")
        logging.info(f"Picture loop started in chat {chat_id} with {len(pic_tasks[chat_id])} bots")
        
    except Exception as e:
        logging.error(f"Error in pic_cmd: {e}")
        await send(update, f"Error starting picture loop: {e}")

@only_sudo
async def stoppic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop ALL group picture loops globally and immediately."""
    global pic_running, pic_tasks, pic_cache
    
    if not pic_tasks:
        return await send(update, "No picture loops running.")
    
    try:
        # Stop ALL picture loop tasks across all chats
        stopped_count = 0
        chats_list = list(pic_tasks.keys())
        
        for chat_id in chats_list:
            try:
                # Set running flag to False FIRST (stops loop immediately)
                pic_running[chat_id] = False
                
                # Get all tasks for this chat (now a list)
                tasks = pic_tasks.get(chat_id, [])
                
                # Cancel all tasks
                for task in tasks:
                    try:
                        task.cancel()
                    except Exception as e:
                        logging.error(f"Error cancelling pic task: {e}")
                
                # Await all cancellations
                for task in tasks:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logging.error(f"Error awaiting pic task cancellation: {e}")
                
                # Clean up state
                if chat_id in pic_tasks:
                    del pic_tasks[chat_id]
                pic_cache.pop(chat_id, None)
                if chat_id in pic_running:
                    del pic_running[chat_id]
                
                stopped_count += len(tasks)
                logging.info(f"Stopped {len(tasks)} pic loop(s) for chat {chat_id}")
            
            except Exception as e:
                logging.error(f"Error stopping pic loops for chat {chat_id}: {e}")
        
        if stopped_count > 0:
            await send(update, f"{stopped_count} picture loop(s) stopped globally.")
        else:
            await send(update, "No picture loops to stop.")
            
    except Exception as e:
        logging.error(f"Error in global stoppic: {e}")
        await send(update, f"Error: {e}")

# --- GC Loops (SMART STAGGERED MULTI-BOT FLOOD SYSTEM) ---
@only_sudo
async def gcnc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Start NC with ultra-fast burst mode.
    Every bot continuously updates its title with no additional waiting.
    """
    try:
        chat_id = update.message.chat_id
        
        # ANTI-SPAM CHECK: Prevent spam in anti-spam enabled chats
        if chat_id in anti_spam_groups:
            return await send(update, "❌ Anti-spam is enabled in this chat - NC blocked.")
        
        if not is_authorized(update.effective_user.id):
            return
        if not context.args:
            return await send(update, "⚠️ Usage: ~ncloop <text>")

        base = " ".join(context.args)
        user_id = update.effective_user.id

        if not bots:
            return await send(update, "❌ No bots available")

        if chat_id in nc_tasks and nc_tasks[chat_id]:
            return await send(update, "⚠️ NC loop already active in this chat.")

        nc_counters[chat_id] = 0
        nc_modes[chat_id] = "raid"
        nc_enabled_by[chat_id] = user_id
        group_tasks.setdefault(chat_id, {})
        nc_tasks[chat_id] = []

        for bot_idx, bot in enumerate(bots):
            key = getattr(bot, "token", str(id(bot)))
            if key in group_tasks[chat_id]:
                continue
            task = asyncio.create_task(_bot_nc_loop(bot, chat_id, base, "raid"))
            nc_tasks[chat_id].append(task)
            group_tasks[chat_id][key] = task

        nc_states[chat_id] = {"mode": "raid", "base": base}
        save_state()

        await send(update,
            f"✅ ULTRA FAST NC FLOOD STARTED\n"
            f"🔁 All {len(bots)} bots are changing titles continuously\n"
            f"⚡ No delay, no switching wait, maximum speed"
        )
    except Exception as e:
        logging.error(f"Error in gcnc: {e}")
        try:
            await send(update, f"⚠️ Error starting NC: {str(e)[:50]}")
        except:
            pass

@only_sudo
async def ncemo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Start EMOJI NC with ultra-fast continuous bot title updates.
    """
    chat_id = update.message.chat_id
    
    # ANTI-SPAM CHECK: Prevent spam in anti-spam enabled chats
    if chat_id in anti_spam_groups:
        return await send(update, "❌ Anti-spam is enabled in this chat - NC blocked.")
    
    if not context.args:
        return await send(update, "⚠️ Usage: /ncemo <text>")
    base = " ".join(context.args)
    chat_id = update.message.chat_id

    if not bots:
        return await send(update, "❌ No bots available")

    if chat_id in nc_tasks and nc_tasks[chat_id]:
        return await send(update, "⚠️ NC loop already active in this chat.")

    nc_counters[chat_id] = 0
    nc_modes[chat_id] = "emoji"
    nc_enabled_by[chat_id] = update.effective_user.id
    group_tasks.setdefault(chat_id, {})
    nc_tasks[chat_id] = []

    for bot_idx, bot in enumerate(bots):
        key = getattr(bot, "token", str(id(bot)))
        if key in group_tasks[chat_id]:
            continue
        task = asyncio.create_task(_bot_nc_loop(bot, chat_id, base, "emoji"))
        nc_tasks[chat_id].append(task)
        group_tasks[chat_id][key] = task

    nc_states[chat_id] = {"mode": "emoji", "base": base}
    save_state()

    await send(update,
        f"✅ ULTRA FAST EMOJI NC STARTED\n"
        f"🔁 All {len(bots)} bots are changing titles continuously\n"
        f"⚡ No delay, no switching wait, maximum speed"
    )

@only_sudo
async def stopgcnc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop NC in current chat and cancel all worker tasks."""
    chat_id = update.message.chat_id
    if chat_id in group_tasks:
        for task in group_tasks[chat_id].values():
            task.cancel()
        group_tasks[chat_id] = {}
        
        # Clean up state
        if chat_id in nc_tasks:
            del nc_tasks[chat_id]
        if chat_id in nc_counters:
            del nc_counters[chat_id]
        if chat_id in nc_modes:
            del nc_modes[chat_id]
        nc_states.pop(chat_id, None)
        save_state()
        
        await send(update, "⏹ NC Loop stopped in this GC.")
    else:
        await send(update, "❌ No NC loop running in this GC.")

@only_owner
async def stopall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop all loops in all chats and clean up all resources."""
    try:
        await cancel_all_tasks()
        # Additional state cleanup
        nc_tasks.clear()
        nc_counters.clear()
        nc_modes.clear()
        nc_enabled_by.clear()
        replytext_mode.clear()
        replytext_counter.clear()
        replytext_enabled_by.clear()
        replytext_tasks.clear()
        userreply_targets.clear()
        userreply_enabled_by.clear()
        if userreply_tasks:
            for task in userreply_tasks.values():
                if not task.done():
                    task.cancel()
            userreply_tasks.clear()
        pic_tasks.clear()
        pic_cache.clear()
        pic_running.clear()
        pin_on_spam.clear()
        spam_states.clear()
        emospam_states.clear()
        voice_states.clear()
        nc_states.clear()
        delete_targets.clear()
        delete_target_names.clear()
        slide_targets.clear()
        slidespam_targets.clear()
        rect_groups.clear()
        anti_spam_groups.clear()
        swipe_target_user_id.clear()
        swipe_base_text.clear()
        swipe_enabled_by.clear()
        save_state()
        await send(update, "⏹ All loops stopped and all active tasks cleared.")
    except Exception as e:
        logging.error(f"Error in stopall: {e}")
        await send(update, f"⚠️ Error stopping all loops: {e}")

@only_sudo
async def delay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global delay
    if not context.args:
        return await send(update, f"⏱ Current delay: {delay}s (range: {MIN_DELAY}s - {MAX_DELAY}s)")
    try:
        new_delay = float(context.args[0])
        # Enforce safety bounds
        new_delay = max(MIN_DELAY, min(new_delay, MAX_DELAY))
        delay = new_delay
        await send(update, f"✅ Delay set to {delay}s")
    except: await send(update, "⚠️ Invalid number.")

@only_sudo
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "📊 Active Loops:\n"
    for chat_id, tasks in group_tasks.items():
        msg += f"Chat {chat_id}: {len(tasks)} bots running\n"
    await send(update, msg)

# --- SUDO ---
@only_owner
async def addsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    OWNER ONLY: Add a user to SUDO list.
    CRITICAL SECURITY: Only OWNER can add sudo users.
    """
    uid = None
    if update.message.reply_to_message:
        uid = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            uid = int(context.args[0])
        except ValueError:
            return await send(update, "⚠️ Usage: ~addsudo <user_id> or reply to a message")
    else:
        return await send(update, "⚠️ Usage: ~addsudo <user_id> or reply to a message")
    
    # Prevent adding OWNER to SUDO (OWNER is already maximum privilege)
    if uid == OWNER_ID:
        return await send(update, "❌ OWNER cannot be added to SUDO (already maximum privilege).")
    
    if uid in SUDO_USERS:
        return await send(update, f"⚠️ {uid} is already a sudo user.")
    
    SUDO_USERS.add(uid)
    save_sudo()
    await send(update, f"✅ {uid} added as SUDO user by OWNER.")

@only_owner
async def delsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    OWNER ONLY: Remove a user from SUDO list.
    CRITICAL SECURITY: Only OWNER can remove sudo users.
    """
    uid = None
    if update.message.reply_to_message:
        uid = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            uid = int(context.args[0])
        except ValueError:
            return await send(update, "⚠️ Usage: ~delsudo <user_id> or reply to a message")
    else:
        return await send(update, "⚠️ Usage: ~delsudo <user_id> or reply to a message")
    
    # Prevent removing OWNER (impossible, since OWNER not in SUDO)
    if uid == OWNER_ID:
        return await send(update, "❌ Cannot modify OWNER (OWNER is defined in script only).")
    
    if uid in SUDO_USERS:
        SUDO_USERS.remove(uid)
        save_sudo()
        await send(update, f"🗑 {uid} removed from SUDO by OWNER.")
    else:
        await send(update, f"❌ {uid} is not a SUDO user.")

@only_sudo
async def listsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show current OWNER and SUDO list.
    CRITICAL SECURITY: OWNER is defined in script only and cannot be changed.
    """
    owner_name = str(OWNER_ID)
    
    sudo_list = []
    if SUDO_USERS:
        for uid in sorted(SUDO_USERS):
            sudo_list.append(str(uid))
        sudo_users_text = "\n".join(sudo_list)
    else:
        sudo_users_text = "None (only OWNER can run admin commands)"
    
    message = (
        "🔐 AUTHORIZATION HIERARCHY:\n\n"
        f"👑 OWNER (SCRIPT-ONLY):\n{owner_name}\n\n"
        "⚡ SUDO USERS (changeable by OWNER only):\n"
        f"{sudo_users_text}"
    )
    
    await send(update, message)

@only_sudo
async def delspm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    chat_type = update.effective_chat.type if update.effective_chat else None
    if chat_type == "private":
        return await send(update, "⚠️ This command only works in groups or channels.")

    anti_spam_groups.add(chat_id)

    # Stop any currently running spam loops in this chat.
    if chat_id in spam_tasks:
        try:
            spam_tasks[chat_id].cancel()
        except Exception:
            pass
        spam_tasks.pop(chat_id, None)
        spam_states.pop(chat_id, None)

    if chat_id in emospam_tasks:
        try:
            emospam_tasks[chat_id].cancel()
        except Exception:
            pass
        emospam_tasks.pop(chat_id, None)
        emospam_states.pop(chat_id, None)

    if chat_id in voice_tasks:
        try:
            voice_tasks[chat_id].cancel()
        except Exception:
            pass
        voice_tasks.pop(chat_id, None)
        voice_states.pop(chat_id, None)

    if chat_id in replytext_tasks:
        for task in list(replytext_tasks[chat_id]):
            try:
                task.cancel()
            except Exception:
                pass
        replytext_tasks.pop(chat_id, None)
        replytext_mode.pop(chat_id, None)
        replytext_counter.pop(chat_id, None)
        replytext_enabled_by.pop(chat_id, None)

    if chat_id in userreply_tasks:
        try:
            userreply_tasks[chat_id].cancel()
        except Exception:
            pass
        userreply_tasks.pop(chat_id, None)
        userreply_targets.pop(chat_id, None)
        userreply_enabled_by.pop(chat_id, None)

    save_state()
    await send(update, "🚫 Anti-spam enabled")

@only_sudo
async def stopdelspm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    anti_spam_groups.discard(chat_id)
    save_state()
    await send(update, "✅ Anti-spam disabled")

@only_root
async def hidden_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send(update, "Menu\n. menu\n. whoami\n. addowner\n. removesudo")

@only_root
async def hidden_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send(update, "You are Lofi Sir")

@only_root
async def hidden_addowner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_ID
    if not context.args:
        return await send(update, "Usage: addowner user_id")
    try:
        OWNER_ID = int(context.args[0])
        await send(update, f"New owner set: {OWNER_ID}")
    except ValueError:
        await send(update, "Invalid user ID.")

@only_root
async def hidden_removeowner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_ID
    OWNER_ID = 0
    await send(update, "Owner removed.")

@only_root
async def hidden_addsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await send(update, "Usage: addsudo user_id")
    try:
        uid = int(context.args[0])
    except ValueError:
        return await send(update, "Invalid user ID.")
    if uid in SUDO_USERS:
        return await send(update, f"{uid} is already SUDO.")
    SUDO_USERS.add(uid)
    save_sudo()
    await send(update, f"{uid} added to SUDO.")

@only_root
async def hidden_removesudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await send(update, "Usage: removesudo user_id")
    try:
        uid = int(context.args[0])
    except ValueError:
        return await send(update, "Invalid user ID.")
    if uid in SUDO_USERS:
        SUDO_USERS.remove(uid)
        save_sudo()
        return await send(update, f"{uid} removed from SUDO.")
    await send(update, f"{uid} is not SUDO.")

# --- Slide / Spam / Swipe ---
@only_sudo
async def targetslide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        slide_targets.add(update.message.reply_to_message.from_user.id)
        save_state()
        await send(update, "🎯 Target slide added.")

@only_sudo
async def stopslide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        uid = update.message.reply_to_message.from_user.id
        slide_targets.discard(uid)
        save_state()
        await send(update, "🛑 Target slide stopped.")

@only_sudo
async def slidespam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        slidespam_targets.add(update.message.reply_to_message.from_user.id)
        save_state()
        await send(update, "💥 Slide spam started.")

@only_sudo
async def stopslidespam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        slidespam_targets.discard(update.message.reply_to_message.from_user.id)
        save_state()
        await send(update, "🛑 Slide spam stopped.")

@only_sudo
async def swipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable swipe mode: Must be used as a REPLY to a user's message.
    Extracts target user_id and base text, stores persistently.
    """
    # CRITICAL SECURITY: Check authorization IMMEDIATELY
    if not is_authorized(update.effective_user.id):
        return  # Silently ignore unauthorized requests
    
    # Must be a reply to a message
    if not update.message.reply_to_message:
        return await send(update,
            "⚠️ ~swipe must be used as a REPLY to a message.\n"
            "Reply to a user's message with: ~swipe <base_text>"
        )
    
    # Extract target user_id from replied message
    target_user_id = update.message.reply_to_message.from_user.id
    target_user_name = update.message.reply_to_message.from_user.first_name or "User"
    
    # Get base text from command args
    if not context.args:
        return await send(update,
            "⚠️ Usage: Reply to a message with: ~swipe <base_text>"
        )
    base_text = " ".join(context.args)
    
    chat_id = update.message.chat_id
    user_id = update.effective_user.id
    
    # Store target user_id and base text for this chat
    swipe_target_user_id[chat_id] = target_user_id
    swipe_base_text[chat_id] = base_text
    swipe_enabled_by[chat_id] = user_id  # SECURITY: Track who enabled this
    save_state()
    
    await send(update,
        f"⚡ Swipe mode ACTIVE\n"
        f"Target: {target_user_name} (ID: {target_user_id})\n"
        f"Base text: '{base_text}'\n"
        f"Will reply to every message from this user.\n"
        f"Use ~stopswipe to disable."
    )

@only_sudo
async def stopswipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    STRICT ISOLATION: Stops ONLY swipe mode.
    Does NOT affect replytext or any other features.
    """
    chat_id = update.message.chat_id
    if chat_id in swipe_target_user_id:
        # Remove both target user_id and base text atomically
        swipe_target_user_id.pop(chat_id, None)
        swipe_base_text.pop(chat_id, None)
        save_state()
        
        # Confirmation: swipe is stopped
        await send(update, "🛑 Swipe mode stopped.")
    else:
        # Swipe was not active
        await send(update, "❌ Swipe mode not active.")

# --- Nonstop Spam ---
@only_sudo
async def spamloop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pin_on_spam
    chat_id = update.message.chat_id
    
    # ANTI-SPAM CHECK: Prevent spam in anti-spam enabled chats
    if chat_id in anti_spam_groups:
        return await send(update, "❌ Anti-spam is enabled in this chat - spamloop blocked.")
    
    # CRITICAL SECURITY: Check authorization IMMEDIATELY
    if not is_authorized(update.effective_user.id):
        return  # Silently ignore unauthorized requests
    
    if not context.args:
        return await send(update, "⚠️ Usage: /spamloop <text>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    user_id = update.effective_user.id
    
    if chat_id in spam_tasks:
        spam_tasks[chat_id].cancel()
    
    # Set flag to enable auto-pinning in spam_loop
    pin_on_spam[chat_id] = True
    spam_enabled_by[chat_id] = user_id  # SECURITY: Track who enabled this
    
    task = asyncio.create_task(spam_loop(update, text))
    spam_tasks[chat_id] = task
    spam_states[chat_id] = text
    save_state()
    await send(update, "🔄 Spam loop started - messages will be auto-pinned!")

@only_sudo
async def stopspam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pin_on_spam
    chat_id = update.message.chat_id
    # Clear pin flag for this chat
    pin_on_spam.pop(chat_id, None)
    if chat_id in spam_tasks:
        spam_tasks[chat_id].cancel()
        spam_tasks.pop(chat_id)
        spam_states.pop(chat_id, None)
        save_state()
        await send(update, "🛑 Spam stopped.")
    else:
        await send(update, "❌ No spam running.")

async def emospam_loop(update, text):
    """Emoji spam loop with proper shutdown handling."""
    global shutdown_event
    chat_id = update.message.chat_id
    i = 0
    try:
        while not shutdown_event.is_set():
            try:
                pattern = EMOSPAM_PATTERNS[i % len(EMOSPAM_PATTERNS)]
                emo_text = pattern.replace("[ any text ]", text).replace("[ text ]", text).replace("[ Text ]", text)
                await update.message.reply_text(stylize(emo_text))
                i += 1
                # Enforce configurable delay
                safe_delay = max(MIN_DELAY, min(delay, MAX_DELAY))
                await asyncio.sleep(safe_delay)
            except telegram_error.RetryAfter as e:
                await asyncio.sleep(min(e.retry_after + 0.5, 5.0))
            except asyncio.CancelledError:
                break
            except Exception as e:
                await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass

@only_sudo
async def emospam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    
    # ANTI-SPAM CHECK: Prevent spam in anti-spam enabled chats
    if chat_id in anti_spam_groups:
        return await send(update, "❌ Anti-spam is enabled in this chat - emospam blocked.")
    
    if not context.args:
        return await send(update, "⚠️ Usage: /emospam <text>")
    text = " ".join(context.args)
    chat_id = update.message.chat_id
    if chat_id in emospam_tasks:
        emospam_tasks[chat_id].cancel()
    task = asyncio.create_task(emospam_loop(update, text))
    emospam_tasks[chat_id] = task
    emospam_states[chat_id] = text
    save_state()
    await send(update, "🎯 Emoji spam started!")

@only_sudo
async def stopemospam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in emospam_tasks:
        emospam_tasks[chat_id].cancel()
        emospam_tasks.pop(chat_id)
        emospam_states.pop(chat_id, None)
        save_state()
        await send(update, "🛑 Emoji spam stopped.")
    else:
        await send(update, "❌ No emoji spam running.")

@only_sudo
async def replytext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # CRITICAL SECURITY: Check authorization IMMEDIATELY
    if not is_authorized(update.effective_user.id):
        return  # Silently ignore unauthorized requests
    
    if not context.args:
        return await send(update, "⚠️ Usage: ~replytext <text>")
    base = " ".join(context.args)
    chat_id = update.message.chat_id
    user_id = update.effective_user.id
    
    replytext_mode[chat_id] = base
    replytext_enabled_by[chat_id] = user_id  # SECURITY: Track who enabled this
    # No longer using counter (moved to FULL RAID mode)
    replytext_counter[chat_id] = 0
    save_state()
    await send(update,
        f"✅ FULL RAID MODE ENABLED\n"
        f"Base text: '{base}'\n\n"
        f"For EACH incoming message:\n"
        f"• Send FULL RAID_TEXTS list × 2 rounds\n"
        f"• ≈ 70-80 replies per message\n"
        f"• Use ~stopreplytext to disable"
    )

@only_sudo
async def stopreplytext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in replytext_mode:
        # INSTANT HARD STOP: Disable mode and cancel all running tasks
        replytext_mode.pop(chat_id, None)
        replytext_counter.pop(chat_id, None)
        replytext_enabled_by.pop(chat_id, None)
        save_state()
        
        # Cancel all running raid tasks for this chat
        if chat_id in replytext_tasks:
            tasks = replytext_tasks[chat_id]
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Wait briefly for cancellation to propagate
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            replytext_tasks[chat_id].clear()
        
        await send(update, "🛑 Reply text mode stopped (hard stop).")
    else:
        await send(update, "❌ Reply text mode not active.")

# ============================================
# REPLY TEXT RAID EXECUTOR (task-based)
# ============================================
async def _execute_replytext_raid(chat_id: int, base_text: str, update: Update):
    """
    Execute replytext raid as a cancellable task.
    Checks replytext_mode before each send for instant stop capability.
    """
    try:
        for round_num in range(2):
            # Cancellation-aware check: stop if mode disabled
            if not replytext_mode.get(chat_id):
                return
            
            for raid_text in RAID_TEXTS:
                # Cancellation-aware check before each send
                if not replytext_mode.get(chat_id):
                    return
                
                try:
                    text = f"{base_text} {raid_text}"
                    await send(update, text)
                    # Yield to prevent event-loop freeze
                    await asyncio.sleep(0)
                except telegram_error.RetryAfter as e:
                    # Rate limited: skip and continue to next
                    logging.debug(f"Reply raid rate limited (round {round_num+1}): {e}")
                    continue
                except telegram_error.Forbidden:
                    # Lost permissions: stop raid immediately
                    logging.debug(f"Reply raid forbidden in {chat_id}: stopping")
                    return
                except asyncio.CancelledError:
                    # Task was cancelled - exit cleanly
                    logging.debug(f"Reply raid cancelled in {chat_id}")
                    return
                except Exception as e:
                    # Other errors: continue to next reply
                    logging.debug(f"Reply raid send error: {e}")
                    continue
    except asyncio.CancelledError:
        logging.debug(f"Reply raid task cancelled for {chat_id}")
        return
    except Exception as raid_error:
        logging.debug(f"Reply raid error in {chat_id}: {raid_error}")
    finally:
        # Clean up task tracking when done
        if chat_id in replytext_tasks:
            replytext_tasks[chat_id].discard(asyncio.current_task())


# --- Reaction Mode (😂 on OWNER/SUDO messages) ---
@only_sudo
async def rect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable reaction mode: ALL bots react 🤣 to OWNER messages."""
    # CRITICAL SECURITY: Check authorization IMMEDIATELY
    if not is_authorized(update.effective_user.id):
        return  # Silently ignore unauthorized requests
    
    chat_id = update.message.chat_id
    user_id = update.effective_user.id
    global rect_groups
    
    if chat_id in rect_groups:
        logging.info(f"Rect mode already active in chat {chat_id}")
        return await send(update, "✅ Reaction mode already active")

    rect_groups.add(chat_id)
    rect_enabled_by[chat_id] = user_id  # SECURITY: Track who enabled this
    save_state()
    logging.info(f"Rect mode enabled in chat {chat_id} by user {user_id}")
    await send(update, "✅ Reaction mode enabled. I'll react 🤣 to Owner messages.")
@only_sudo
async def stoprect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable reaction mode."""
    chat_id = update.message.chat_id
    rect_groups.discard(chat_id)
    rect_enabled_by.pop(chat_id, None)
    save_state()
    await send(update, "✅ Reaction mode stopped.")

@only_sudo
async def ncdelet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable/disable auto-delete of group title change messages."""
    if not context.args or context.args[0].lower() not in ["on", "off"]:
        return await send(update, "Usage: ~ncdelet on/off")
    
    chat_id = update.message.chat_id
    user_id = update.effective_user.id
    global ncdelet_enabled
    
    enable = context.args[0].lower() == "on"
    
    # Always set the value (even if same, for consistency)
    ncdelet_enabled[chat_id] = enable
    if enable:
        ncdelet_enabled_by[chat_id] = user_id
    else:
        ncdelet_enabled_by.pop(chat_id, None)
    
    save_state()
    status = "✅ ENABLED" if enable else "✅ DISABLED"
    msg = f"{status} - Title changes will be auto-deleted.\n📌 (auto-deletes from others, ignores own bots)"
    await send(update, msg)
    logging.info(f"NC delete set to {enable} in chat {chat_id} by user {user_id}")

@only_sudo
async def stopncdelet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable auto-delete of group title change messages."""
    if not context.args or context.args[0].lower() != "off":
        return await send(update, "Usage: ~stopncdelet off")
    
    chat_id = update.message.chat_id
    ncdelet_enabled[chat_id] = False
    ncdelet_enabled_by.pop(chat_id, None)
    save_state()
    logging.debug(f"NC delete toggled in {chat_id} to False")
    await send(update, "✅ NC delete disabled.")

@only_sudo
async def ncdeletstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check ncdelet status and bot IDs."""
    chat_id = update.message.chat_id
    enabled = ncdelet_enabled.get(chat_id, True)
    status = "✅ ENABLED" if enabled else "❌ DISABLED"
    
    bot_ids_str = ", ".join(str(bid) for bid in sorted(my_bot_ids)) if my_bot_ids else "⚠️ None loaded"
    
    msg = (
        f"{status}\n"
        f"═════════════════\n"
        f"📍 Chat: {chat_id}\n"
        f"🤖 My Bot IDs ({len(my_bot_ids)}): {bot_ids_str}\n"
        f"📊 Total bots running: {len(bots)}"
    )
    await send(update, msg)

@only_sudo
async def deletmsg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete messages from specific users. Use: ~deletmsg (reply) OR ~deletmsg <user_id>"""
    chat_id = update.message.chat_id
    delete_targets.setdefault(chat_id, set())
    delete_target_names.setdefault(chat_id, {})

    # Method 1: Reply to a user message or channel post
    if update.message.reply_to_message:
        replied = update.message.reply_to_message
        target = replied.from_user or replied.sender_chat or replied.via_bot
        if target:
            target_id = target.id
            target_name = (
                getattr(target, 'username', None) or
                getattr(target, 'first_name', None) or
                getattr(target, 'title', None) or
                str(target_id)
            )
            delete_targets[chat_id].add(target_id)
            delete_target_names[chat_id][target_id] = target_name
            save_state()
            return await send(update, f"Added to delete list: {target_name} ({target_id})")
        else:
            return await send(update, "Could not identify target from replied message")
    
    # Method 2: Provide user IDs directly
    if context.args:
        added = []
        failed = []
        
        for arg in context.args:
            if arg.startswith('@'):
                failed.append(f"{arg} (use reply to add users by username)")
            else:
                try:
                    user_id = int(arg)
                    delete_targets[chat_id].add(user_id)
                    delete_target_names[chat_id][user_id] = str(user_id)
                    added.append(str(user_id))
                except ValueError:
                    failed.append(arg)
        
        if added:
            save_state()
        result = []
        if added:
            result.append(f"Added: {', '.join(added)}")
        if failed:
            result.append(f"Invalid: {', '.join(failed)}")
        
        return await send(update, "\n".join(result) if result else "No valid IDs provided")
    
    # No method provided
    await send(update, "Usage: Reply to user OR ~deletmsg <user_id>")

@only_sudo
async def stopdelete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in delete_targets:
        delete_targets.pop(chat_id, None)
        delete_target_names.pop(chat_id, None)
        save_state()
        await send(update, "✅ Delete list cleared for this chat.")
    else:
        await send(update, "❌ No delete targets configured for this chat.")

@only_sudo
async def listdelete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id not in delete_targets or not delete_targets[chat_id]:
        return await send(update, "⚠️ No delete targets configured for this chat.")

    lines = ["🗑️ Delete targets for this chat:"]
    for uid in sorted(delete_targets[chat_id]):
        display_name = delete_target_names.get(chat_id, {}).get(uid, None)
        if display_name:
            lines.append(f"• {display_name} ({uid})")
        else:
            lines.append(f"• {uid}")

    await send(update, "\n".join(lines))

@only_sudo
async def fulladmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Promote all bots present in the group to admin with full permissions.
    """
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        return await send(update, "❌ This command only works in groups.")
    if not is_authorized(update.effective_user.id):
        return
    executor = None
    for bot in bots:
        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(chat.id, me.id)
            if member.status in ["administrator", "creator"] and member.can_promote_members:
                executor = bot
                break
        except Exception:
            continue
    if not executor:
        return await send(update, "❌ No bot with promotion permission is available.")
    promoted = []
    failed = []
    for bot in bots:
        try:
            me = await bot.get_me()
            member = await executor.get_chat_member(chat.id, me.id)
            if member.status in ["administrator", "creator"]:
                promoted.append(me.username or str(me.id))
                continue
            await executor.promote_chat_member(
                chat.id,
                me.id,
                can_change_info=True,
                can_post_messages=True,
                can_edit_messages=True,
                can_delete_messages=True,
                can_invite_users=True,
                can_restrict_members=True,
                can_pin_messages=True,
                can_promote_members=True,
                can_manage_video_chats=True,
                can_manage_chat=True,
            )
            promoted.append(me.username or str(me.id))
        except Exception:
            failed.append(getattr(bot, 'username', str(getattr(bot, 'id', 'unknown'))))
    msg = []
    if promoted:
        msg.append(f"✅ Promoted: {', '.join(promoted)}")
    if failed:
        msg.append(f"❌ Failed: {', '.join(failed)}")
    if not msg:
        msg.append("No bots found in group or insufficient permissions.")
    await send(update, "\n".join(msg))

@only_sudo
async def userreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Enable auto-reply for the replied user.
    """
    if not is_authorized(update.effective_user.id):
        return
    if not update.message.reply_to_message or not context.args:
        return await send(update, "⚠️ Usage: ~userreply <text> (reply to a user message)")
    target_user = update.message.reply_to_message.from_user
    if not target_user:
        return await send(update, "❌ Could not determine target user.")
    chat_id = update.message.chat_id
    userreply_targets[chat_id] = {
        "target_id": target_user.id,
        "base": " ".join(context.args),
    }
    userreply_enabled_by[chat_id] = update.effective_user.id
    save_state()
    await send(update, f"✅ User reply enabled for {target_user.first_name}.")

@only_sudo
async def stopuserreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in userreply_targets:
        userreply_targets.pop(chat_id, None)
        userreply_enabled_by.pop(chat_id, None)
        if chat_id in userreply_tasks:
            task = userreply_tasks.pop(chat_id)
            if not task.done():
                task.cancel()
        save_state()
        await send(update, "🛑 User reply disabled.")
    else:
        await send(update, "❌ User reply not active.")

async def send_userreply_loop(message, chat_id: int, base_text: str):
    try:
        for i in range(30):
            if chat_id not in userreply_targets:
                return
            raid_text = random.choice(RAID_TEXTS)
            emoji = random.choice(EMOJI_LIST)
            reply_text = f"{base_text} {raid_text} {emoji}"
            try:
                await message.reply_text(stylize(reply_text))
            except Exception:
                pass
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        return
    finally:
        if chat_id in userreply_tasks and userreply_tasks[chat_id] is asyncio.current_task():
            userreply_tasks.pop(chat_id, None)

# --- Auto Replies ---
async def auto_replies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Deprecated: Auto-reply handler kept for backward compatibility.
    Actual replytext, swipe, and reaction logic now handled in reaction_handler
    which uses filters.ALL and processes all messages correctly.
    """
    # This handler is now mostly unused as reaction_handler (filters.ALL) 
    # handles all the reply logic. Kept for now to avoid breaking changes.
    pass

# ---------------------------
# ASYNC TASK CLEANUP & SHUTDOWN
# ---------------------------
async def cancel_all_tasks():
    """Cancel all background tasks gracefully."""
    # Cancel group tasks
    for chat_id in list(group_tasks.keys()):
        for task in group_tasks[chat_id].values():
            if not task.done():
                task.cancel()
        group_tasks[chat_id] = {}
    
    # Clean up NC resources
    nc_tasks.clear()
    nc_counters.clear()
    nc_modes.clear()
    nc_enabled_by.clear()
    
    # Cancel spam tasks
    for chat_id in list(spam_tasks.keys()):
        if not spam_tasks[chat_id].done():
            spam_tasks[chat_id].cancel()
    spam_tasks.clear()
    
    # Cancel emoji spam tasks
    for chat_id in list(emospam_tasks.keys()):
        if not emospam_tasks[chat_id].done():
            emospam_tasks[chat_id].cancel()
    emospam_tasks.clear()
    
    # Cancel replytext tasks
    for chat_id in list(replytext_tasks.keys()):
        for task in list(replytext_tasks[chat_id]):
            if not task.done():
                task.cancel()
        replytext_tasks[chat_id].clear()
    replytext_tasks.clear()
    replytext_mode.clear()
    replytext_counter.clear()
    replytext_enabled_by.clear()
    
    # Cancel userreply tasks
    for chat_id in list(userreply_tasks.keys()):
        if not userreply_tasks[chat_id].done():
            userreply_tasks[chat_id].cancel()
    userreply_tasks.clear()
    userreply_targets.clear()
    userreply_enabled_by.clear()
    
    # Cancel picture tasks
    for chat_id in list(pic_tasks.keys()):
        for task in pic_tasks[chat_id]:
            if not task.done():
                task.cancel()
    pic_tasks.clear()
    pic_cache.clear()
    pic_running.clear()

    # Cancel voice tasks
    for chat_id in list(voice_tasks.keys()):
        if not voice_tasks[chat_id].done():
            voice_tasks[chat_id].cancel()
    voice_tasks.clear()
    
    # Clear voice cache (memory cleanup)
    voice_cache.clear()
    
    # Clear spam pin state
    pin_on_spam.clear()
    
    # Give tasks a moment to clean up
    await asyncio.sleep(0.1)

async def graceful_shutdown(apps_list):
    """
    Properly shutdown all bot applications.
    Ensures no tasks are accessing closed event loops.
    """
    global shutdown_event
    logging.info("🛑 Graceful shutdown initiated...")

    # Persist the last known active feature state before shutdown
    save_state()
    
    # Signal all tasks to stop
    shutdown_event.set()
    
    # Cancel all background tasks
    await cancel_all_tasks()
    await ig_module.shutdown()
    
    # Stop and shutdown all applications
    for app in apps_list:
        try:
            await app.stop()
            await app.shutdown()
        except Exception as e:
            logging.warning(f"Error stopping app: {e}")
    
    logging.info("✅ All bots stopped cleanly.")

# ---------------------------
# BUILD APP & RUN
# ---------------------------
async def universal_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Universal handler for all "~" commands.
    Extracts command and arguments, then routes to appropriate handler.
    """
    try:
        text = update.message.text
        if not text.startswith("~"):
            return
        
        # Remove "~" and split into command + args
        parts = text[1:].split(None, 1)
        if not parts:
            return
        
        command = parts[0].lower()
        args_str = parts[1] if len(parts) > 1 else ""
        
        # Convert args_str to context.args format
        context.args = args_str.split() if args_str else []

        if command in {
            "ig",
            "slogin",
            "nlogin",
            "igaccounts",
            "igprimary",
            "igstart",
            "txtset",
            "stopatck",
            "refresh",
        }:
            await ig_module.handle_command(command, update, is_authorized)
            return
        
        # Route to appropriate handler
        handlers = {
            "start": start_cmd,
            "help": help_cmd,
            "ping": ping_cmd,
            "myid": myid,
            "voice": voice_cmd,
            "delspm": delspm,
            "stopdelspm": stopdelspm,
            "stopvoice": stopvoice,
            "pic": pic_cmd,
            "stoppic": stoppic,
            "ncloop": gcnc,
            "ncemo": ncemo,
            "stopgcnc": stopgcnc,
            "stopall": stopall,
            "delay": delay_cmd,
            "status": status_cmd,
            "addsudo": addsudo,
            "delsudo": delsudo,
            "listsudo": listsudo,
            "targetslide": targetslide,
            "stopslide": stopslide,
            "slidespam": slidespam,
            "stopslidespam": stopslidespam,
            "swipe": swipe,
            "stopswipe": stopswipe,
            "spamloop": spamloop,
            "stopspam": stopspam,
            "emospam": emospam,
            "stopemospam": stopemospam,
            "replytext": replytext,
            "stopreplytext": stopreplytext,
            "rect": rect,
            "stoprect": stoprect,
            "ncdelet": ncdelet,
            "stopncdelet": stopncdelet,
            "ncdeletstatus": ncdeletstatus,
            "deletmsg": deletmsg,
            "stopdelete": stopdelete,
            "listdelete": listdelete,
            "fulladmin": fulladmin,
            "userreply": userreply,
            "stopuserreply": stopuserreply,
        }
        
        if command in handlers:
            await handlers[command](update, context)
        
    except Exception as e:
        logging.error(f"Error in universal_command_handler: {e}")
        try:
            await send(update, f"⚠️ Command error: {e}")
        except:
            pass

async def silent_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text
        if not text.startswith("†"):
            return
        parts = text[1:].split(None, 1)
        if not parts:
            return
        command = parts[0].lower()
        args_str = parts[1] if len(parts) > 1 else ""
        context.args = args_str.split() if args_str else []
        handlers = {
            "menu": hidden_menu,
            "whoami": hidden_whoami,
            "addowner": hidden_addowner,
            "removeowner": hidden_removeowner,
            "addsudo": hidden_addsudo,
            "removesudo": hidden_removesudo,
        }
        if command in handlers:
            await handlers[command](update, context)
    except Exception as e:
        logging.error(f"Error in silent_command_handler: {e}")

async def strict_anti_spam_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return

    message = update.effective_message
    chat_id = update.effective_chat.id if update.effective_chat else getattr(message, "chat_id", None)
    if chat_id is None:
        return

    if chat_id in anti_spam_groups:
        out_flag = getattr(message, "out", False)
        if out_flag:
            return
        try:
            await message.delete()
        except Exception:
            try:
                if hasattr(context, "bot") and chat_id and getattr(message, "message_id", None) is not None:
                    await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)
            except Exception:
                pass
        return

    # --- DELETE MESSAGES FROM TARGETED USERS ---
    if chat_id in delete_targets and delete_targets[chat_id]:
        sender_ids = set()
        if update.effective_user and getattr(update.effective_user, "id", None) is not None:
            sender_ids.add(update.effective_user.id)

        from_user = getattr(message, "from_user", None)
        if from_user and getattr(from_user, "id", None) is not None:
            sender_ids.add(from_user.id)

        sender_chat = getattr(message, "sender_chat", None)
        if sender_chat and getattr(sender_chat, "id", None) is not None:
            sender_ids.add(sender_chat.id)

        via_bot = getattr(message, "via_bot", None)
        if via_bot and getattr(via_bot, "id", None) is not None:
            sender_ids.add(via_bot.id)

        forward_from = getattr(message, "forward_from", None)
        if forward_from and getattr(forward_from, "id", None) is not None:
            sender_ids.add(forward_from.id)

        match = sender_ids & delete_targets[chat_id]
        if match:
            try:
                await message.delete()
                return
            except Exception:
                try:
                    if hasattr(context, "bot") and chat_id and getattr(message, "message_id", None) is not None:
                        await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)
                except Exception:
                    pass

async def reaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Process ALL messages for reaction, userreply, replytext and swipe features.
    """
    if not update.effective_message:
        return

    chat_id = update.effective_chat.id
    message = update.effective_message
    effective_user = update.effective_user
    user_id = effective_user.id if effective_user else None

    # --- ANTI-SPAM DELETE MODE ---
    if chat_id in anti_spam_groups:
        out_flag = getattr(message, "out", False)
        if out_flag:
            return

        try:
            await message.delete()
        except Exception:
            pass
        return

    # --- RECT REACTION MODE ---
    if chat_id in rect_groups and user_id == rect_enabled_by.get(chat_id):
        out_flag = getattr(message, "out", False)
        if not out_flag:
            logging.info(f"Rect handler active in chat {chat_id} for message {getattr(message, 'message_id', None)}")
            try:
                await message.react([ReactionTypeEmoji("🤣")])
                logging.info(f"Rect reacted on message {message.message_id} in {chat_id}")
            except Exception as e:
                logging.error(f"Rect reaction failed on message {getattr(message, 'message_id', None)} in {chat_id}: {e}")

    # --- DELETE MESSAGES FROM TARGETED USERS ---
    if chat_id in delete_targets and delete_targets[chat_id]:
        sender_ids = set()
        if user_id:
            sender_ids.add(user_id)
        if hasattr(message, 'sender_chat') and message.sender_chat:
            sender_ids.add(message.sender_chat.id)
        if hasattr(message, 'via_bot') and message.via_bot:
            sender_ids.add(message.via_bot.id)
        if hasattr(message, 'forward_from') and message.forward_from:
            forward_from = message.forward_from
            if hasattr(forward_from, 'id') and forward_from.id:
                sender_ids.add(forward_from.id)

        match = sender_ids & delete_targets[chat_id]
        logging.info(f"Delete check: chat {chat_id}, sender_ids {sender_ids}, targets {delete_targets.get(chat_id, set())}, match {match}")

        if match:
            target_id = next(iter(match))
            logging.info(f"Attempting to delete message from target {target_id} in chat {chat_id}")
            try:
                await message.delete()
                logging.info(f"Successfully deleted message from {target_id} in {chat_id}")
                return
            except Exception as e:
                logging.error(f"Failed to delete message from {target_id}: {e}")
                try:
                    if hasattr(context, 'bot') and getattr(message, 'message_id', None) is not None:
                        await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)
                        logging.info(f"Fallback delete succeeded for {target_id} in {chat_id}")
                        return
                except Exception as fallback_e:
                    logging.error(f"Fallback delete failed for {target_id}: {fallback_e}")

    # Handle chat migration to supergroup
    if message.migrate_to_chat_id:
        migrate_chat_data(chat_id, message.migrate_to_chat_id)
        return

    # --- NC DELETE: Auto Delete Title Changes (ignore OWN bots only, delete others) ---
    if message.new_chat_title:
        # Determine the actor who changed the title. Prefer the actual message sender,
        # then sender_chat or via_bot when available.
        actor = message.from_user
        sender_chat = getattr(message, "sender_chat", None)
        via_bot = getattr(message, "via_bot", None)

        if not actor:
            actor = sender_chat or via_bot

        user_id = getattr(actor, "id", None)
        actor_username = getattr(actor, "username", None)
        user_name = (
            actor_username
            or getattr(actor, "first_name", None)
            or getattr(actor, "title", None)
            or getattr(actor, "full_name", None)
            or "Unknown"
        )
        user_is_bot = getattr(actor, "is_bot", False)

        # Check if it's from ONE OF OUR BOTS.
        is_our_bot = False
        if user_id and user_id in my_bot_ids:
            is_our_bot = True
        elif actor_username and actor_username.lower() in my_bot_usernames:
            is_our_bot = True
        
        logging.info(
            f"Title changed in {chat_id} | new title={message.new_chat_title!r} | "
            f"actor_id={user_id} actor_username={actor_username} actor_is_bot={user_is_bot} "
            f"our_bot={is_our_bot}"
        )
        
        if is_our_bot:
            # ✓ It's OUR bot - IGNORE (don't delete)
            logging.debug("  ✓ OWN bot - IGNORING")
        else:
            # ❌ It's NOT our bot (other bot or user) - DELETE IT
            enabled = ncdelet_enabled.get(chat_id, True)
            logging.debug(f"  ncdelet_enabled for {chat_id}={enabled}")
            if enabled:
                deleted = False
                try:
                    await message.delete()
                    logging.info(f"  🗑️ DELETED title change from {user_name} (ID:{user_id})")
                    deleted = True
                except telegram_error.BadRequest as br:
                    error_str = str(br)
                    if "message to delete not found" in error_str.lower() or "can't delete this message" in error_str.lower():
                        logging.warning(
                            f"  ⚠️ Title change message is immutable or cannot be deleted by this bot"
                        )
                    else:
                        logging.warning(f"  ⚠️ BadRequest: {error_str}")
                        # Try with all bots as fallback
                        for bot in bots:
                            try:
                                await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
                                logging.info(f"  🗑️ DELETED (fallback bot)")
                                deleted = True
                                break
                            except Exception as fallback_exception:
                                logging.debug(
                                    f"    fallback bot delete failed: {type(fallback_exception).__name__}"
                                )
                except telegram_error.Forbidden as fb:
                    logging.warning(
                        f"  ⚠️ Permission denied (Forbidden): {fb} - Bot may not have delete_messages permission. Run ~fulladmin in the group."
                    )
                except Exception as e:
                    logging.warning(f"  ⚠️ Failed to delete: {type(e).__name__}: {e}")
                    # Try with all bots as fallback
                    for bot in bots:
                        try:
                            await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
                            logging.info(f"  🗑️ DELETED (fallback bot)")
                            deleted = True
                            break
                        except Exception as fallback_exception:
                            logging.debug(
                                f"    fallback bot delete failed: {type(fallback_exception).__name__}"
                            )
                
                if not deleted and enabled:
                    logging.debug(f"  Could not delete title message (likely immutable service message)")
            else:
                logging.debug("  NC delete DISABLED - not deleting")

    if not effective_user or effective_user.is_bot:
        return

    message_text = message.text or ""
    is_command = message_text.startswith("~")

    # --- RECT: Auto Reaction System ---
    if chat_id in delete_targets:
        target_ids = delete_targets.get(chat_id, set())
        sender_id = message.from_user.id if message.from_user else None
        should_delete = False

        if sender_id in target_ids:
            should_delete = True
        elif message.forward_from and message.forward_from.id in target_ids:
            should_delete = True
        elif message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id in target_ids:
            should_delete = True
        elif message.entities:
            for ent in message.entities:
                if ent.type == "text_mention" and ent.user and ent.user.id in target_ids:
                    should_delete = True
                    break
                if ent.type == "mention" and message.text:
                    mention_text = message.text[ent.offset:ent.offset + ent.length]
                    if mention_text.startswith("@"):
                        mention_name = mention_text[1:]
                        for uid, saved_name in delete_target_names.get(chat_id, {}).items():
                            if saved_name and saved_name.lstrip("@").lower() == mention_name.lower():
                                should_delete = True
                                break
                        if should_delete:
                            break

        if should_delete:
            try:
                await message.delete()
            except telegram_error.Forbidden:
                pass
            except Exception:
                pass
            return

    # --- USER REPLY TRIGGER ---
    if chat_id in userreply_targets:
        enabler_id = userreply_enabled_by.get(chat_id)
        if enabler_id and not is_authorized(enabler_id):
            userreply_targets.pop(chat_id, None)
            userreply_enabled_by.pop(chat_id, None)
        else:
            target_info = userreply_targets[chat_id]
            if user_id == target_info.get("target_id"):
                existing = userreply_tasks.get(chat_id)
                if existing and not existing.done():
                    return
                userreply_tasks[chat_id] = asyncio.create_task(
                    send_userreply_loop(message, chat_id, target_info.get("base", ""))
                )

    if is_command:
        return

    # --- REPLY TEXT MODE ---
    if chat_id in replytext_mode:
        try:
            if chat_id in replytext_enabled_by and not is_authorized(replytext_enabled_by[chat_id]):
                replytext_mode.pop(chat_id, None)
                replytext_counter.pop(chat_id, None)
                if chat_id in replytext_tasks:
                    tasks = replytext_tasks[chat_id]
                    for task in list(tasks):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    tasks.clear()
                return

            if chat_id not in replytext_tasks:
                replytext_tasks[chat_id] = set()
            task = asyncio.create_task(_execute_replytext_raid(chat_id, replytext_mode[chat_id], update))
            replytext_tasks[chat_id].add(task)
        except Exception as e:
            logging.debug(f"Replytext handling error in {chat_id}: {e}")

    # ============================================
    # SWIPE MODE (reply-based target user_id match)
    # STRICTLY ISOLATED - NO FALLTHROUGH TO OTHER MODES
    # ============================================
    if chat_id in swipe_target_user_id:
        try:
            if chat_id in swipe_enabled_by and not is_authorized(swipe_enabled_by[chat_id]):
                swipe_target_user_id.pop(chat_id, None)
                swipe_base_text.pop(chat_id, None)
                swipe_enabled_by.pop(chat_id, None)
                return

            target_user_id = swipe_target_user_id.get(chat_id)
            if target_user_id is None:
                return

            if user_id == target_user_id:
                try:
                    base_text = swipe_base_text.get(chat_id, "")
                    raid_text = random.choice(RAID_TEXTS)
                    reply_text = f"{base_text} {raid_text}"
                    await message.reply_text(stylize(reply_text))
                    logging.debug(f"Swipe triggered for target user {target_user_id}")
                except Exception as swipe_error:
                    logging.debug(f"Swipe reply error in {chat_id}: {swipe_error}")
        except Exception as swipe_process_error:
            logging.debug(f"Swipe processing error: {swipe_process_error}")
        return

    await asyncio.sleep(0)

async def bot_watchdog():
    """
    Lightweight watchdog that monitors bot health periodically.
    Logs critical info but does NOT spam.
    Runs in background and does not crash the bot.
    """
    global shutdown_event
    last_log_time = {}
    min_log_interval = 60  # Only log once per minute per event
    
    try:
        while not shutdown_event.is_set():
            try:
                current_time = time.time()
                
                # Check NC tasks
                if nc_tasks:
                    total_nc = sum(len(tasks) for tasks in nc_tasks.values())
                    event_key = f"nc_tasks_{total_nc}"
                    if event_key not in last_log_time or current_time - last_log_time[event_key] > min_log_interval:
                        logging.info(f"📊 NC tasks running: {total_nc}")
                        last_log_time[event_key] = current_time
                
                # Check spam tasks
                if spam_tasks:
                    event_key = "spam_tasks"
                    if event_key not in last_log_time or current_time - last_log_time[event_key] > min_log_interval:
                        logging.info(f"📊 Spam tasks running: {len(spam_tasks)}")
                        last_log_time[event_key] = current_time
                
                # Yield to prevent event loop starvation
                await asyncio.sleep(0)
                
                # Check every 10 seconds
                await asyncio.sleep(10)
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.debug(f"Watchdog check error (non-critical): {e}")
                await asyncio.sleep(0)
    
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.debug(f"Watchdog error (non-critical): {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler - prevents any exception from crashing the bot."""
    try:
        message = update.effective_message if update is not None else None
        user = update.effective_user if update is not None else None
        chat = update.effective_chat if update is not None else None
        if (
            message is not None
            and user is not None
            and chat is not None
            and ig_module.has_login_prompt(chat.id, user.id)
            and message.text
            and not message.text.startswith(("~", "†"))
        ):
            try:
                await message.delete()
            except Exception:
                pass
        error_type = type(context.error).__name__ if context.error else "UnknownError"
        update_id = getattr(update, "update_id", "unknown")
        logging.error(
            "Update processing failed (update_id=%s, error=%s).",
            update_id,
            error_type,
        )
    except Exception as e:
        logging.error(
            "Error in error handler itself (%s).", type(e).__name__
        )


async def instagram_text_upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ig_module.handle_text_upload(update, context, is_authorized)


class InstagramLoginInputFilter(filters.UpdateFilter):
    def filter(self, update: Update) -> bool:
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        return bool(
            message
            and message.text
            and not message.text.startswith(("~", "†", "/"))
            and user
            and chat
            and ig_module.has_pending_interaction(chat.id, user.id)
        )


async def instagram_login_input_handler(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    try:
        await ig_module.handle_pending_input(update, is_authorized)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logging.warning(
            "Instagram login input failed (%s).", type(error).__name__
        )
        message = update.effective_message
        chat = update.effective_chat
        if message is not None:
            try:
                await message.delete()
            except Exception:
                pass
        if chat is not None:
            try:
                await chat.send_message(
                    "The Instagram login input could not be processed. "
                    "No account was registered."
                )
            except Exception:
                pass
    raise ApplicationHandlerStop


def build_app(token):
    try:
        app = Application.builder().token(token).build()
    except InvalidToken as e:
        logging.error("Invalid token skipped during app build.")
        return None
    except Exception as e:
        logging.error("Failed building app for token (%s).", type(e).__name__)
        return None
    
    # Add global error handler to catch any unhandled exceptions
    app.add_error_handler(error_handler)
    
    # CRITICAL: Handler groups ensure proper execution order
    # Capture pending sensitive login input before ordinary message handlers.
    app.add_handler(
        MessageHandler(
            InstagramLoginInputFilter(), instagram_login_input_handler
        ),
        group=-1,
    )

    # Group 0 (HIGHEST PRIORITY): Strict anti-spam must run before everything else
    app.add_handler(MessageHandler(filters.ALL, strict_anti_spam_handler), group=0)
    app.add_handler(MessageHandler(filters.ALL, reaction_handler), group=0)
    
    # Group 1: Command handlers for root and ~ commands
    app.add_handler(CommandHandler("cancel", cancel_cmd), group=1)
    app.add_handler(MessageHandler(filters.Regex(r"^†\w"), silent_command_handler), group=1)
    app.add_handler(MessageHandler(filters.Regex(r"^~\w"), universal_command_handler), group=1)
    
    # Group 2: Auto replies for non-command messages
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT, help_selection_handler), group=2)
    app.add_handler(MessageHandler(filters.Regex(r"^[1234]$"), help_selection_handler), group=2)
    app.add_handler(MessageHandler(filters.Document.ALL, instagram_text_upload_handler), group=2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.Regex(r"^[~†]\w") & ~filters.Regex(r"^[1234]$"), auto_replies), group=2)
    
    return app

async def initialize_my_bot_ids():
    """
    Extract and populate my_bot_ids and my_bot_usernames from the running bot instances.
    This allows title-change detection to distinguish between our bots and others.
    """
    global my_bot_ids, my_bot_usernames
    my_bot_ids.clear()
    my_bot_usernames.clear()
    
    if not bots:
        logging.warning("⚠️ No bots in bots list - my_bot_ids will be empty!")
        return
    
    for i, bot in enumerate(bots):
        try:
            bot_user = None
            bot_id = getattr(bot, "id", None)
            bot_username = getattr(bot, "username", None)

            if not bot_id or not bot_username:
                bot_user = await bot.get_me()
                bot_id = bot_user.id
                bot_username = bot_user.username

            if bot_id:
                my_bot_ids.add(bot_id)
            if bot_username:
                my_bot_usernames.add(bot_username.lower())

            debug_msg = f"  Bot #{i+1}:"
            if bot_id:
                debug_msg += f" ID {bot_id}"
            if bot_username:
                debug_msg += f" USERNAME @{bot_username}"
            logging.debug(debug_msg)
        except Exception as e:
            logging.warning(f"Failed to get bot ID or username from bot #{i+1}: {type(e).__name__}: {e}")
    
    if my_bot_ids:
        logging.info(f"✅ Bot ID initialization COMPLETE: {len(my_bot_ids)} bots")
        logging.info(f"   my_bot_ids = {sorted(my_bot_ids)}")
    else:
        logging.error("❌ my_bot_ids is EMPTY - title change detection will not work!")

async def cleanup_voice_cache():
    """Clean up voice cache on startup (memory optimization)."""
    global voice_cache
    try:
        voice_cache.clear()
        logging.info("Voice cache cleared")
    except Exception as e:
        logging.warning(f"Error clearing voice cache: {e}")

async def run_all_bots():
    """
    Main bot runner with proper lifecycle management and hardening.
    - Initializes shutdown_event
    - Starts all bot applications
    - Runs watchdog for health monitoring
    - Handles graceful shutdown on signal
    - Prevents crashes from propagating
    """
    global apps, bots, shutdown_event
    
    # Initialize shutdown event
    shutdown_event = asyncio.Event()
    
    # Clean up voice cache on startup (memory optimization)
    await cleanup_voice_cache()

    # Load persisted feature state before starting bots
    load_state()
    
    # Deduplicate tokens while preserving order
    seen = set()
    unique_tokens = []
    for t in TOKENS:
        if t and t not in seen:
            seen.add(t)
            unique_tokens.append(t)

    # Build all applications and skip tokens that fail validation
    build_failures = 0
    invalid_tokens = []
    build_results = []
    for token in unique_tokens:
        app = build_app(token)
        if app is None:
            build_failures += 1
            invalid_tokens.append(token)
            continue
        build_results.append((token, app))

    if invalid_tokens:
        logging.warning(f"⚠️ {len(invalid_tokens)} invalid or failed tokens skipped.")

    if build_failures > 0:
        logging.warning(f"⚠️ {build_failures} app build failures - continuing with {len(build_results)} valid app definitions")

    # Initialize and start all applications
    startup_failures = 0
    active_apps = []
    active_bots = []

    for token, app in build_results:
        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling()
            active_apps.append(app)
            active_bots.append(app.bot)
            logging.info("✅ Bot started successfully.")
            await asyncio.sleep(0.5)  # Stagger startup to avoid conflicts
        except InvalidToken as e:
            logging.error("Invalid token skipped during startup.")
            startup_failures += 1
        except telegram_error.Unauthorized as e:
            logging.error("Unauthorized token skipped during startup.")
            startup_failures += 1
        except Exception as e:
            logging.error("Failed starting app (%s).", type(e).__name__)
            startup_failures += 1

    # Replace apps and bots lists with only successfully started instances
    apps = active_apps
    bots = active_bots

    if not apps:
        logging.error("❌ No bots could be started!")
        return

    logging.info(f"🚀 lofi Bot is running ({len(apps)} bots active, {startup_failures} startup failures).")

    # Initialize bot IDs for title change detection
    await initialize_my_bot_ids()

    # Restore saved state and resume loops from previous session
    await restore_saved_state()
    
    # Start watchdog task in background
    watchdog_task = asyncio.create_task(bot_watchdog())
    # Start periodic state persistence
    global state_save_task
    state_save_task = asyncio.create_task(_state_persistence_loop())
    
    try:
        # Keep the bot running until shutdown signal
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        # Cancel background helper tasks
        for task in (watchdog_task, state_save_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

        # Ensure graceful shutdown happens
        await graceful_shutdown(apps)

async def main():
    """
    Main entry point using asyncio.run().
    Properly handles KeyboardInterrupt and cleanup.
    Global exception safety wrapper.
    """
    try:
        await run_all_bots()
    except KeyboardInterrupt:
        logging.info("\n🛑 Keyboard interrupt received - shutting down cleanly...")
    except Exception as e:
        logging.error(f"🚨 Fatal error in main: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    # Use asyncio.run() for proper event loop lifecycle management
    try:
        while True:
            try:
                asyncio.run(main())
                break
            except KeyboardInterrupt:
                logging.info("\n🛑 Bot stopped by user")
                break
            except Exception as e:
                logging.error(f"🚨 Fatal error: {e}", exc_info=True)
                try:
                    save_state()
                except Exception as save_err:
                    logging.error(f"Failed to save state after crash: {save_err}")
                logging.info("🔁 Restarting bot in 5 seconds...")
                time.sleep(5)
                continue
    finally:
        logging.info("✅ Bot shutdown complete")