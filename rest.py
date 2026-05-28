# uncompyle6 version 3.9.3
# Python bytecode version base 3.2 (3180)
# Decompiled from: Python 3.14.5 (main, May 10 2026, 18:26:20) [GCC 16.1.1 20260430]
# Embedded file name: ./services/rest.py
# Compiled at: 2015-04-16 13:39:10
import os, bottle, json, logging, hashlib, traceback, time, itertools, sqlite3, random
from datetime import datetime
import functools, re, threading, pickle, gc, inspect
from pprint import pformat
from threading import Lock
from pim.internal import calendar, message, contact, analytics, account, profile, task, call, memo, linking, tag, social, focalpoint, bblink, priorityinbox, vvm
from pim.internal.account import get_account
from pim.environment import logging_config
from pim.exception import UncaughtPimRestExceptionWrapper, AccessDenied, EnterprisePerimeterHardLocked, MissingDataError, PIMException, exc_to_json, DomainAccessDenied, FunctionAccessDenied
from pim.exception.account import AccountPermissionError, ProviderNotFound
from pim.exception.calendar import CalendarInvalidMeetingError, IcsParseException
from pim.exception.contact import ContactRequestedSizeTooLarge
from pim.exception.http import HttpMethodNotAllowed, HttpRouteGoneError
from pim.exception.rest import AnchoredQueryNoAccounts
from pim.objects.static import Account, Defaults
from pim.objects.encoder import PIMEncoder
from pim.services.wsgi import WSGIServerBottle
from pim.services.session import SessionManagementService
from pim.services.settings import SettingsMonitorService
from pim.services.localization import LocaleService
from pim.services.notifications import NotificationMgmtService
from pim.services.unifiedcontacts import UnifiedContactsService
from pim.providers.contactenhancement.service import queue_enhancements, is_enhancement_enabled, enable_enhancement
from pim.services import base
from pim.providers.ProviderNotification import NotifyType
from pim.utils.counters.rest_counters import RestCounter
from pim.utils.obfuscate import obfuscate_rest_path_pin
from pim.configuration import REST_HOST, REST_PORT, REST_PORT_ENTERPRISE, REST_VERSION, PPS_EMULATION, DOMAIN_CATEGORIES, DOMAIN_ACCOUNTS, DOMAIN_SUBTYPE_MANAGE_ACCOUNTS, ACCOUNT_TYPES, PIM_API_LOGGING_ENABLED, DEBUG, PIM_REST_PROFILING_ENABLED, DOMAIN_CALENDAR, DOMAIN_CONTACTS, PERSONAL_ROOT, ENTERPRISE_ROOT, PIM_YAPPI_PROFILER_ENABLED, PIM_REST_FULL_LOGGING_ENABLED, PIM_REST_YAPPI_PROFILING_ENABLED, PIM_REST_QUIP_ENABLED, PIM_DISABLE_BOOT_BOOST
import pim.internal.settings
from pim.utils.message.messaging import remove_friendly_names, USER_ACTION_REPLY, USER_ACTION_FORWARD
from pim.utils.accounts.aab_registration import get_aab_terms_and_version, getSourceAddress, aab_already_registered
from pim.utils.system.heap import write_memusage
from pim.perimeter import enterprise
from email.utils import formataddr, parseaddr
from pim.utils.contact import news_fetch
from pim.utils.secureemail.attributes import EncodingType, EncodingAction
from pim.utils.secureemail.utils import convert_message_type, import_cert, view_cert, get_secureemail_options, set_secureemail_options, get_account_info, secure_email_api_access_check, is_secureemail_supported, remove_secure_attachments_from_secure_messages, certs_ldap_request, certs_ocsp_request, certs_clean_cache_request
from pim.utils.secureemail.secureemailinit import initialize_secure_email
from pim.objects.conversation import count_conversations_by_account_id
from qnx.pps import PpsFile
from pim.objects.constants import EnhancementType, DatabaseContextTypes, PriorityInboxEntityType, PriorityInboxFeedbackType, PriorityInboxFeedbackField, PriorityInboxResponseTimeType, PriorityInboxFeedbackCurrentStates
from pim.utils.perflog import PerformanceLogger
import pim.utils.prof
from pim.services.accountids import LOCAL_BBM_ACCOUNTID, LOCAL_PINMESSAGES_ACCOUNTID, UNIFIED_CONTACTS_ACCOUNTID, LOCAL_CONTACTS_ACCOUNTID, LOCAL_SIMCONTACTS_ACCOUNTID
import urllib.request
import urllib.parse
import string
from pim.configuration.default import PERSONAL_SHARED_MEDIA_ROOT, PERSONAL_SHAREWITH_ROOT, ENTERPRISE_SHARED_MEDIA_ROOT, ENTERPRISE_SHAREWITH_ROOT, ENTERPRISE_SHARED_MEDIA_ROOT_DIRECT, PIM_BOOT_BOOST_APPS, ENTERPRISE_SHAREWITH_ROOT_DIRECT
from pim.utils.secureemail.configuration import SMIME_supported
from pim.utils.calendar.common import anonymize_data
from pim.objects.providers.navigator import NavigatorListener, IDLE_MODE
from pim.utils.thread.qnxthreadname import elevated_prio, reduced_prio, nominal_prio, HIGH_THREAD_PRIORITY, HIGH_THREAD_PRIORITY_STRING, NOMINAL_THREAD_PRIORITY_STRING, LOW_THREAD_PRIORITY_STRING
from pim.services.settings.properties import LocaleSettingsProperties
_navigator_listener = NavigatorListener(elevate_priority=True) if not PPS_EMULATION else None
if SMIME_supported:
    from pim.utils.secureemail.smime.smime_constants import SMIME_ATTACHMENT_EXTENDED_NAMES, SMIME_ATTACHMENT_CONTENT_TYPES
from pim.utils import oauth2
from qnx.traceevent import ktrace
from pim.utils.prof import KernelTraceEventCodes
REMOVE_PUNCTUATIONS = '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~'
remove_phone_tokens_re = re.compile("[%s]" % re.escape(REMOVE_PUNCTUATIONS + "-" + string.whitespace))
logger = logging.getLogger("rest")
_PIN_RELAY_PROVIDER_REF = None
_PIN_RELAY_ACCOUNT_REF = None
_PIN_RELAY_PROVIDER_LOGGED = False
_PIN_RELAY_CLIENT_SESSION_REF = None

def _get_pin_relay_sender(fallback=None):
    if fallback:
        return fallback
    env_pin = os.environ.get("PIN_RELAY_FROM_PIN")
    if env_pin:
        return env_pin
    try:
        from pim.providers.pin.PINProvider import PINProvider
        device_pin = PINProvider.get_device_pin()
        if device_pin:
            return device_pin
    except Exception as e:
        logger.error("Hook PIM: Failed to get device PIN from PINProvider: %s", e)
    return os.environ.get("PIN_RELAY_FALLBACK_PIN", "2BBF52EB")

def _get_pin_relay_body():
    body = bottle.request.POST.get("body", default="")
    if body and body.strip():
        return body
    body_plain_text = bottle.request.POST.get("body_plaintext", default=None)
    if body_plain_text:
        try:
            parsed = json.loads(body_plain_text)
            if parsed:
                return parsed
        except:
            return body_plain_text
    full_body = bottle.request.POST.get("full_body", default=None)
    if full_body:
        return full_body
    return body

def _get_pin_relay_subject():
    subject = bottle.request.POST.get("subject", default="")
    if subject is None:
        return ""
    return subject.replace("\r\n", "\r").replace("\r", "\r\n")

def _get_pin_relay_priority():
    for key in ("priority", "importance"):
        try:
            value = bottle.request.POST.get(key, default=None)
            if value is not None and value != "":
                return int(json.loads(value)) if value[0] in ("\"", "[", "{") else int(value)
        except:
            pass
    try:
        options = json.loads(bottle.request.POST.get("options", default="{}"))
        for key in ("priority", "importance"):
            if key in options:
                return int(options[key])
    except:
        pass
    return 1

def _get_pin_relay_reply_to(orig_msg_id):
    try:
        if not orig_msg_id or str(orig_msg_id) == "0":
            return []
        db_path = os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db")
        db = sqlite3.connect(db_path, timeout=10)
        db.create_collation("en_US", _pin_relay_collation)
        cursor = db.cursor()
        cursor.execute("SELECT from_address FROM Message WHERE id = ?", (int(orig_msg_id),))
        row = cursor.fetchone()
        db.close()
        if row and row[0]:
            return [row[0]]
    except Exception as e:
        _pin_relay_debug("reply recipient lookup failed orig_msg_id=%s err=%s" % (orig_msg_id, e))
    return []

def _pin_relay_http_json(url, payload=None, timeout=10):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    response = urllib.request.urlopen(req, timeout=timeout)
    body = response.read()
    if not body:
        return None
    return json.loads(body.decode("utf-8"))

def _pin_relay_read_receipt_state_path():
    return os.environ.get("PIN_RELAY_READ_RECEIPTS_PATH", "/tmp/pin_relay_read_receipts.json")

def _pin_relay_load_read_receipts():
    try:
        path = _pin_relay_read_receipt_state_path()
        if not os.path.exists(path):
            return {}
        f = open(path, "r")
        data = json.loads(f.read() or "{}")
        f.close()
        return data
    except:
        return {}

def _pin_relay_save_read_receipts(data):
    try:
        f = open(_pin_relay_read_receipt_state_path(), "w")
        f.write(json.dumps(data))
        f.close()
    except Exception as e:
        _pin_relay_debug("read receipt state save failed %s" % e)

def _pin_relay_send_read_receipts_once():
    local_pin = _get_pin_relay_sender()
    relay_base_url = os.environ.get("PIN_RELAY_BASE_URL", "http://10.58.53.142:8080").rstrip("/")
    sent = _pin_relay_load_read_receipts()
    changed = False
    db_path = os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db")
    db = sqlite3.connect(db_path, timeout=10)
    db.create_collation("en_US", _pin_relay_collation)
    cursor = db.cursor()
    cursor.execute("SELECT sync_id FROM Message WHERE sync_id IS NOT NULL AND read_flag = 1 AND IFNULL(from_address, '') != ?", (local_pin,))
    rows = cursor.fetchall()
    db.close()
    for row in rows:
        sync_id = row[0]
        if not sync_id or sent.get(sync_id):
            continue
        try:
            relay_id = int(str(sync_id).replace("pinrelay-", "", 1))
            _pin_relay_http_json(relay_base_url + "/receipt", {"pin": local_pin, "id": relay_id, "type": "read"}, timeout=10)
            sent[sync_id] = int(time.time())
            changed = True
            _pin_relay_debug("read receipt ok sync_id=%s id=%s" % (sync_id, relay_id))
        except Exception as e:
            _pin_relay_debug("read receipt failed sync_id=%s err=%s" % (sync_id, e))
    if changed:
        _pin_relay_save_read_receipts(sent)

class _PinRelayNotifyMessage(object):
    def __init__(self, message_id, conversation_id, folder_id):
        self.id = message_id
        self.conversation_id = conversation_id
        self.folder_id = folder_id

def _pin_relay_debug(text):
    try:
        f = open("/tmp/pin_relay_receive.log", "a")
        f.write("%s %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text))
        f.close()
    except:
        pass

def _pin_relay_collation(left, right):
    if left is None:
        left = ""
    if right is None:
        right = ""
    left = left.lower()
    right = right.lower()
    if left < right:
        return -1
    if left > right:
        return 1
    return 0

def _store_pin_relay_body(message_id, body):
    body_dir = os.environ.get("PIN_RELAY_BODY_DIR", "/accounts/1000/pimdata/_startup_data/messages/199/4/0")
    if not os.path.isdir(body_dir):
        os.makedirs(body_dir)
    digest = hashlib.md5(("%s-%s-%s" % (message_id, time.time(), body)).encode("utf-8")).hexdigest()[:8]
    body_path = os.path.join(body_dir, "msg-%s" % digest)
    f = open(body_path, "wb")
    f.write(body.encode("utf-8"))
    f.close()
    return body_path

def _insert_pin_relay_incoming_message(msg):
    _pin_relay_debug("insert start id=%s from=%s body=%r" % (msg.get("id"), msg.get("from"), msg.get("body")))
    db_path = os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db")
    db = sqlite3.connect(db_path, timeout=10)
    db.create_collation("en_US", _pin_relay_collation)
    cursor = db.cursor()
    sync_id = "pinrelay-%s" % msg.get("id")
    cursor.execute("SELECT id FROM Message WHERE sync_id = ?", (sync_id,))
    row = cursor.fetchone()
    if row:
        db.close()
        _pin_relay_debug("insert skipped duplicate sync_id=%s" % sync_id)
        return None
    cursor.execute("SELECT id FROM MessageFolder WHERE type = 1 LIMIT 1")
    row = cursor.fetchone()
    inbox_folder_id = row[0] if row else 1
    cursor.execute("INSERT INTO MessageConversation (sync_id, is_priority_inbox_auto, is_priority_inbox_user, ui_capabilities) VALUES (?, 0, 0, 0)", (sync_id,))
    conversation_id = cursor.lastrowid
    body = msg.get("body") or ""
    from_pin = msg.get("from") or ""
    title = msg.get("subject") or ""
    priority = int(msg["priority"]) if "priority" in msg and msg.get("priority") is not None else 1
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
    cursor.execute("INSERT INTO Message (folder_id, conversation_id, status, title, sync_id, sync_version, sync_dirty, deleted, date_received, date_sent, size, from_address, read_flag, flagged_flag, color, priority, has_attachments, message_class, is_remote_search_result, is_priority_inbox_auto, is_priority_inbox_user, ui_capabilities, message_type, hidden, attributes, preview) VALUES (?, ?, 0, ?, ?, ?, 0, 0, ?, ?, ?, ?, 0, 0, 0, ?, 0, 0, 0, 0, 0, 0, ?, 0, 0, ?)", (inbox_folder_id, conversation_id, title, sync_id, sync_id, now, now, len(body), from_pin, priority, "application/vnd.blackberry.pin", body))
    message_id = cursor.lastrowid
    body_path = _store_pin_relay_body(message_id, body)
    cursor.execute("UPDATE Message SET text_body_filename = ?, text_body_content_type = ? WHERE id = ?", (body_path, "text/plain; charset=utf-8", message_id))
    cursor.execute("INSERT INTO MessageRecipient (address, type, message_id) VALUES (?, 'to', ?)", (_get_pin_relay_sender(), message_id))
    db.commit()
    db.close()
    _pin_relay_debug("insert ok message_id=%s conversation_id=%s folder_id=%s" % (message_id, conversation_id, inbox_folder_id))
    return (message_id, conversation_id, inbox_folder_id)

def _notify_pin_relay_incoming(message_id, conversation_id, folder_id):
    _pin_relay_debug("notify start message_id=%s conversation_id=%s folder_id=%s" % (message_id, conversation_id, folder_id))
    try:
        from pim.providers.ProviderNotification import ProviderNotification
        notify_obj = ProviderNotification(LOCAL_PINMESSAGES_ACCOUNTID)
        notify_obj.notify_message_new(_PinRelayNotifyMessage(message_id, conversation_id, folder_id))
        try:
            notify_obj.close()
        except:
            pass
        _pin_relay_debug("notify native ok")
        return
    except Exception as e:
        _pin_relay_debug("notify native failed %s" % e)
        logger.error("Hook PIM: Native notify_message_new failed: %s", e)
    try:
        pps = PpsFile("/pps/services/pim/status", "w")
        pps.write({"account_id": LOCAL_PINMESSAGES_ACCOUNTID, "data": json.dumps([[message_id, conversation_id, folder_id]]), "name": "message_new", "type": "messages"})
        pps.close()
        _pin_relay_debug("notify pps ok")
    except Exception as e:
        _pin_relay_debug("notify pps failed %s" % e)
        logger.error("Hook PIM: PPS message_new failed: %s", e)

def _notify_pin_relay_message_updated(message_id, conversation_id, folder_id):
    _pin_relay_debug("notify update start message_id=%s conversation_id=%s folder_id=%s" % (message_id, conversation_id, folder_id))
    try:
        from pim.providers.ProviderNotification import ProviderNotification
        notify_obj = ProviderNotification(LOCAL_PINMESSAGES_ACCOUNTID)
        methods = []
        try:
            methods = [name for name in dir(notify_obj) if "message" in name.lower() or "conversation" in name.lower() or "update" in name.lower()]
            _pin_relay_debug("ProviderNotification methods=%s" % methods)
        except:
            pass
        current_status = 0
        current_folder_id = folder_id
        try:
            db_path = os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db")
            db = sqlite3.connect(db_path, timeout=10)
            db.create_collation("en_US", _pin_relay_collation)
            cursor = db.cursor()
            cursor.execute("SELECT status, folder_id FROM Message WHERE id = ?", (message_id,))
            row = cursor.fetchone()
            db.close()
            if row:
                current_status = row[0]
                current_folder_id = row[1]
        except Exception as e:
            _pin_relay_debug("notify update status lookup failed %s" % e)
        changes = {"status": current_status, "folder_id": current_folder_id}
        try:
            notify_obj.notify_message_changed(message_id, conversation_id, current_folder_id, changes=changes, from_UI=False)
            _pin_relay_debug("notify update native ok method=notify_message_changed")
        except Exception as e:
            _pin_relay_debug("notify update native method=notify_message_changed failed %s" % e)
        if int(current_status) in (102, 103):
            try:
                notify_obj.close()
            except:
                pass
            return
        if int(current_status) not in (102, 103):
            try:
                notify_obj.notify_message_sent(_PinRelayNotifyMessage(message_id, conversation_id, current_folder_id))
                _pin_relay_debug("notify update native ok method=notify_message_sent")
                try:
                    notify_obj.close()
                except:
                    pass
                return
            except Exception as e:
                _pin_relay_debug("notify update native method=notify_message_sent failed %s" % e)
        msg = _PinRelayNotifyMessage(message_id, conversation_id, folder_id)
        for method_name in ("notify_message_updated", "notify_message_update", "notify_messages_bulk_changed", "notify_message_new"):
            method = getattr(notify_obj, method_name, None)
            if method:
                try:
                    method(msg)
                    _pin_relay_debug("notify update native ok method=%s" % method_name)
                    try:
                        notify_obj.close()
                    except:
                        pass
                    return
                except Exception as e:
                    _pin_relay_debug("notify update native method=%s failed %s" % (method_name, e))
        try:
            notify_obj.close()
        except:
            pass
    except Exception as e:
        _pin_relay_debug("notify update native failed %s" % e)
    try:
        pps = PpsFile("/pps/services/pim/status", "w")
        pps.write({"account_id": LOCAL_PINMESSAGES_ACCOUNTID, "data": json.dumps([[message_id, conversation_id, folder_id]]), "name": "message_update", "type": "messages"})
        pps.close()
        _pin_relay_debug("notify update pps ok")
    except Exception as e:
        _pin_relay_debug("notify update pps failed %s" % e)

def _pin_relay_register_provider(account_obj):
    global _PIN_RELAY_PROVIDER_REF
    global _PIN_RELAY_ACCOUNT_REF
    global _PIN_RELAY_PROVIDER_LOGGED
    if account_obj is None:
        return
    _PIN_RELAY_ACCOUNT_REF = account_obj
    provider_obj = None
    for provider_attr in ("invokeable_provider", "external_provider", "provider"):
        provider_candidate = getattr(account_obj, provider_attr, None)
        if provider_candidate is not None and provider_candidate is not False:
            provider_obj = provider_candidate
            break
    if provider_obj is None:
        provider_obj = account_obj
    try:
        has_status_method = (
            hasattr(provider_obj, "pin_message_send_status_update") or
            hasattr(provider_obj, "message_send_status_update") or
            hasattr(provider_obj, "send_status_update")
        )
    except:
        has_status_method = False
    if has_status_method:
        _PIN_RELAY_PROVIDER_REF = provider_obj
    if not _PIN_RELAY_PROVIDER_LOGGED:
        _PIN_RELAY_PROVIDER_LOGGED = True
        try:
            account_methods = [name for name in dir(account_obj) if "pin" in name.lower() or "status" in name.lower() or "provider" in name.lower() or "notify" in name.lower() or "listener" in name.lower() or "session" in name.lower()]
        except:
            account_methods = []
        try:
            provider_methods = [name for name in dir(provider_obj) if "pin" in name.lower() or "status" in name.lower() or "listener" in name.lower() or "notify" in name.lower()]
        except:
            provider_methods = []
        for extra_attr in ("invokeable_provider", "external_provider", "provider"):
            try:
                extra_obj = getattr(account_obj, extra_attr, None)
                if extra_obj is not None:
                    extra_methods = [name for name in dir(extra_obj) if "pin" in name.lower() or "status" in name.lower() or "listener" in name.lower() or "notify" in name.lower() or "session" in name.lower()]
                    _pin_relay_debug("registered extra attr=%s obj=%s methods=%s" % (extra_attr, extra_obj, extra_methods))
            except Exception as e:
                _pin_relay_debug("registered extra attr=%s failed %s" % (extra_attr, e))
        _pin_relay_debug("registered account methods=%s" % account_methods)
        _pin_relay_debug("registered provider=%s methods=%s" % (provider_obj, provider_methods))

def _pin_relay_register_client_session(client_session):
    global _PIN_RELAY_CLIENT_SESSION_REF
    session_obj = client_session
    _PIN_RELAY_CLIENT_SESSION_REF = session_obj
    try:
        session_methods = [name for name in dir(session_obj) if "query" in name.lower() or "session" in name.lower() or "get" in name.lower() or "open" in name.lower()]
    except:
        session_methods = []
    _pin_relay_debug("registered client session obj=%s methods=%s" % (session_obj, session_methods))

def _pin_relay_call_original_like_orm_update(sync_id, delivered):
    if not delivered or _PIN_RELAY_CLIENT_SESSION_REF is None:
        return False
    try:
        from pim.objects.orm import Message
        from pim.objects.providers.message import MessageStatusEnum
        session = _PIN_RELAY_CLIENT_SESSION_REF
        close_session = False
        session_context = None
        if hasattr(session, "open_session"):
            session_context = session.open_session(LOCAL_PINMESSAGES_ACCOUNTID)
            close_session = True
            try:
                session = session_context.__enter__()
            except:
                session = session_context
        elif hasattr(session, "get_session"):
            try:
                session = session.get_session(LOCAL_PINMESSAGES_ACCOUNTID)
            except TypeError:
                session = session.get_session()
        elif not hasattr(session, "query"):
            _pin_relay_debug("original-like orm update no query session obj=%s" % session)
            return False
        msg = session.query(Message).filter_by(sync_id=sync_id).first()
        if msg is None and isinstance(sync_id, str) and sync_id.startswith("pinrelay-"):
            msg = session.query(Message).filter_by(sync_id=sync_id.replace("pinrelay-", "", 1)).first()
        if msg is None:
            _pin_relay_debug("original-like orm update message not found sync_id=%s" % sync_id)
            return False
        sent_folder_id = None
        try:
            sent_folder_id = _PIN_RELAY_PROVIDER_REF.pinSentFolderId
        except:
            pass
        if sent_folder_id is None:
            try:
                db_path = os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db")
                db = sqlite3.connect(db_path, timeout=10)
                cursor = db.cursor()
                cursor.execute("SELECT id FROM MessageFolder WHERE type = 2 LIMIT 1")
                row = cursor.fetchone()
                db.close()
                sent_folder_id = row[0] if row else 3
            except:
                sent_folder_id = 3
        msg.status = MessageStatusEnum.SENT
        msg.folder_id = sent_folder_id
        try:
            msg.status_description = ""
        except:
            pass
        try:
            msg.sync_dirty = False
        except:
            pass
        session.add(msg)
        session.commit()
        msg_id = msg.id
        msg_conversation_id = msg.conversation_id
        msg_folder_id = msg.folder_id
        msg_status = msg.status
        if close_session:
            try:
                session_context.__exit__(None, None, None)
            except:
                pass
        _pin_relay_debug("original-like orm update ok sync_id=%s msg_id=%s status=%s folder_id=%s" % (sync_id, msg_id, msg_status, msg_folder_id))
        try:
            notify_obj = getattr(_PIN_RELAY_ACCOUNT_REF, "notify", None)
            if notify_obj is not None:
                notify_obj.notify_message_changed(msg_id, msg_conversation_id, msg_folder_id, changes={"status": msg_status, "folder_id": msg_folder_id}, from_UI=False)
                _pin_relay_debug("original-like orm notify via account ok")
                return True
        except Exception as e:
            _pin_relay_debug("original-like orm notify via account failed %s" % e)
        _notify_pin_relay_message_updated(msg_id, msg_conversation_id, msg_folder_id)
        return True
    except Exception as e:
        _pin_relay_debug("original-like orm update failed %s" % e)
        return False

def _pin_relay_call_original_status_update(sync_id, delivered, status_override=None):
    global _PIN_RELAY_PROVIDER_REF
    if not delivered and status_override is None:
        return False
    refid = sync_id
    if isinstance(refid, str) and refid.startswith("pinrelay-"):
        refid = refid.replace("pinrelay-", "", 1)
    try:
        refid_value = int(refid)
    except:
        refid_value = refid
    status_value = status_override if status_override is not None else 6
    candidates = []
    if _PIN_RELAY_PROVIDER_REF is not None and _PIN_RELAY_PROVIDER_REF is not False:
        candidates.append(_PIN_RELAY_PROVIDER_REF)
    if _PIN_RELAY_ACCOUNT_REF is not None and _PIN_RELAY_ACCOUNT_REF is not False:
        candidates.append(_PIN_RELAY_ACCOUNT_REF)
    try:
        for obj in gc.get_objects():
            try:
                obj_name = obj.__class__.__name__
                obj_module = getattr(obj.__class__, "__module__", "")
                if "PIN" in obj_name or "pin" in obj_module.lower():
                    if obj not in candidates:
                        candidates.append(obj)
            except:
                pass
    except Exception as e:
        _pin_relay_debug("original status gc scan failed %s" % e)
    if not candidates:
        _pin_relay_debug("original status update skipped no provider refid=%s status=%s" % (refid_value, status_value))
        return False
    _pin_relay_debug("original status candidates=%s" % [(getattr(obj.__class__, "__module__", ""), obj.__class__.__name__) for obj in candidates[:30]])
    for obj in list(candidates):
        for attr in ("pin_message_send_status_update", "message_send_status_update", "send_status_update"):
            method = getattr(obj, attr, None)
            if method:
                try:
                    method(refid_value, status_value)
                    _PIN_RELAY_PROVIDER_REF = obj
                    _pin_relay_debug("original status update ok obj=%s method=%s refid=%s status=%s" % (obj, attr, refid_value, status_value))
                    return True
                except Exception as e:
                    _pin_relay_debug("original status update failed method=%s err=%s" % (attr, e))
        for listener_attr in ("messageStatusListener", "message_status_listener", "statusListener", "status_listener", "pinMessageStatusListener"):
            listener = getattr(obj, listener_attr, None)
            if listener is None:
                continue
            method = getattr(listener, "update_message_status", None)
            if method:
                try:
                    method(refid_value, status_value)
                    _pin_relay_debug("original listener update ok attr=%s refid=%s status=%s" % (listener_attr, refid_value, status_value))
                    return True
                except Exception as e:
                    _pin_relay_debug("original listener update failed attr=%s err=%s" % (listener_attr, e))
    return False

def _pin_relay_merge_provider_data(existing, relay_data):
    data = {}
    if existing:
        try:
            data = json.loads(existing)
            if not isinstance(data, dict):
                data = {}
        except:
            data = {}
    data["pin_relay"] = relay_data
    return json.dumps(data)

def _pin_relay_sync_outgoing_receipts_once():
    local_pin = _get_pin_relay_sender()
    relay_base_url = os.environ.get("PIN_RELAY_BASE_URL", "http://10.58.53.142:8080").rstrip("/")
    db_path = os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db")
    db = sqlite3.connect(db_path, timeout=10)
    db.create_collation("en_US", _pin_relay_collation)
    cursor = db.cursor()
    cursor.execute("SELECT provider_data FROM Message WHERE provider_data LIKE '%pin_relay_backend_id%' ORDER BY id DESC LIMIT 200")
    known_ids = []
    for row in cursor.fetchall():
        try:
            data = json.loads(row[0] or "{}")
            backend_id = data.get("pin_relay_backend_id")
            if backend_id and str(backend_id) not in known_ids:
                known_ids.append(str(backend_id))
        except:
            pass
    if not known_ids:
        _pin_relay_debug("outgoing receipt sync skipped no known backend ids")
        db.close()
        return
    receipts_url = relay_base_url + "/receipts?pin=" + urllib.parse.quote(local_pin) + "&ids=" + urllib.parse.quote(",".join(known_ids))
    _pin_relay_debug("outgoing receipt sync request ids=%s" % ",".join(known_ids))
    receipts = _pin_relay_http_json(receipts_url, timeout=10)
    if not receipts:
        _pin_relay_debug("outgoing receipt sync no receipts ids=%s" % ",".join(known_ids))
        db.close()
        return
    _pin_relay_debug("outgoing receipt sync response count=%s" % len(receipts))
    updated = []
    for item in receipts:
        relay_id = item.get("id")
        receipt_map = item.get("receipts") or {}
        if not relay_id or not receipt_map:
            continue
        if str(relay_id) not in known_ids:
            continue
        delivered_at = None
        read_at = None
        remote_receipts = {}
        for pin, receipt in receipt_map.items():
            if pin == local_pin:
                continue
            remote_receipts[pin] = receipt
            if receipt.get("delivered_at") and not delivered_at:
                delivered_at = receipt.get("delivered_at")
            if receipt.get("read_at") and not read_at:
                read_at = receipt.get("read_at")
        if not delivered_at and not read_at:
            continue
        sync_id = str(relay_id)
        legacy_sync_id = "pinrelay-%s" % relay_id
        cursor.execute("SELECT id, conversation_id, folder_id, provider_data, sync_id FROM Message WHERE provider_data LIKE ? ORDER BY id DESC LIMIT 1", ('%"pin_relay_backend_id": "' + str(relay_id) + '"%',))
        row = cursor.fetchone()
        if not row:
            cursor.execute("SELECT id, conversation_id, folder_id, provider_data, sync_id FROM Message WHERE sync_id IN (?, ?) ORDER BY CASE WHEN sync_id = ? THEN 0 ELSE 1 END, id DESC LIMIT 1", (sync_id, legacy_sync_id, sync_id))
            row = cursor.fetchone()
        if not row:
            continue
        message_id, conversation_id, folder_id, message_provider_data, sync_id = row
        existing_relay_data = {}
        native_refid = sync_id
        if message_provider_data:
            try:
                existing_provider_data = json.loads(message_provider_data)
                if isinstance(existing_provider_data, dict):
                    existing_relay_data = existing_provider_data.get("pin_relay") or {}
                    native_refid = str(existing_provider_data.get("pin_relay_client_refid") or native_refid)
            except:
                existing_relay_data = {}
        was_delivered = bool(existing_relay_data.get("delivered_at"))
        native_delivered_enabled = os.environ.get("PIN_RELAY_ENABLE_NATIVE_DELIVERED_STATUS", "1") == "1"
        relay_data = {"delivered_at": delivered_at if native_delivered_enabled else None, "read_at": read_at, "receipts": remote_receipts}
        if delivered_at and not native_delivered_enabled:
            relay_data["server_delivered_at"] = delivered_at
        status_description = "Read" if read_at else "Delivered"
        original_status_ok = False
        should_send_delivered_status = False
        if os.environ.get("PIN_RELAY_TRY_ORIGINAL_STATUS", "1") == "1":
            should_send_delivered_status = delivered_at is not None and not was_delivered
            if not native_delivered_enabled:
                should_send_delivered_status = False
            if should_send_delivered_status:
                accepted_before_delivered_ok = _pin_relay_call_original_status_update(native_refid, False, status_override=5)
                _pin_relay_debug("outgoing receipt accepted-before-delivered sync_id=%s native_refid=%s ok=%s" % (sync_id, native_refid, accepted_before_delivered_ok))
            original_status_ok = _pin_relay_call_original_status_update(native_refid, should_send_delivered_status)
            if should_send_delivered_status and not original_status_ok and os.environ.get("PIN_RELAY_ENABLE_DELIVERED_FALLBACK", "1") == "1":
                delivered_status = int(os.environ.get("PIN_RELAY_NATIVE_DELIVERED_MESSAGE_STATUS", "102"))
                accepted_status = int(os.environ.get("PIN_RELAY_NATIVE_ACCEPTED_MESSAGE_STATUS", "103"))
                cursor.execute("UPDATE Message SET status = ?, sync_dirty = 0 WHERE id = ?", (accepted_status, message_id))
                db.commit()
                cursor.execute("UPDATE Message SET status = ?, sync_dirty = 0 WHERE id = ?", (delivered_status, message_id))
                _pin_relay_debug("outgoing receipt native delivered fallback sync_id=%s message_id=%s status=%s" % (sync_id, message_id, delivered_status))
                original_status_ok = True
        new_message_provider_data = _pin_relay_merge_provider_data(message_provider_data, relay_data)
        if message_provider_data != new_message_provider_data:
            if os.environ.get("PIN_RELAY_VISIBLE_RECEIPTS", "0") == "1":
                marker = "✓✓ " if read_at else "✓ "
                cursor.execute("UPDATE Message SET provider_data = ?, sync_dirty = 0, preview = CASE WHEN preview LIKE '✓%' THEN preview ELSE ? || IFNULL(preview, '') END WHERE id = ?", (new_message_provider_data, marker, message_id))
            else:
                cursor.execute("UPDATE Message SET provider_data = ?, sync_dirty = 0 WHERE id = ?", (new_message_provider_data, message_id))
            if should_send_delivered_status:
                updated.append((message_id, conversation_id, folder_id, sync_id))
        elif original_status_ok:
            updated.append((message_id, conversation_id, folder_id, sync_id))
        cursor.execute("SELECT id, provider_data FROM MessageRecipient WHERE message_id = ?", (message_id,))
        recipient_rows = cursor.fetchall()
        for recipient_row in recipient_rows:
            recipient_id, provider_data = recipient_row
            new_provider_data = _pin_relay_merge_provider_data(provider_data, relay_data)
            if provider_data != new_provider_data:
                cursor.execute("UPDATE MessageRecipient SET provider_data = ? WHERE id = ?", (new_provider_data, recipient_id))
    if updated:
        db.commit()
    db.close()
    for message_id, conversation_id, folder_id, sync_id in updated:
        _pin_relay_debug("outgoing receipt synced sync_id=%s message_id=%s" % (sync_id, message_id))
        _notify_pin_relay_message_updated(message_id, conversation_id, folder_id)

def _pin_relay_apply_icon_test_once():
    test_path = os.environ.get("PIN_RELAY_ICON_TEST_PATH", "/tmp/pin_relay_icon_test.json")
    if not os.path.exists(test_path):
        return
    try:
        test_file = open(test_path, "r")
        test_data = json.loads(test_file.read())
        test_file.close()
    except Exception as e:
        _pin_relay_debug("icon test read failed %s" % e)
        return
    sync_id = test_data.get("sync_id")
    if not sync_id:
        return
    db_path = os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db")
    db = sqlite3.connect(db_path, timeout=10)
    db.create_collation("en_US", _pin_relay_collation)
    cursor = db.cursor()
    cursor.execute("SELECT id, conversation_id, folder_id, status, attributes, ui_capabilities FROM Message WHERE sync_id = ?", (sync_id,))
    row = cursor.fetchone()
    if not row:
        db.close()
        _pin_relay_debug("icon test message not found sync_id=%s" % sync_id)
        return
    message_id, conversation_id, folder_id, old_status, old_attributes, old_ui_capabilities = row
    status = test_data.get("status", old_status)
    attributes = test_data.get("attributes", old_attributes)
    ui_capabilities = test_data.get("ui_capabilities", old_ui_capabilities)
    status_description = test_data.get("status_description", None)
    new_sync_id = test_data.get("new_sync_id", None)
    if status_description is None:
        if new_sync_id is None:
            cursor.execute("UPDATE Message SET status = ?, attributes = ?, ui_capabilities = ? WHERE id = ?", (status, attributes, ui_capabilities, message_id))
        else:
            cursor.execute("UPDATE Message SET status = ?, attributes = ?, ui_capabilities = ?, sync_id = ?, sync_version = ? WHERE id = ?", (status, attributes, ui_capabilities, new_sync_id, new_sync_id, message_id))
    else:
        if new_sync_id is None:
            cursor.execute("UPDATE Message SET status = ?, attributes = ?, ui_capabilities = ?, status_description = ? WHERE id = ?", (status, attributes, ui_capabilities, status_description, message_id))
        else:
            cursor.execute("UPDATE Message SET status = ?, attributes = ?, ui_capabilities = ?, status_description = ?, sync_id = ?, sync_version = ? WHERE id = ?", (status, attributes, ui_capabilities, status_description, new_sync_id, new_sync_id, message_id))
    db.commit()
    db.close()
    _pin_relay_debug("icon test applied sync_id=%s message_id=%s status=%s attributes=%s ui_capabilities=%s" % (sync_id, message_id, status, attributes, ui_capabilities))
    _notify_pin_relay_message_updated(message_id, conversation_id, folder_id)

def _pin_relay_receive_loop():
    _pin_relay_debug("receive loop start")
    while True:
        try:
            local_pin = _get_pin_relay_sender()
            _pin_relay_debug("poll local_pin=%s" % local_pin)
            if local_pin:
                relay_base_url = os.environ.get("PIN_RELAY_BASE_URL", "http://10.58.53.142:8080")
                messages = _pin_relay_http_json(relay_base_url.rstrip("/") + "/poll?pin=" + urllib.parse.quote(local_pin), timeout=10)
                _pin_relay_debug("poll result count=%s" % (len(messages) if messages else 0))
                if messages:
                    for msg in messages:
                        inserted = _insert_pin_relay_incoming_message(msg)
                        if inserted:
                            _notify_pin_relay_incoming(inserted[0], inserted[1], inserted[2])
                        try:
                            _pin_relay_http_json(relay_base_url.rstrip("/") + "/ack", {"pin": local_pin, "id": msg.get("id")}, timeout=10)
                            _pin_relay_debug("ack ok id=%s" % msg.get("id"))
                        except Exception as e:
                            _pin_relay_debug("ack failed id=%s err=%s" % (msg.get("id"), e))
                            logger.error("Hook PIM: PIN relay ACK failed: %s", e)
        except Exception as e:
            _pin_relay_debug("receive loop failed %s" % e)
            logger.error("Hook PIM: PIN relay receive loop failed: %s", e)
        try:
            _pin_relay_send_read_receipts_once()
        except Exception as e:
            _pin_relay_debug("read receipt loop failed %s" % e)
            logger.error("Hook PIM: PIN relay read receipt loop failed: %s", e)
        try:
            _pin_relay_sync_outgoing_receipts_once()
        except Exception as e:
            _pin_relay_debug("outgoing receipt sync failed %s" % e)
            logger.error("Hook PIM: PIN relay outgoing receipt sync failed: %s", e)
        try:
            _pin_relay_apply_icon_test_once()
        except Exception as e:
            _pin_relay_debug("icon test failed %s" % e)
            logger.error("Hook PIM: PIN relay icon test failed: %s", e)
        time.sleep(float(os.environ.get("PIN_RELAY_POLL_INTERVAL", "5")))

def _start_pin_relay_receive_loop():
    if os.environ.get("PIN_RELAY_RECEIVE_ENABLED", "1") != "1":
        return
    timer = threading.Timer(float(os.environ.get("PIN_RELAY_START_DELAY", "15")), _pin_relay_receive_loop)
    timer.daemon = True
    timer.start()

_start_pin_relay_receive_loop()
if PIM_YAPPI_PROFILER_ENABLED:
    try:
        import yappi
        yappiLock = Lock()
    except:
        logger.exception("Yappi was not found, if you want to enabling profiling, install this dependency by building it in pim/test/yappi")

if PIM_REST_YAPPI_PROFILING_ENABLED or PIM_REST_FULL_LOGGING_ENABLED:
    rest_call_counter = itertools.count(0)
route_cache_file = os.path.join(os.path.dirname(__file__), "rest_routes.p")

def _get_ids_data(data):
    idstring = data.get("ids")
    if not idstring:
        return ("", [])
    ids = {"id" + str(n): int(id) for n, id in enumerate(idstring.split(","))}
    query_string = "(:" + ",:".join(list(ids.keys())) + ")"
    return (query_string, ids)


def _get_search_terms_from_bottle():
    search_terms = []
    search_keys = []
    for search_term in bottle.request.GET.iterallitems():
        logger.debug("search term: %s", search_term)
        search_terms.append(search_term)
        search_keys.append(search_term[0])

    logger.debug("search keys: %s", search_keys)
    return search_terms


_CONVERT_UNICODE_REGEX = re.compile("[\\xc2-\\xf4][\\x80-\\xbf]+")
_CONVERT_UNICODE = lambda m: m.group(0).encode("latin1").decode("utf8")

def _convert_unicode(data):
    return _CONVERT_UNICODE_REGEX.sub(_CONVERT_UNICODE, data)


def _good_file_path(path, is_enterprise):
    filepath = os.path.realpath(path) if not path.startswith("/tmp") else path
    if "/../" not in filepath and (filepath.startswith(PERSONAL_SHARED_MEDIA_ROOT) or filepath.startswith(PERSONAL_SHAREWITH_ROOT) or is_enterprise and (filepath.startswith(ENTERPRISE_SHARED_MEDIA_ROOT) or filepath.startswith(ENTERPRISE_SHAREWITH_ROOT) or filepath.startswith(ENTERPRISE_SHARED_MEDIA_ROOT_DIRECT) or filepath.startswith(ENTERPRISE_SHAREWITH_ROOT_DIRECT)) or filepath.startswith("/tmp/")):
        return True
    return False


def _confirm_good_file_path_on_attachments(account_id):
    attachment_list = json.loads(bottle.request.POST.get("attachments", default="{}"))
    if attachment_list:
        enterprise = get_account_info(account_id, "enterprise")
        for attachment in attachment_list:
            path = attachment.get("filepath", None)
            if path and isinstance(path, str):
                if not _good_file_path(path, enterprise):
                    raise ValueError("filepath not allowed:", path)
                continue

    return


class PIMExeptionHandler:

    def apply(self, callback, context):

        @functools.wraps(callback)
        def wrapper(*args, **kwds):
            try:
                return callback(*args, **kwds)
            except (bottle.HTTPResponse, bottle.HTTPError):
                raise
            except (PIMException, Exception) as e:
                if not isinstance(e, PIMException):
                    try:
                        raise UncaughtPimRestExceptionWrapper from e
                    except UncaughtPimRestExceptionWrapper as temp:
                        e = temp

                header = {"Content-Type": "application/json"}
                if e.user_error:
                    logger.info("PIM user error being passed to UI while executing %s %s\n%s", bottle.request.method, obfuscate_rest_path_pin(bottle.request.path), e)
                else:
                    logger.info("PIM error response (not a bug) on %s %s: %s", bottle.request.method, obfuscate_rest_path_pin(bottle.request.path), e)
                    logger.debug("Exception for previous error:", "".join(traceback.format_exc()))
                raise bottle.HTTPResponse(status=e.status, output=json.dumps(e.to_json(enable_value=True, enable_traceback=DEBUG)), header=header) from e
            except Exception as e:
                header = {"Content-Type": "application/json"}
                logger.exception("Unhandled exception on %s %s", bottle.request.method, obfuscate_rest_path_pin(bottle.request.path))
                raise bottle.HTTPResponse(status=500, output=json.dumps(exc_to_json(e, enable_value=True, enable_traceback=DEBUG)), header=header) from e

        return wrapper


class ETagHandler:

    def apply(self, callback, context):

        @functools.wraps(callback)
        def wrapper(client_session, *args, **kwargs):
            response_body = callback(client_session, *args, **kwargs)
            if bottle.request.method == "GET":
                response_etag = self.generate_etag(response_body)
                if response_etag != None:
                    bottle.response.headers["Etag"] = response_etag
                    if_none_match = bottle.request.headers.get("If-None-Match", None)
                    if if_none_match != None and if_none_match == response_etag:
                        bottle.response.status = 304
                        response_body = "''"
            return response_body

        return wrapper

    def generate_etag(self, response_body):
        etag = None
        if isinstance(response_body, str):
            hash = hashlib.sha1(response_body.encode())
            etag = hash.hexdigest()
        return etag


class CachedMessageResponse:
    MAX_AGE = 2

    def __init__(self, account_id, message_id, json_message):
        self.account_id = account_id
        self.message_id = message_id
        self.json_message = json_message
        self.cached_time = time.time()


class SessionEnforcer(object):
    name = "session"

    def __init__(self, session):
        self.session = session

    def get_domain(self, requestPath):
        domain = None
        for domainItem, domainList in DOMAIN_CATEGORIES.items():
            if self.in_list(requestPath, domainList):
                domain = domainItem
                break

        return domain

    def in_list(self, requestPath, domainList):
        for domainPath in domainList:
            if requestPath.startswith(domainPath):
                return True

        return False

    def apply(self, callback, context):
        args = inspect.getargspec(context["callback"])[0]
        if "client_session" not in args:
            return callback

        @functools.wraps(callback)
        def decorator(*args, **kwargs):
            id = bottle.request.headers.get("Pim-Session") or bottle.request.GET.get("pim-session")
            if id is None:
                raise AccessDenied("No session id provided.")
            version = bottle.request.headers.get("API-Version") or bottle.request.GET.get("api-version")
            if version is None or version != REST_VERSION:
                raise AccessDenied("Invalid API version.")
            domain = self.get_domain(bottle.request.path)
            with self.session.ui(id, domain) as client_session:
                kwargs["client_session"] = client_session
                return callback(*args, **kwargs)
            logger.debug("Done")
            return

        return decorator


class Prioritizer:
    _start_time = time.time()
    _BOOT_BOOST_PERIOD = 0 if PIM_DISABLE_BOOT_BOOST else 180
    _WHITELIST_UIDS = [
     417]
    _WHITELIST_APPNAMES = [
     "sys.pim.email.card",
     "sys.pim.email.composer.card"]

    def apply(self, callback, context):

        @functools.wraps(callback)
        def wrapper(client_session, *args, **kwargs):
            rest_prio = None
            prio = nominal_prio
            try:
                rest_prio = bottle.request.GET.get("rest_prio", None)
            except Exception as e:
                logger.warning("unknown exception encountered when obtaining rest_prio %r", e)

            if rest_prio == HIGH_THREAD_PRIORITY_STRING or rest_prio == "1":
                logger.info("elevating thread prio based on rest_prio")
                prio = elevated_prio
            elif rest_prio is None:
                caller_pid = -1
                caller_uid = -1
                caller_name = None
                try:
                    if client_session.client_session.pid:
                        caller_pid = int(client_session.client_session.pid)
                    if client_session.client_session.user_id:
                        caller_uid = int(client_session.client_session.user_id)
                    if client_session.application_name:
                        caller_name = client_session.application_name.rsplit(".g", 1)[0]
                except (TypeError, AttributeError):
                    logger.warning("error accessing client_session.client_session attributes")
                except ValueError:
                    logger.warning("could not convert client pid or uid to int")
                except Exception as e:
                    logger.warning("unknown exception encountered when obtaining caller pid/uid %r", e)

                if _navigator_listener and _navigator_listener.pps.boosted == caller_pid:
                    logger.info("elevating thread prio - caller pid is currently boosted")
                    prio = elevated_prio
                elif time.time() - Prioritizer._start_time < Prioritizer._BOOT_BOOST_PERIOD and caller_name in PIM_BOOT_BOOST_APPS:
                    logger.info("elevating thread prio of (%s) - within the boot boost period", caller_name)
                    prio = elevated_prio
                elif caller_uid in Prioritizer._WHITELIST_UIDS:
                    logger.info("elevating thread prio - caller uid is whitelisted")
                    prio = elevated_prio
                elif caller_name in Prioritizer._WHITELIST_APPNAMES:
                    logger.info("elevating thread prio - caller %r is whitelisted", caller_name)
                    prio = elevated_prio
            elif rest_prio == NOMINAL_THREAD_PRIORITY_STRING:
                logger.info("nominal thread prio based on rest_prio")
                prio = nominal_prio
            elif rest_prio == LOW_THREAD_PRIORITY_STRING:
                logger.info("reduced thread prio based on rest_prio")
                prio = reduced_prio
            else:
                logger.info("default thread prio")
                prio = nominal_prio
            with prio():
                bottle.request.priority = threading.current_thread().prio
                return callback(client_session, *args, **kwargs)
            return

        return wrapper


class RequestTimer:

    def apply(self, callback, context):

        @functools.wraps(callback)
        def wrapper(*args, **kwargs):
            ApiMonitor.log_request_parameters(bottle)
            bottle.request.priority = None
            qs = bottle.request.query_string
            agent = bottle.request.headers.get("User-Agent")
            start = time.time()
            write_memusage()
            log_desc = callback.__name__ if "rule" not in context else context["rule"]
            r = None
            duration = None
            if PIM_REST_YAPPI_PROFILING_ENABLED or PIM_REST_FULL_LOGGING_ENABLED:
                rest_count = next(rest_call_counter)
                rest_identifier = datetime.now().strftime("%Y-%m-%d-%H:%M:%S")
            try:
                try:
                    logger.info("Start Request '%s %s%s' %s", bottle.request.method, obfuscate_rest_path_pin(bottle.request.path), "?..." if qs else "", " (From %r)" % agent if agent else "")
                    if qs:
                        logger.debug("with query string: %s", qs)
                    base.sqltap_start()
                    if PIM_REST_PROFILING_ENABLED:
                        if profile_key not in base.REST_PROFILE_HISTORIANS:
                            base.REST_PROFILE_HISTORIANS[log_desc] = pim.utils.prof.ProfileHistorian()
                        with pim.utils.prof.profile(name=log_desc, profile_historian=base.REST_PROFILE_HISTORIANS[log_desc]):
                            r = callback(*args, **kwargs)
                    else:
                        if PIM_REST_YAPPI_PROFILING_ENABLED and PIM_YAPPI_PROFILER_ENABLED:
                            with yappiLock:
                                if yappi.is_running():
                                    yappi.stop()
                                yappi.clear_stats()
                                yappi.set_clock_type("WALL")
                                yappi.start()
                                start = time.time()
                                r = callback(*args, **kwargs)
                                duration = time.time() - start
                                title = "REST-CALL[" + str(rest_count) + "]" + bottle.request.path.replace(os.path.sep, "-") + "-" + rest_identifier
                                d = os.path.dirname("/var/tmp/REST/")
                                if not os.path.exists(d):
                                    os.makedirs(d)
                                RestService.dump_profile_stats(title, "/var/tmp/REST/")
                                yappi.stop()
                        else:
                            r = callback(*args, **kwargs)
                        if PIM_API_LOGGING_ENABLED:
                            ApiMonitor.log("Rest-%s-Complete" % bottle.request.method, "%s" % bottle.request.path[1:], pformat(r), show_time=True)
                        filename = bottle.request.method + "-".join(bottle.request.path.split("/"))
                        base.sqltap_report(filename)
                        status = bottle.response.status
                        return r
                except bottle.HTTPResponse as r:
                    status = r.status
                    raise
                except bottle.HTTPError as r:
                    status = r.code
                    raise
                except Exception:
                    status = 500
                    raise

            finally:
                if PIM_REST_YAPPI_PROFILING_ENABLED and PIM_YAPPI_PROFILER_ENABLED:
                    bottle.response.headers["responsetime"] = duration
                    bottle.response.headers["rest_call_number"] = rest_count
                else:
                    duration = time.time() - start
                if not PPS_EMULATION:
                    if bottle.request.priority:
                        PerformanceLogger.log_perf_entry("REST", bottle.request.method + " " + log_desc, duration * 1000, bottle.request.priority)
                write_memusage()
                if PIM_REST_FULL_LOGGING_ENABLED:
                    self.log(bottle, rest_count, rest_identifier, r)
                logger.info("Request '%s %s%s' response %d in %.3fs %s", bottle.request.method, obfuscate_rest_path_pin(bottle.request.path), "?..." if qs else "", status, duration, " (From %r)" % agent if agent else "")
                if PIM_REST_QUIP_ENABLED:
                    RestCounter.instance().update_time(agent, bottle.request.method, log_desc, bottle.request.query_string, bottle.request.priority, duration)

            return

        return wrapper

    def log(self, bottle, rest_count, rest_identifier, r):
        try:
            restpath = bottle.request.path
            filename = restpath.replace(os.path.sep, "-")
            d = os.path.dirname("/var/tmp/REST/")
            if not os.path.exists(d):
                os.makedirs(d)
            filename = "/var/tmp/REST/REST-CALL[" + str(rest_count) + "]"
            filename += bottle.request.path.replace(os.path.sep, "-") + "-" + rest_identifier + ".log"
            fd = open(filename, "wt")
            fd.write("METHOD:\n")
            fd.write(bottle.request.method)
            fd.write("\nREQUEST:\n")
            fd.write(bottle.request.path + "?" + bottle.request.query_string)
            fd.write("\nHEADERS:\n")
            for header in bottle.request.headers:
                fd.write("\t")
                fd.write(header)
                fd.write(":")
                fd.write(bottle.request.headers[header])
                fd.write("\n")

            fd.write("BODY:\n")
            for line in bottle.request.body.readlines():
                fd.write(line.decode("utf8"))

            fd.write("\nRESPONSE:\n")
            fd.write("HEADERS:\n")
            for header in bottle.response.headers:
                fd.write("\t")
                fd.write(header)
                fd.write(":")
                fd.write(bottle.response.headers[header])
                fd.write("\n")

            fd.write("BODY:\n")
            fd.write(r)
            fd.close()
        except Exception as e:
            logger.exception("Error logging REST Request")


class QuipRestCounters:

    def __init__(self):
        return

    def apply(self, callback, context):

        @functools.wraps(callback)
        def wrapper(*args, **kwargs):
            if not PIM_REST_QUIP_ENABLED:
                logger.debug("not adding in quip")
                return callback(*args, **kwargs)
            counter = RestCounter.instance()
            log_desc = callback.__name__ if "rule" not in context else context["rule"]
            quip_key = counter.get_quip_route_key(bottle.request.method, log_desc, bottle.request.query_string)
            try:
                response = callback(*args, **kwargs)
                status = bottle.response.status
                if status == 200 or status == 202:
                    counter.increment_success_count(quip_key)
                else:
                    counter.increment_failure_count(quip_key)
                return response
            except Exception:
                counter.increment_exceptions_thrown(quip_key)
                raise

        return wrapper


class Ktracer:

    def apply(self, callback, context):

        @functools.wraps(callback)
        def wrapper(*args, **kwargs):
            label = "REST {} {} {}".format(bottle.request.method, bottle.request.path, bottle.request.headers.get("Pim-Session") or "N/A")
            with ktrace(label, baseevent=KernelTraceEventCodes.RestService):
                result = callback(*args, **kwargs)
                return result

        return wrapper


def nohardlocks():
    if enterprise.locked_hard:
        try:
            enterprise.unlock()
        except RuntimeError as e:
            raise EnterprisePerimeterHardLocked() from e

        if enterprise.locked_hard:
            raise EnterprisePerimeterHardLocked()


class RestService(object):

    def __init__(self):
        self.session_service = SessionManagementService()
        self.settings_service = SettingsMonitorService()
        self.notification_service = NotificationMgmtService()
        self.cached_message = None
        self.account_release_flags = dict()
        self._request_gc = False
        rest = bottle.app()
        if PPS_EMULATION:
            bottle.install(RequestTimer())
            bottle.install(PIMExeptionHandler())
            bottle.install(SessionEnforcer(self.session_service))
            bottle.install(ETagHandler())
        else:
            bottle.install(Ktracer())
            bottle.install(RequestTimer())
            bottle.install(PIMExeptionHandler())
            bottle.install(SessionEnforcer(self.session_service))
            bottle.install(Prioritizer())
            bottle.install(ETagHandler())
            bottle.install(QuipRestCounters())

        @rest.route("/bbm/contacts/search/:pin#[a-fA-F0-9]{8}#", method="GET")
        def get_bbm_contact_name_avatar(client_session, pin):
            bbm_account = get_account(client_session, LOCAL_BBM_ACCOUNTID)
            return self.json_response(bbm_account.rpc.get_name_and_avatar(pin=pin))

        @rest.route("/bbm/contacts/search/bulk/uri", method="POST")
        def get_bbm_contact_bulk_uri(client_session):
            bbm_account = get_account(client_session, LOCAL_BBM_ACCOUNTID)
            data = self.json_request()
            contacts = data.get("contacts", None)
            return self.json_response(bbm_account.rpc.get_bbm_contact_bulk_uri(contacts=contacts))

        @rest.route("/trans", method="GET")
        def translation(client_session):
            message_id = bottle.request.GET.get("message_id")
            data = {}
            data["message_id"] = message_id
            data["tranlation"] = LocaleService.get(message_id)
            return self.json_response(data)

        @rest.route("/restart", method="GET")
        def restart(client_session):
            import pim.services.accounts.watcher
            logger.error("Restarting all accounts...")
            pim.services.accounts.watcher.ChangesWatcher.restart_all_accounts()
            return self.json_response(True)

        @rest.route("/splat", method=["DELETE", "PUT"])
        def splat(client_session):
            if bottle.request.method == "DELETE":
                type = bottle.request.GET.get("type", NotifyType.MESSAGE)
                data = self.notification_service.rpc.remove_splat(type)
            elif bottle.request.method == "PUT":
                type = bottle.request.GET.get("type", NotifyType.MESSAGE)
                data = self.notification_service.rpc.add_splat(type)
            else:
                raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
            return self.json_response(data)

        @rest.route("/calls", method=["GET", "DELETE"])
        def calls(client_session):
            if bottle.request.method == "GET":
                data = call.get_calls(client_session, bottle.request.GET)
            elif bottle.request.method == "DELETE":
                data = call.delete_calls(client_session, bottle.request.GET)
            return self.json_response(data)

        @rest.route("/calldetails", method=["GET"])
        def calls(client_session):
            data = call.get_call_details(client_session, bottle.request.GET)
            return self.json_response(data)

        @rest.route("/calldetails/:account_id#[0-9]+#", method=["GET"])
        def calls(client_session, account_id):
            if not account_id or int(account_id) <= 0:
                return self.json_response("Invalid account id.", 500)
            data = call.get_call_details(client_session, bottle.request.GET, account_id)
            return self.json_response(data)

        @rest.route("/calls/unread_count", method="GET")
        def calls(client_session):
            unread = call.get_unread_count(client_session, bottle.request.GET)
            return self.json_response({"unread_count": unread})

        @rest.route("/call/:id#[0-9]+#", method=["GET", "DELETE", "POST"])
        def calls(client_session, id):
            if bottle.request.method == "GET":
                data = call.get_call(client_session, id, bottle.request.GET)
            elif bottle.request.method == "DELETE":
                data = call.delete_call(client_session, id)
            elif bottle.request.method == "POST":
                json = self.json_request()
                data = call.update_call(client_session, id, json)
            return self.json_response(data)

        @rest.route("/call/:id#[0-9]+#/details", method=["GET"])
        def call_details(client_session, id):
            data = call.get_call_details_by_call_id(client_session, id, bottle.request.GET)
            return self.json_response(data)

        @rest.route("/calllog_count")
        def call_logs_count(client_session):
            data = call.get_call_count(client_session)
            return self.json_response(data)

        @rest.route("/calllog_clearundismissed")
        def call_logs_clearundismissed(client_session):
            data = call.mark_read(client_session, bottle.request.GET)
            return self.json_response(data)

        @rest.route("/call/:account_id#[0-9]+#/calls", method=["GET", "DELETE"])
        def calls(client_session, account_id):
            account_id = int(account_id)
            v = validate_account_id(account_id)
            if v:
                return v
            if bottle.request.method == "GET":
                data = call.get_calls(client_session, bottle.request.GET, account_id)
            elif bottle.request.method == "DELETE":
                data = call.delete_calls(client_session, bottle.request.GET, account_id)
            return self.json_response(data)

        @rest.route("/call/:account_id#[0-9]+#/call/details", method=["GET"])
        def calls(client_session, account_id):
            account_id = int(account_id)
            v = validate_account_id(account_id)
            if v:
                return v
            data = call.get_call_details(client_session, bottle.request.GET, account_id)
            return self.json_response(data)

        @rest.route("/call/:account_id#[0-9]+#/call/detail/stats", method=["GET"])
        def calls(client_session, account_id):
            account_id = int(account_id)
            v = validate_account_id(account_id)
            if v:
                return v
            data = call.get_call_detail_stats(client_session, account_id)
            return self.json_response(data)

        @rest.route("/call/:account_id#[0-9]+#/call/:_id#[0-9]+#", method=["GET", "DELETE", "POST"])
        def calls(client_session, account_id, _id):
            account_id = int(account_id)
            _id = int(_id)
            v = validate_account_id(account_id)
            if v:
                return v
            if bottle.request.method == "GET":
                data = call.get_call(client_session, _id, bottle.request.GET, account_id)
            elif bottle.request.method == "DELETE":
                data = call.delete_call(client_session, _id, account_id)
            elif bottle.request.method == "POST":
                json = self.json_request()
                data = call.update_call(client_session, _id, json, account_id)
            return self.json_response(data)

        @rest.route("/call/:account_id#[0-9]+#/call/:_id#[0-9]+#/details", method=["GET"])
        def call_details(client_session, account_id, _id):
            account_id = int(account_id)
            _id = int(_id)
            v = validate_account_id(account_id)
            if v:
                return v
            data = call.get_call_details_by_call_id(client_session, _id, bottle.request.GET, account_id)
            return self.json_response(data)

        @rest.route("/call/:account_id#[0-9]+#/calls/count")
        def call_logs_count(client_session, account_id):
            account_id = int(account_id)
            v = validate_account_id(account_id)
            if v:
                return v
            data = call.get_call_count(client_session, account_id)
            return self.json_response(data)

        @rest.route("/call/:account_id#[0-9]+#/calls/unread_count", method="GET")
        def calls(client_session, account_id):
            account_id = int(account_id)
            v = validate_account_id(account_id)
            if v:
                return v
            unread = call.get_unread_count(client_session, bottle.request.GET, account_id)
            return self.json_response({"unread_count": unread})

        @rest.route("/call/:account_id#[0-9]+#/calls/mark_read", method=["GET"])
        def call_logs_mark_read(client_session, account_id):
            account_id = int(account_id)
            v = validate_account_id(account_id)
            if v:
                return v
            data = call.mark_read(client_session, bottle.request.GET, account_id)
            return self.json_response(data)

        @rest.route("/vvm/:account_id#[0-9]+#/:id#[0-9]+#", method=["POST"])
        def vvms(client_session, account_id, id):
            if bottle.request.method == "POST":
                json = self.json_request()
                data = vvm.update_vvm(client_session, account_id, id, json)
            return self.json_response(data)

        @rest.route("/vvm/:account_id#[0-9]+#/:id#[0-9]+#/attachments", method=["GET"])
        def vvms(client_session, account_id, id):
            data = vvm.get_attachments(client_session, account_id, id, bottle.request.GET)
            return self.json_response(data)

        @rest.route("/vvms/:account_id#[0-9]+#", method=["GET", "DELETE"])
        def vvms(client_session, account_id):
            if bottle.request.method == "GET":
                data = vvm.get_vvms(client_session, int(account_id), bottle.request.GET)
            elif bottle.request.method == "DELETE":
                data = vvm.delete_vvms(client_session, account_id, bottle.request.GET)
            return self.json_response(data)

        @rest.route("/vvms/:account_id#[0-9]+#/played", method=["POST"])
        def vvms(client_session, account_id):
            is_played = bool(int(bottle.request.POST.get("is_played", 1)))
            data = vvm.mark_played(client_session, int(account_id), bottle.request.GET, is_played)
            return self.json_response(data)

        @rest.route("/vvms/:account_id#[0-9]+#/viewed", method=["POST"])
        def vvms(client_session, account_id):
            is_viewed = bool(int(bottle.request.POST.get("is_viewed", 1)))
            data = vvm.mark_viewed(client_session, int(account_id), bottle.request.GET, is_viewed)
            return self.json_response(data)

        def _unread_messages_count(client_session, account_id, folder_id):
            data = bottle.request.GET
            sort_columns = []
            sort_orders = []
            filters = {}
            if account_id is not None:
                account_id = int(account_id)
                filters["account_id"] = account_id
            if folder_id is not None:
                filters["id"] = int(folder_id)
            args = self.read_anchor_args(data, sort_columns, sort_orders, filters)
            dataset = message.get_unread_message_counts_by_folder_anchor(client_session, account_id=account_id, **args)
            return self.json_response(dataset)

        @rest.route("/mail/messages/unread_count")
        def unread_messages_count_unified(client_session):
            try:
                return _unread_messages_count(client_session, None, None)
            except AnchoredQueryNoAccounts:
                return self.json_response([
                 {"unread_count": 0, 
                  "account_id": (-1), 
                  "id": (-1)}])

            return

        @rest.route("/mail/messages/unread_count/:account_id#[0-9]+#")
        def unread_messages_count_by_account(client_session, account_id):
            return _unread_messages_count(client_session, account_id, None)

        @rest.route("/mail/messages/unread_count/:account_id#[0-9]+#/:folder_id#[0-9]+#")
        def unread_messages_count_by_account_by_folder(client_session, account_id, folder_id):
            return _unread_messages_count(client_session, account_id, folder_id)

        @rest.route("/mail/messages/:account_id#[0-9]+#/count", method="GET")
        def count_messages_by_account(client_session, account_id):
            unread_only = boolean_get_paramater("unread", False)
            count = message.get_count_messages(client_session, unread_only=unread_only, account_id=account_id, folder_id=None, conversation_id=None)
            return self.json_response({"count": count})

        @rest.route("/mail/messages/:account_id#[0-9]+#/:folder_id#[0-9]+#/count", method="GET")
        def count_messages_by_folder(client_session, account_id, folder_id):
            unread_only = boolean_get_paramater("unread", False)
            count = message.get_count_messages(client_session, unread_only=unread_only, account_id=account_id, folder_id=folder_id, conversation_id=None)
            return self.json_response({"count": count})

        @rest.route("/mail/conversation/:account_id#[0-9]+#/:conversation_id#[0-9]+#/count", method="GET")
        def count_messages_by_conversation(client_session, account_id, conversation_id):
            unread_only = boolean_get_paramater("unread", False)
            count = message.get_count_messages(client_session, unread_only=unread_only, account_id=account_id, folder_id=None, conversation_id=conversation_id)
            return self.json_response({"count": count})

        @rest.route("/mail/conversations/:account_id#[0-9]+#/count", method="GET")
        def count_coversations_by_account(client_session, account_id):
            count = count_conversations_by_account_id(client_session, account_id)
            return self.json_response({"count": count})

        @rest.route("/mail/conversation/move/:account_id#[0-9]+#/:conversation_id#[0-9]+#/:target_id#[0-9]+#")
        def move_conversation(client_session, account_id, conversation_id, target_id):
            result = message.move_conversation(client_session, account_id, conversation_id, target_id)
            return self.json_response(result)

        @rest.route("/mail/priority_inbox/settings", method="GET")
        def mail_priority_inbox_settings_get(client_session):
            return self.json_response(priorityinbox.get_datapoints())

        @rest.route("/mail/priority_inbox/settings", method="POST")
        def mail_priority_inbox_settings_post(client_session):
            updates = bottle.request.GET.get("updates", "").split(",")
            priorityinbox.update_datapoints(tuple(int(u) for u in updates))

        @rest.route("/mail/priority_inbox/message/:account_id#[0-9]+#/:id#[0-9]+#/reasons", method="GET")
        def mail_priority_inbox_message_reasons(client_session, account_id, id):
            m = message._get_message(client_session, account_id, id)
            return self.json_response(priorityinbox.matched_datapoints(m, account_id))

        @rest.route("/mail/priority_inbox/conversation/:account_id#[0-9]+#/:id#[0-9]+#/reasons", method="GET")
        def mail_priority_inbox_conversation_reasons(client_session, account_id, id):
            msgs = message.list_messages_anchor_unified(client_session, account_id=account_id, conversation_id=id, show_sent=True, show_foldered=True, columns=[
             "date_sent"], sort_orders=["ASC"])
            results = {}
            for m in msgs:
                for dp in priorityinbox.matched_datapoints(m, account_id):
                    desc = dp["desc"]
                    if desc in results:
                        if dp["pass"]:
                            results[desc]["pass"] = 1
                    else:
                        results[desc] = dp

            return self.json_response(list(results.values()))

        @rest.route("/mail/priority_inbox/message/:account_id#[0-9]+#/:id#[0-9]+#/feedback", method="POST")
        def mail_priority_inbox_message_feedback_post(client_session, account_id, id):
            Field = PriorityInboxFeedbackField
            State = PriorityInboxFeedbackType
            OldState = PriorityInboxFeedbackCurrentStates
            fb = int(bottle.request.POST.get("feedback", State.NEUTRAL))
            field = int(bottle.request.POST.get("field", Field.UNKNOWN))
            user_states, auto_states = priorityinbox.get_message_feedback(account_id, message_id=id)
            auto = lambda s: s in auto_states
            update_contact = lambda s: priorityinbox.contact_feedback(s, account_id, message_id=id)
            update_message = lambda s: message.update_message(client_session, account_id, id, {"is_priority_inbox_auto": s})

            def update_conversation(state, mess=None):
                m = message._get_message(client_session, account_id, id)
                if m:
                    if auto(1):
                        if state == State.POSITIVE:
                            state = State.NEUTRAL
                            mess = 1
                    elif state == State.NEGATIVE:
                        state = State.NEUTRAL
                        mess = 0
                    if mess is not None:
                        update_message(int(mess))
                    d = {"is_priority_inbox_user": state}
                    message.update_conversation(client_session, account_id, m.conversation_id, d)
                return

            logger.info("PRIORITY FEEDBACK [%s] NEW:%s USER:%s, AUTO:%s", "CONVERSATION" if field == Field.CONVERSATION else "CONTACT" if field == Field.CONTACT else "UNKNOWN", fb, user_states, auto_states)
            if field == Field.CONVERSATION:
                is_pos = fb == State.POSITIVE
                if user_states == OldState.S3:
                    update_conversation(State.POSITIVE if is_pos else State.NEUTRAL, mess=0)
                elif user_states == OldState.S7:
                    update_conversation(State.NEUTRAL if is_pos else State.NEGATIVE, mess=1)
                else:
                    update_conversation(fb)
            elif field == Field.CONTACT:
                if fb == State.POSITIVE:
                    if user_states == OldState.S1:
                        update_conversation(State.POSITIVE, mess=1)
                    elif user_states == OldState.S2:
                        update_conversation(State.POSITIVE)
                    elif user_states == OldState.S3:
                        update_conversation(State.POSITIVE, mess=0)
                    elif user_states == OldState.S4:
                        update_conversation(State.NEUTRAL, mess=1)
                    elif user_states == OldState.S5:
                        update_contact(State.NEUTRAL if auto(1) else State.POSITIVE)
                        update_message(1)
                    elif user_states == OldState.S6:
                        update_contact(State.NEUTRAL if auto(1) else State.POSITIVE)
                        update_message(1)
                    elif user_states == OldState.S7:
                        update_conversation(State.NEUTRAL, mess=1)
                    elif user_states == OldState.S8:
                        update_contact(State.POSITIVE)
                        update_conversation(State.NEUTRAL, mess=1)
                    elif user_states == OldState.S9:
                        update_contact(State.NEUTRAL if auto(1) else State.POSITIVE)
                        update_conversation(State.NEUTRAL, mess=1)
                elif fb == State.NEGATIVE:
                    if user_states == OldState.S1:
                        update_contact(State.NEGATIVE if auto(1) else State.NEUTRAL)
                        update_conversation(State.NEUTRAL, mess=0)
                    elif user_states == OldState.S2:
                        update_contact(State.NEGATIVE)
                        update_conversation(State.NEUTRAL, mess=0)
                    elif user_states == OldState.S3:
                        update_conversation(State.NEUTRAL, mess=0)
                    elif user_states == OldState.S4:
                        update_contact(State.NEGATIVE if auto(1) else State.NEUTRAL)
                        update_message(0)
                    elif user_states == OldState.S5:
                        update_contact(State.NEGATIVE if auto(1) else State.NEUTRAL)
                        update_message(0)
                    elif user_states == OldState.S6:
                        update_conversation(State.NEUTRAL, mess=0)
                    elif user_states == OldState.S7:
                        update_conversation(State.NEGATIVE, mess=1)
                    elif user_states == OldState.S8:
                        update_conversation(State.NEGATIVE)
                    elif user_states == OldState.S9:
                        update_conversation(State.NEGATIVE, mess=0)
            return

        @rest.route("/mail/priority_inbox/conversation/:account_id#[0-9]+#/:id#[0-9]+#/feedback", method="POST")
        def mail_priority_inbox_conversation_feedback_post(client_session, account_id, id):
            Field = PriorityInboxFeedbackField
            State = PriorityInboxFeedbackType
            OldState = PriorityInboxFeedbackCurrentStates
            fb = int(bottle.request.POST.get("feedback", State.NEUTRAL))
            field = int(bottle.request.POST.get("field", Field.UNKNOWN))
            user_states, auto_states = priorityinbox.get_message_feedback(account_id, conversation_id=id)
            auto = lambda s: s in auto_states
            update_contact = lambda s: priorityinbox.contact_feedback(s, account_id, conversation_id=id)

            def update_message(state):
                c = message.get_conversation_hide_sent(client_session, account_id, id)
                if c and c.newest_message_id:
                    message.update_message(client_session, account_id, c.newest_message_id, {"is_priority_inbox_auto": state})

            def update_conversation(state, mess=None):
                if auto(1):
                    if state == State.POSITIVE:
                        state = State.NEUTRAL
                        mess = 1
                elif state == State.NEGATIVE:
                    state = State.NEUTRAL
                    mess = 0
                d = {"is_priority_inbox_user": state}
                message.update_conversation(client_session, account_id, id, d)
                if mess is not None:
                    update_message(int(mess))
                return

            logger.info("PRIORITY FEEDBACK [%s] NEW:%s USER:%s, AUTO:%s", "CONVERSATION" if field == Field.CONVERSATION else "CONTACT" if field == Field.CONTACT else "UNKNOWN", fb, user_states, auto_states)
            if field == Field.CONVERSATION:
                is_pos = fb == State.POSITIVE
                if user_states == OldState.S3:
                    update_conversation(State.POSITIVE if is_pos else State.NEUTRAL, mess=0)
                elif user_states == OldState.S7:
                    update_conversation(State.NEUTRAL if is_pos else State.NEGATIVE, mess=1)
                else:
                    update_conversation(fb)
            elif field == Field.CONTACT:
                if fb == State.POSITIVE:
                    if user_states == OldState.S1:
                        update_conversation(State.POSITIVE, mess=1)
                    elif user_states == OldState.S2:
                        update_conversation(State.POSITIVE)
                    elif user_states == OldState.S3:
                        update_conversation(State.POSITIVE, mess=0)
                    elif user_states == OldState.S4:
                        update_conversation(State.NEUTRAL, mess=1)
                    elif user_states == OldState.S5:
                        update_contact(State.NEUTRAL if auto(1) else State.POSITIVE)
                        update_message(1)
                    elif user_states == OldState.S6:
                        update_contact(State.NEUTRAL if auto(1) else State.POSITIVE)
                        update_message(1)
                    elif user_states == OldState.S7:
                        update_conversation(State.NEUTRAL, mess=1)
                    elif user_states == OldState.S8:
                        update_contact(State.POSITIVE)
                        update_conversation(State.NEUTRAL, mess=1)
                    elif user_states == OldState.S9:
                        update_contact(State.NEUTRAL if auto(1) else State.POSITIVE)
                        update_conversation(State.NEUTRAL, mess=1)
                elif fb == State.NEGATIVE:
                    if user_states == OldState.S1:
                        update_contact(State.NEGATIVE if auto(1) else State.NEUTRAL)
                        update_conversation(State.NEUTRAL, mess=0)
                    elif user_states == OldState.S2:
                        update_contact(State.NEGATIVE)
                        update_conversation(State.NEUTRAL, mess=0)
                    elif user_states == OldState.S3:
                        update_conversation(State.NEUTRAL, mess=0)
                    elif user_states == OldState.S4:
                        update_contact(State.NEGATIVE if auto(1) else State.NEUTRAL)
                        update_message(0)
                    elif user_states == OldState.S5:
                        update_contact(State.NEGATIVE if auto(1) else State.NEUTRAL)
                        update_message(0)
                        if auto(1):
                            update_conversation(State.NEGATIVE)
                    elif user_states == OldState.S6:
                        if auto(1):
                            update_conversation(State.NEGATIVE)
                        else:
                            update_conversation(State.NEUTRAL, mess=0)
                    elif user_states == OldState.S7:
                        update_conversation(State.NEGATIVE, mess=1)
                    elif user_states == OldState.S8:
                        update_conversation(State.NEGATIVE)
                    elif user_states == OldState.S9:
                        update_conversation(State.NEGATIVE, mess=0)
                try:
                    account = get_account(client_session, account_id)
                    account.rpc.mail_reprocess_priority_inbox_conversation(conversation_id=id, async=True)
                except Exception:
                    logger.warn("Unable to reprocess messages in account[%s] conversation[%s]", account_id, id)

            return

        @rest.route("/mail/priority_inbox/message/:account_id#[0-9]+#/:id#[0-9]+#/feedback", method="GET")
        def mail_priority_inbox_message_feedback_get(client_session, account_id, id):
            return self.json_response(priorityinbox.get_message_feedback(account_id, message_id=id)[0])

        @rest.route("/mail/priority_inbox/conversation/:account_id#[0-9]+#/:id#[0-9]+#/feedback", method="GET")
        def mail_priority_inbox_conversation_feedback_get(client_session, account_id, id):
            return self.json_response(priorityinbox.get_message_feedback(account_id, conversation_id=id)[0])

        @rest.route("/mail/priority_inbox/message/:account_id#[0-9]+#/:id#[0-9]+#/response_time", method="POST")
        def mail_priority_inbox_message_response_time(client_session, account_id, id):
            type = int(bottle.request.POST.get("type", PriorityInboxResponseTimeType.UNKNOWN))
            dt = int(bottle.request.POST.get("delta", 0))
            if type:
                priorityinbox.message_response_time(account_id, id, type, dt)

        @rest.route("/mail/priority_inbox/messages/response_time", method="POST")
        def mail_priority_inbox_messages_response_time(client_session):
            type = int(bottle.request.POST.get("type", PriorityInboxResponseTimeType.UNKNOWN))
            dt = int(bottle.request.POST.get("delta", 0))
            if type:
                for account_id, id in self.json_request("messages"):
                    priorityinbox.message_response_time(account_id, id, type, dt)

        @rest.route("/mail/priority_inbox/entity", method="POST")
        def mail_priority_inbox_entity(client_session):
            type = int(bottle.request.POST.get("type", PriorityInboxEntityType.UNKNOWN))
            sid = bottle.request.POST.get("sid", "")
            nid = bottle.request.POST.get("nid")
            entity = priorityinbox.is_entity_priority(sid, nid, type)
            if entity is None:
                return self.json_response("priority inbox service not running", 500)
            else:
                return self.json_response(entity)

        @rest.route("/mail/priority_inbox/entity/multi", method="POST")
        def mail_priority_inbox_entity_multi(client_session):
            ids = bottle.request.POST.get("ids", "")
            ids = [int(id) for id in ids.split(",")]
            return self.json_response(priorityinbox.get_entity_dicts_by_ids(ids))

        @rest.route("/mail/priority_inbox/entity/feedback", method="POST")
        def mail_priority_inbox_entity_feedback(client_session):
            type = int(bottle.request.POST.get("type", PriorityInboxEntityType.UNKNOWN))
            sid = bottle.request.POST.get("sid", None)
            nid = bottle.request.POST.get("nid", None)
            fb = int(bottle.request.POST.get("feedback", PriorityInboxFeedbackType.NEUTRAL))
            priorityinbox.entity_feedback(sid, nid, type, fb)
            return

        @rest.route("/mail/priority_inbox/entity/get_feedback", method="POST")
        def mail_priority_inbox_entity_feedback_get(client_session):
            type = int(bottle.request.POST.get("type", PriorityInboxEntityType.UNKNOWN))
            sid = bottle.request.POST.get("sid", None)
            nid = bottle.request.POST.get("nid", None)
            return self.json_response(priorityinbox.get_entity_feedback(sid, nid, type))

        @rest.route("/mail/priority_inbox/stats", method="GET")
        def mail_priority_inbox_stats_get(client_session):
            result = self.json_response(priorityinbox.print_stats())
            if int(bottle.request.GET.get("reset", 0)):
                priorityinbox.reset_stats()
            return result

        @rest.route("/calendars")
        def calendars_list(client_session):
            results = list(calendar.list_folders(client_session))
            logger.info("calendar folder search: %d folders", len(results))
            return self.json_response(results)

        @rest.route("/calendars/freebusy", method="POST")
        def calendars_freebusy(client_session):
            data = self.json_request()
            freebusy, need_live = calendar.freebusy(client_session, data)
            return self.json_response({"data": freebusy,  "live": need_live})

        editable_calendar_properties = ('colour', 'visible', 'is_meeting_mode_active')

        @rest.route("/calendars/:account_id#[0-9]+#/:id#[0-9]+#", method="POST")
        def calendars_update(client_session, account_id, id):
            validate_calendar_request(account_id, id)
            data = self.json_request()
            filtered_data = {k: v for k, v in data.items() if k in editable_calendar_properties}
            result = calendar.modify_calendar(client_session, account_id, id, filtered_data)
            return self.json_response(result)

        @rest.route("/calendars/events/:account_id#[0-9]+#/:id#[0-9]+#", method=[
         "GET", "POST", "PUT"])
        def calendars_events(client_session, account_id, id):
            validate_calendar_request(account_id, id)
            if bottle.request.method == "GET":
                return self.json_response(calendar.event_info(client_session, account_id, id))
            else:
                if bottle.request.method == "POST" or bottle.request.method == "PUT":
                    data = self.json_request()
                    if "recurrence" in data and data["recurrence"]:
                        data["recurrence"]["type"] = "ActiveSync"
                    if not data.get("timezone", None):
                        data["timezone"] = self.settings_service.tzname
                    data.setdefault("notify", False)
                    data.setdefault("notify_all", True)
                    if data["notify"] and not data["notify_all"] and ("added_attendees" not in data or not data["added_attendees"]):
                        if "deleted_attendees" not in data or not data["deleted_attendees"]:
                            return self.json_response("Missing list of attendees to notify.", 500)
                    async = data.get("async", True)
                    result = calendar.modify_event(client_session, account_id, id, data, async=async)
                return self.json_response(result)

        @rest.route("/calendars/events/:account_id#[0-9]+#/:id#[0-9]+#/delete", method="POST")
        def calendars_events_delete(client_session, account_id, id):
            validate_calendar_request(account_id, id)
            data = None
            if "json" in bottle.request.forms:
                data = self.json_request()
            if data:
                data.setdefault("notify", False)
                data.setdefault("notify_comment", None)
            else:
                data = {}
                data["notify"] = False
            result = calendar.delete_event(client_session, account_id, id, data)
            return self.json_response(result)

        @rest.route("/calendars/reset/:account_id#[0-9]+#", method="GET")
        def calendars_reset_local(client_session, account_id):
            return self.json_response(calendar.reset_local_folder(client_session, account_id))

        @rest.route("/calendars/events/:account_id#[0-9]+#", method="POST")
        def calendars_events_create(client_session, account_id):
            validate_calendar_request(account_id)
            data = self.json_request()
            if "recurrence" in data and data["recurrence"]:
                data["recurrence"]["type"] = "ActiveSync"
            if not data.get("timezone", None):
                data["timezone"] = self.settings_service.tzname
            result = calendar.create_event(client_session, account_id, data)
            return self.json_response(result)

        @rest.route("/calendars/events/search", method="GET")
        def calendars_events_search_all_GET(client_session):
            ignore_dates = bottle.request.GET.get("ignore_dates")
            prefix = bottle.request.GET.get("prefix_match")
            start = bottle.request.GET.get("start")
            end = bottle.request.GET.get("end")
            utc = int(bottle.request.GET.get("utc", 0)) == 1
            if not prefix and not ignore_dates:
                if not start:
                    return self.json_response("missing start param", 500)
                if not end:
                    return self.json_response("missing end param", 500)
            offset = int(bottle.request.GET.get("offset", 0))
            limit = int(bottle.request.GET.get("limit", -1))
            expand = int(bottle.request.GET.get("expand", 1))
            details = bottle.request.GET.get("details")
            sorted = int(bottle.request.GET.get("sort", 0))
            sort_asc = int(bottle.request.GET.get("sort_asc", 0))
            sort = None
            if sorted:
                sort = {"key": "start_time",  "asc": (bool(sort_asc))}
            return self.json_response(calendar.event_search(client_session, start, end, expand, details, limit=limit, sort=sort, prefix=prefix, offset=offset, utc=utc))

        @rest.route("/calendars/events/search", method="POST")
        def calendars_events_search_all(client_session):
            ignore_dates = bottle.request.POST.get("ignore_dates")
            prefix = bottle.request.POST.get("prefix_match")
            start = bottle.request.POST.get("start")
            end = bottle.request.POST.get("end")
            utc = int(bottle.request.POST.get("utc", 0)) == 1
            personal_only = bottle.request.POST.get("personal_only")
            if not prefix and not ignore_dates:
                if not start and not prefix:
                    return self.json_response("missing start param", 500)
                if not end and not prefix:
                    return self.json_response("missing end param", 500)
            offset = int(bottle.request.POST.get("offset", 0))
            limit = int(bottle.request.POST.get("limit", -1))
            expand = int(bottle.request.POST.get("expand", 1))
            details = bottle.request.POST.get("details")
            sort = None
            if "sort" in bottle.request.POST:
                sort = self.json_request(field="sort")
            subject_prefix = bottle.request.POST.get("subject_prefix_match")
            location_prefix = bottle.request.POST.get("location_prefix_match")
            attendee_prefix = bottle.request.POST.get("attendee_prefix_match")
            request = self.json_request() if "json" in bottle.request.POST else None
            results = calendar.event_search(client_session, start, end, expand, details, request, None, limit, sort, prefix=prefix, subject_prefix=subject_prefix, location_prefix=location_prefix, attendee_prefix=attendee_prefix, offset=offset, utc=utc, personal_only=personal_only)
            return self.json_response(results)

        @rest.route("/calendars/events/:account_id#[0-9]+#/search", method="POST")
        @rest.route("/calendars/events/:account_id#[0-9]+#/:id#[0-9]+#/search", method="POST")
        def calendars_events_search_by_account(client_session, account_id, id=None):
            validate_calendar_request(account_id, id)
            request = self.json_request() if "json" in bottle.request.POST else {}
            if id:
                request["events"] = {id: None}
            req = {account_id: request}
            prefix = bottle.request.POST.get("prefix_match")
            start = bottle.request.POST.get("start")
            end = bottle.request.POST.get("end")
            if not prefix and "events" not in request and "guids" not in request:
                if not start:
                    return self.json_response("missing start param", 500)
                if not end:
                    return self.json_response("missing end param", 500)
            expand = int(bottle.request.POST.get("expand", "1"))
            details = bottle.request.POST.get("details")
            return self.json_response(calendar.event_search(client_session, start, end, expand, details=details, request=req, prefix=prefix))

        @rest.route("/calendars/events/:account_id#[0-9]+#/:id#[0-9]+#/exception", method="POST")
        def calendars_events_exception(client_session, account_id, id):
            validate_calendar_request(account_id, id)
            data = self.json_request()
            if "original_start_time" not in data:
                return self.json_response("Original start time missing.", 500)
            else:
                if "start_time" in data:
                    if "end_time" not in data:
                        return self.json_response("Create exception - end time missing.", 500)
                    else:
                        data.setdefault("notify", False)
                        result = calendar.create_event_exception(client_session, account_id, id, data)
                        return self.json_response(result)
                result = calendar.delete_event_exception(client_session, account_id, id, data)
                return self.json_response(result)

        @rest.route("/calendars/events/:account_id#[0-9]+#/:id#[0-9]+#/reply", method="POST")
        def calendars_events_reply(client_session, account_id, id):
            validate_calendar_request(account_id, id)
            data = self.json_request()
            if "status" not in data:
                return self.json_response("Meeting status missing.", 500)
            data.setdefault("comments", "")
            result = calendar.event_reply(client_session, account_id, id, data)
            return self.json_response(result)

        @rest.route("/calendars/events/:account_id#[0-9]+#/reply", method="POST")
        def calendars_events_reply_with_event(client_session, account_id):
            validate_calendar_request(account_id)
            data = self.json_request()
            if "status" not in data:
                return self.json_response("Meeting status missing.", 500)
            else:
                if "event" not in data:
                    return self.json_response("Event data missing.", 500)
                result = calendar.event_reply(client_session, account_id, None, data)
                return self.json_response(result)

        @rest.route("/calendars/ics/:account_id#[0-9]+#/forward", method="POST")
        def calendars_ics_forward(client_session, account_id):
            validate_calendar_request(account_id)
            data = self.json_request()
            logger.debug("calendars_ics_forward: %s", data)
            event_id = data.get("event_id")
            event_ics = data.get("event")
            if event_id:
                event_id = int(event_id)
            elif not event_ics:
                return self.json_response("Event data missing.", 500)
            orig_msg_id = data.get("orig_msg_id")
            orig_msg_id = int(orig_msg_id) if orig_msg_id else 0
            _calendars_forward(client_session, account_id, event_id, orig_msg_id, event_ics)

        @rest.route("/calendars/events/:account_id#[0-9]+#/:id#[0-9]+#/forward", method="POST")
        def calendars_events_forward(client_session, account_id, id):
            validate_calendar_request(account_id, id)
            _calendars_forward(client_session, account_id, int(id))

        def _calendars_forward(client_session, account_id, event_id=None, orig_msg_id=0, event_ics=None):
            to_list = remove_friendly_names(json.loads(bottle.request.POST.get("to", default="[]")))
            from_addr = bottle.request.POST.get("from")
            m_account_id = bottle.request.POST.get("message_account_id")
            if not to_list:
                return self.json_response("Meeting forward 'to' missing.", 500)
            if not from_addr:
                return self.json_response("Meeting forward 'from' missing.", 500)
            if not m_account_id:
                return self.json_response("Meeting forward 'message_account_id' missing.", 500)
            else:
                if not event_id:
                    event_id = bottle.request.POST.get("event_id")
                    event_id = int(event_id) if event_id else None
                m_account_id = int(m_account_id)
                ics_attachment, provider_data = calendar.event_build_ics(client_session, account_id, event_id, to_list, from_addr, m_account_id, orig_msg_id, event_ics)
                if not orig_msg_id:
                    orig_msg_id = provider_data.get("orig_msg_id")
                if ics_attachment:
                    action = provider_data.get("action", "send")
                    reply_to_addr = None
                    organizer = provider_data.pop("organizer", None)
                    if organizer and organizer.get("email") not in from_addr:
                        reply_to_addr = formataddr((organizer.get("name"), organizer.get("email")))
                        logger.info("Setting reply_to_addr to organizer: %s", anonymize_data(reply_to_addr))
                    return self._save_or_send_calendar(client_session, m_account_id, action, orig_msg_id, ics_attachment=ics_attachment, to_list_override=to_list, from_addr=from_addr, reply_to_addr=reply_to_addr, provider_data=provider_data)
                raise CalendarInvalidMeetingError()
                return

        def _verify_ics_permissions(client_session, path, account_id=None):
            if isinstance(path, str):
                abspath = os.path.realpath(path) if not path.startswith("/tmp") else path
                old_enterprise_root = PERSONAL_ROOT + "-enterprise"
                if abspath.startswith(old_enterprise_root):
                    logger.warn("_verify_ics_permissions: {} path is still used to save corp ics instead of {}".format(old_enterprise_root, ENTERPRISE_ROOT))
                is_enterprise_path = abspath.startswith(ENTERPRISE_ROOT) or abspath.startswith(old_enterprise_root)
            else:
                raise ValueError("path is not a string")
            is_enterprise_account = False if account_id is None else int(account_id) in [acc.id for acc in Account.all() if acc.enterprise]
            if is_enterprise_path or is_enterprise_account:
                if not client_session.has_enterprise_access(DOMAIN_CALENDAR):
                    raise IcsParseException(logger, "access denied: enterprise perimeter")
            if account_id and not is_enterprise_account or not is_enterprise_path:
                if not client_session.has_personal_access(DOMAIN_CALENDAR):
                    raise IcsParseException(logger, "access denied: personal perimeter")
            return

        @rest.route("/calendars/events/:account_id#[0-9]+#/:id#[0-9]+#/ics")
        def calendar_events_ics_build(client_session, account_id, id):
            validate_calendar_request(account_id, id)
            path = bottle.request.GET.get("path")
            trusted_date = bottle.request.GET.get("trusted_date")
            add_attendees = bottle.request.GET.get("add_attendees")
            version = int(bottle.request.GET.get("version", "2"))
            try:
                _verify_ics_permissions(client_session, path, account_id)
            except IcsParseException as e:
                return self.json_response(e.msg, 500)

            logger.debug("ics - trusted %s - attendee %s - version %d", str(trusted_date), str(add_attendees), version)
            result = calendar.event_get_ics(client_session, account_id, id, path, trusted_date, add_attendees, version)
            error = result.get("error")
            if error:
                return self.json_response(error, 500)
            return self.json_response(result)

        @rest.route("/calendars/:account_id#[0-9]+#/:event_id#[0-9]+#/clear_online_conference", method="POST")
        def calendars_clear_online_conference(client_session, account_id, event_id):
            success = True
            try:
                calendar.clear_online_conference(client_session, account_id, event_id)
            except Exception:
                success = False
                logger.exception("failed to clear online conference data")

            return self.json_response({"success": success})

        @rest.route("/calendars/ics/:account_id#[0-9]+#/reply", method="POST")
        def calendars_ics_reply(client_session, account_id):
            validate_calendar_request(account_id)
            data = self.json_request()
            status = data.get("status")
            if not status:
                return self.json_response("Meeting status missing.", 500)
            try:
                data["status"] = int(status)
            except Exception:
                return self.json_response("Invalid status: {}.".format(status), 500)

            if "event" not in data:
                return self.json_response("Meeting data missing.", 500)
            data.setdefault("comments", "")
            async = True if data["event"].get("id") else False
            result = calendar.ics_reply(client_session, account_id, data, async=async)
            return self.json_response(result)

        @rest.route("/calendars/ics/:account_id#[0-9]+#/cancel", method="POST")
        def calendars_ics_cancel(client_session, account_id):
            validate_calendar_request(account_id)
            data = self.json_request()
            if "id" not in data and "parent_id" not in data:
                return self.json_response("Event id missing.", 500)
            result = calendar.ics_cancel(client_session, account_id, data)
            return self.json_response(result)

        @rest.route("/calendars/ics/parse", method="POST")
        def calendar_ics_parse(client_session):
            data = self.json_request()
            ics_objects = None
            try:
                ics_file = data.get("filepath")
                if ics_file is None:
                    ics_file, attachment_id = calendar.find_ics_filepath_from_message_id(client_session, data)
                    if ics_file:
                        data["filepath"] = ics_file
                        if "mimetype" not in data:
                            data["mimetype"] = "text/calendar"
                    elif attachment_id:
                        ics_objects = {"attachment_id": attachment_id,  "events": [],  "todos": []}
                        return self.json_response(ics_objects)
                if ics_file:
                    _verify_ics_permissions(client_session, ics_file)
                else:
                    raise IcsParseException(logger, "ics file path is not set")
                mime_type = data.get("mimetype")
                if mime_type in (None, '', 'text/plain') and (ics_file.endswith(".ics") or ics_file.endswith(".vcs")):
                    mime_type = "text/calendar"
                if not mime_type or not ("text/calendar" in mime_type or "application/ics" in mime_type):
                    raise IcsParseException(logger, "file is not an ics")
                ics_objects = calendar.event_parse_ics(client_session, data, self.settings_service.tzname)
            except IcsParseException as e:
                return self.json_response(e.msg, 500)
            except Exception:
                logger.exception("Could not parse ics attachment")
                return self.json_response("Could not parse ics attachment", 500)
            else:
                return self.json_response(ics_objects)
            return

        def validate_calendar_request(account_id, event_id=None):
            if not account_id or int(account_id) <= 0:
                return self.json_response("Invalid account id.", 500)
            else:
                if event_id is not None and int(event_id) <= 0:
                    return self.json_response("Invalid event id.", 500)
                return

        def validate_account_id(account_id):
            if not account_id or int(account_id) <= 0:
                return self.json_response("Invalid account id.", 500)
            else:
                return

        @rest.route("/calendars/drilldown/people", method="POST")
        def calendars_drilldown_people(client_session):
            return analytics_drilldown_people(client_session)

        @rest.route("/calendars/event/last", method="POST")
        def calendars_events_last(client_session):
            return analytics_events_last(client_session)

        @rest.route("/calendars/event/next", method="POST")
        def calendars_events_next(client_session):
            return analytics_events_next(client_session)

        @rest.route("/calendars/person/common", method="POST")
        def calendars_person_common(client_session):
            return analytics_person_common(client_session)

        @rest.route("/calendars/location/common", method="POST")
        def calendars_location_common(client_session):
            return analytics_location_common(client_session)

        calendar_settings_keys = ('snooze_time', 'tzDatabaseId', '_CS_TIMEZONE', 'hourFormat')
        calendar_config = {"meetingReminder": 15,  "adMeetingReminder": 900}

        def filtered_calendar_settings():
            sp = self.settings_service.properties.to_json()
            for k, v in calendar_config.items():
                if k not in sp:
                    pim.internal.settings.write_custom_setting(k, v)
                    sp[k] = v
                    continue

            fp = {k: v for k, v in sp.items() if is_calendar_settings_key(k)}
            return self.json_response(fp)

        def is_calendar_settings_key(k):
            return k in calendar_settings_keys or k in calendar_config.keys()

        @rest.route("/calendars/settings", ["GET", "PUT"])
        def calendars_settings(client_session):
            if bottle.request.method == "GET":
                return filtered_calendar_settings()
            if bottle.request.method == "PUT":
                data = self.json_request()
                for key, value in data.items():
                    if is_calendar_settings_key(key):
                        pim.internal.settings.write_custom_setting(key, value)
                    else:
                        logger.warn("Calendar Settings: Ignored non-calendar setting: %s", key)

                return filtered_calendar_settings()
            raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)

        @rest.route("/calendars/ics/generate", method="POST")
        def calendars_ics_generate(client_session):
            data = self.json_request()
            if not data.get("timezone", None):
                data["timezone"] = self.settings_service.tzname
            result = calendar.event_to_ics(data)
            return self.json_response(result)

        @rest.route("/calendars/ics/:account_id#[0-9]+#/:folder_id#[0-9]+#", method="POST")
        def calendars_ics_add(client_session, account_id, folder_id):
            validate_calendar_request(account_id)
            ics_file = bottle.request.POST.get("filepath")
            account_id = int(account_id)
            ics_data = None
            if ics_file:
                path_ok = True
                try:
                    _verify_ics_permissions(client_session, ics_file, account_id)
                except Exception:
                    path_ok = False

                if path_ok:
                    try:
                        with open(ics_file) as f:
                            ics_data = f.read()
                    except Exception:
                        tr = traceback.format_exc()
                        logger.warning("calendars_ics_add: could not read ics file %s\n%s", ics_file, tr)

            if not ics_data:
                ics_data = bottle.request.POST.get("ics")
            if not ics_data:
                msg = "no ics data in request, could not read ics path"
                logger.error(msg)
                return self.json_response(msg, 500)
            else:
                guid = bottle.request.POST.get("guid")
                try:
                    result = calendar.add_event_from_ics(client_session, account_id, folder_id, ics_data, guid, self.settings_service.tzname)
                except PIMException:
                    result = self.json_response("cannot add event in ics", 500)

                return self.json_response(result)

        @rest.route("/calendars/ics/:account_id#[0-9]+#/counter", method="POST")
        def calendars_ics_counter(client_session, account_id):
            validate_calendar_request(account_id)
            data = self.json_request()
            status = data.get("status")
            if not status:
                return self.json_response("Meeting status missing.", 500)
            try:
                data["status"] = int(status)
            except Exception:
                return self.json_response("Invalid status: {}.".format(status), 500)

            if "event" not in data:
                return self.json_response("Meeting data missing.", 500)
            data.setdefault("comments", "")
            if "new_time" not in data:
                return self.json_response("New time missing.", 500)
            async = True if data["event"].get("id") else False
            data["method"] = "COUNTER"
            result = calendar.ics_reply(client_session, account_id, data, async=async)
            return self.json_response(result)

        @rest.route("/calendars/events/:account_id#[0-9]+#/delete", method="POST")
        def calendars_events_delete_by_guid(client_session, account_id):
            validate_calendar_request(account_id)
            data = None
            if "json" in bottle.request.POST:
                data = self.json_request()
            guid = bottle.request.POST.get("guid")
            original_start_time = bottle.request.POST.get("original_start_time")
            if not guid:
                return self.json_response("Event guid missing.", 500)
            else:
                try:
                    result = calendar.delete_event_by_guid(client_session, account_id, guid, original_start_time, data, self.settings_service.tzname)
                except PIMException:
                    result = self.json_response("cannot delete event by guid", 500)

                return self.json_response(result)

        @rest.route("/cardholder/init_folder", method="POST")
        def calendars_init_cardholder_folder(client_session):
            name = bottle.request.POST.get("name")
            if not name:
                return self.json_response("Name missing.", 500)
            try:
                result = calendar.init_cardholder_folder(client_session, name)
            except PIMException:
                result = self.json_response("cannot initialize cardholder folder", 500)

            return self.json_response(result)

        @rest.route("/addressbooks")
        def addressbook_list(client_session):
            folders = contact.list_folders(client_session)
            return self.json_response(folders)

        def log_session_contact_access(client_session):
            if not client_session:
                return
            logger.info("Granted contact session access hybrid:%d personal:%d enterprise:%d social:%d hidden:%d", client_session.has_hybrid_access(), client_session.has_personal_access(DOMAIN_CONTACTS), client_session.has_enterprise_access(DOMAIN_CONTACTS), client_session.has_social_access(), client_session.has_hiddenapi_access())

        @rest.route("/contacts/:account_id#[0-9]+#/list", method=["POST"])
        def contacts_list(client_session, account_id):
            log_session_contact_access(client_session)
            ids = None
            exclude_ids = None
            if "json" in bottle.request.POST:
                data = self.json_request()
                ids = data.get("ids")
                exclude_ids = data.get("exclude_ids")
            contacts = contact.get_contacts(client_session, account_id, bottle.request.GET, ids, exclude_ids=exclude_ids)
            return self.json_response(contacts)

        @rest.route("/contacts/resync_automerge")
        def contacts_resync_automerge(client_session):
            log_session_contact_access(client_session)
            unified_account = account.get_account(client_session, UNIFIED_CONTACTS_ACCOUNTID)
            unified_account.rpc.contact_resync_automerge()
            return self.json_response({}, 200)

        @rest.route("/contacts/:account_id#[0-9]+#")
        def contacts(client_session, account_id):
            log_session_contact_access(client_session)
            contacts = contact.get_contacts(client_session, account_id, bottle.request.GET)
            return self.json_response(contacts)

        @rest.route("/contacts/:contact_id#[0-9]+#/copy_to_account", method="POST")
        def copy_to_account(client_session, contact_id):
            log_session_contact_access(client_session)
            data = self.json_request()
            accounts = None
            if data and "accounts" in data:
                accounts = data.get("accounts")
                return self.json_response(contact.copy_to_account(client_session, contact_id, accounts))
            else:
                return self.json_response("Invalid account id.", 500)
                return

        @rest.route("/contact/validate", method="POST")
        def contact_validate(client_session):
            log_session_contact_access(client_session)
            data = self.json_request()
            contact_data = data.get("contact", None) if data else None
            targets = data.get("targets", None) if data else None
            id = bottle.request.GET.get("id")
            result = contact.validate_contact(client_session, id, contact_data, targets)
            return self.json_response(result)

        @rest.route("/contacts/:account_id#[0-9]+#", method="POST")
        def contacts_create(client_session, account_id):
            log_session_contact_access(client_session)
            data = self.json_request()
            targets = None
            enterprise_only = boolean_get_paramater("enterprise", False)
            manual_merge_only = boolean_get_paramater("manual_merge_only", False)
            local_only = boolean_get_paramater("local_only", False)
            if data and "targets" in data:
                targets = data.get("targets")
                result = contact.create_contact(client_session, account_id, data, enterprise_only, manual_merge_only, local_only, targets)
            elif data:
                result = contact.create_contact(client_session, account_id, data, enterprise_only, manual_merge_only, local_only)
            return self.json_response(result)

        @rest.route("/contacts/:account_id#[0-9]+#/batch", method=["GET", "POST", "PUT"])
        def contacts_batch(client_session, account_id):
            log_session_contact_access(client_session)
            data = self.json_request()
            contacts = None
            result = {}
            if data and "contacts" in data:
                contacts = data.get("contacts")
            if contacts:
                if len(contacts) > 1000:
                    raise ContactRequestedSizeTooLarge("contacts batch create received too many contacts")
                if bottle.request.method == "PUT" or bottle.request.method == "POST":
                    return_values = boolean_get_paramater("return_values", False)
                    result = contact.create_contacts_batch(client_session, account_id, contacts, return_values)
                elif bottle.request.method == "GET":
                    pass
            return self.json_response(result)

        @rest.route("/contacts/:account_id#[0-9]+#/batch_delete", method=["POST"])
        def contacts_batch_delete(client_session, account_id):
            log_session_contact_access(client_session)
            logger.info("Batch delete request received.")
            data = self.json_request()
            contacts = None
            options = None
            if data and "contacts" in data:
                contacts = data.get("contacts")
            if data and "options" in data:
                options = data.get("options")
            if contacts:
                contact.delete_contacts_batch(client_session, account_id, contacts, options)
            logger.info("Batch delete request processed.")
            return self.json_response({}, 200)

        @rest.route("/contacts/is_sim_full", method=["GET"])
        def contact_account_full(client_session):
            log_session_contact_access(client_session)
            if bottle.request.method == "GET":
                provider_account = get_account(client_session, LOCAL_SIMCONTACTS_ACCOUNTID)
                result = provider_account.rpc.is_full()
                return self.json_response({"full": (1 if result else 0)}, 200)
            return self.json_response("Invalid request", 500)

        def handle_hold_account(self, client_session, account_id):
            if not DEBUG:
                raise FunctionAccessDenied("Accounts may only be held in DEBUG mode")
            response = ""
            if account_id == "0":
                held = [int(k) for k, v in self.account_release_flags.items() if v is not None]
                response = "Holding sessions: {}".format(held)
            elif self.account_release_flags.get(account_id, None) is None:
                logger.warn("~~~ Creating release flag ~~~")
                self.account_release_flags[account_id] = threading.Event()
                release_flag = self.account_release_flags[account_id]
                release_flag.clear()
                logger.warn("+++ Getting session +++")
                session = None
                try:
                    try:
                        session = client_session.get_session(account_id)
                        with session.begin_transaction():
                            logger.warn("+++ Waiting for release flag +++")
                            release_flag.wait()
                            logger.warn("--- Got release flag ---")
                        response = "Done holding session on {}".format(account_id)
                    except Exception as e:
                        response = "Exception holding session on {}:\n{}".format(account_id, e)
                        self.account_release_flags[account_id] = None

                finally:
                    logger.warn("--- Closing session ---")
                    if session is not None:
                        session.close()

            else:
                logger.warn("--- Setting release flag ---")
                self.account_release_flags[account_id].set()
                self.account_release_flags[account_id] = None
                response = "Released session hold on {}".format(account_id)
            return self.json_response(response)

        @rest.route("/contact/:account_id#[0-9]+#/:id#[0-9]+#", method=["GET", "PUT", "DELETE"])
        def contact_by_id(client_session, account_id, id):
            if DEBUG and account_id == "666":
                return handle_hold_account(self, client_session, account_id=id)
            else:
                log_session_contact_access(client_session)
                if not account_id or int(account_id) <= 0:
                    return self.json_response("Invalid account id.", 500)
                if id is not None and int(id) <= 0:
                    return self.json_response("Invalid contact id.", 500)
                if bottle.request.method == "GET":
                    result = contact.get_contact_by_id(client_session, account_id, id)
                elif bottle.request.method == "PUT":
                    fav = bottle.request.GET.get("favourite")
                    if fav:
                        contact.favourite_contact(client_session, account_id, id, fav)
                        return self.json_response({}, 200)
                    data = self.json_request()
                    enterprise_only = boolean_get_paramater("enterprise", False)
                    result = contact.update_contact(client_session, account_id, id, data, enterprise_only=enterprise_only)
                elif bottle.request.method == "DELETE":
                    result = contact.delete_contact(client_session, account_id, id)
                else:
                    raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
                return self.json_response(result)

        @rest.route("/contact/:account_id#[0-9]+#/:id#[0-9]+#/attributes")
        def attributes_by_contact_id(client_session, account_id, id):
            log_session_contact_access(client_session)
            type = bottle.request.GET.get("type")
            data = contact.get_attributes_by_contact_id(client_session, account_id, id, type)
            return self.json_response(data)

        @rest.route("/contact/:account_id#[0-9]+#/:id#[0-9]+#/enhance")
        def enhance_contact(client_session, account_id, id):
            log_session_contact_access(client_session)
            data = []
            to_enhance = [(id, EnhancementType.ContactSave)]
            enhance_set = set(to_enhance)
            queue_enhancements(enhance_set, client_session=client_session)
            return self.json_response(data)

        @rest.route("/enhance", method=["GET", "PUT"])
        def enhancement_enabled(client_session):
            log_session_contact_access(client_session)
            data = {}
            if bottle.request.method == "GET":
                enabled = is_enhancement_enabled()
            elif bottle.request.method == "PUT":
                enable_param = bottle.request.GET.get("enable")
                logger.info("enable = %s", enable_param)
                enabled = False
                if enable_param is not None:
                    num = int(enable_param)
                    if num != 0:
                        enabled = True
                    enable_enhancement(enabled)
                else:
                    logger.error("Missing enable param in request")
            data["enabled"] = enabled
            return self.json_response(data, 200)

        @rest.route("/contact/:account_id#[0-9]+#/:id#[0-9]+#/photos", method=["GET", "POST"])
        def photos_by_contact(client_session, account_id, id):
            log_session_contact_access(client_session)
            if bottle.request.method == "GET":
                result = contact.get_photos_by_contact_id(client_session, account_id, id)
            elif bottle.request.method == "POST":
                data = self.json_request()
                result = contact.add_contact_photo(client_session, account_id, id, data)
            return self.json_response(result)

        @rest.route("/contact/photo/:account_id#[0-9]+#/:id#[0-9]+#/:scale#[0-2]#")
        def get_contact_photo_with_scale(client_session, account_id, id, scale):
            log_session_contact_access(client_session)
            content_type, photo = contact.get_photo_with_scale(client_session, account_id, id, scale)
            bottle.response.content_type = content_type
            return photo

        @rest.route("/contact/:account_id#[0-9]+#/:contact_id#[0-9]+#/photos/primary", method=["GET", "PUT"])
        def contact_primary_photo_handler(client_session, account_id, contact_id):
            log_session_contact_access(client_session)
            result = {}
            if bottle.request.method == "GET":
                result = contact.get_primary_photo(client_session, account_id, contact_id)
            elif bottle.request.method == "PUT":
                result = contact.set_primary_photo(client_session, account_id, contact_id, self.json_request().get("photo_id"))
            if not result:
                return self.json_response({}, 404)
            return self.json_response(result)

        @rest.route("/contact/:account_id#[0-9]+#/:contact_id#[0-9]+#/vcard", method=["GET"])
        def contact_vcard(client_session, account_id, contact_id):
            log_session_contact_access(client_session)
            try:
                data = contact.exportVCard(client_session, account_id, contact_id)
                bottle.response.content_type = "text/vcard"
                return data
            except Exception:
                return self.json_response({}, 404)

        @rest.route("/contact/tovcard", method=["POST"])
        def contact_to_vcard(client_session):
            log_session_contact_access(client_session)
            data = self.json_request()
            try:
                vcard_data = contact.JSONToVCard(client_session, data)
                bottle.response.content_type = "text/vcard"
                return vcard_data
            except Exception:
                return self.json_response("Invalid contact.", 500)

        @rest.route("/contact/vcard", method=["POST"])
        def contact_validate_vcard(client_session):
            log_session_contact_access(client_session)
            request = self.json_request()
            field = "vcard"
            vcard_data = ""
            data = "[]"
            if field in request:
                vcard_data = request[field]
                data = contact.vCardToJSON(client_session, vcard_data)
            return self.json_response(data)

        @rest.route("/contact/vcard_import", method=["POST"])
        def contact_import_from_vcard(client_session):
            log_session_contact_access(client_session)
            request = self.json_request()
            if "filepath" in request:
                contact.contact_import_from_vcard_file(client_session, request["filepath"])
            elif "vcard" in request:
                contact.contact_import_from_vcard_string(client_session, request["vcard"])
            return self.json_response({}, 200)

        @rest.route("/contact/vcard_export", method=["POST"])
        def contact_export_as_vcard(client_session):
            log_session_contact_access(client_session)
            ids = None
            vcards = None
            if "json" in bottle.request.POST:
                data = self.json_request()
                ids = data.get("ids")
            if ids:
                include_unified_id = boolean_get_paramater("include_unified_id", False)
                exclude_accounts = bottle.request.GET.get("exclude_accounts", None)
                try:
                    vcards = contact.exportVCards(client_session, UNIFIED_CONTACTS_ACCOUNTID, ids, include_unified_id, exclude_accounts)
                except Exception:
                    logger.exception("Fail to export contacts as vcards")
                    return self.json_response({}, 500)

            if not vcards:
                return self.json_response({}, 404)
            else:
                bottle.response.content_type = "text/vcard"
                return vcards

        @rest.route("/contact/photo/:account_id#[0-9]+#/:id#[0-9]+#")
        def get_contact_photo(client_session, account_id, id):
            log_session_contact_access(client_session)
            content_type, photo = contact.get_photo(client_session, account_id, id)
            bottle.response.content_type = content_type
            return photo

        @rest.route("/contact/:account_id#[0-9]+#/:id#[0-9]+#/groups")
        def groups_by_contact(client_session, account_id, id):
            log_session_contact_access(client_session)
            data = contact.get_groups_by_contact_id(client_session, account_id, id)
            return self.json_response(data)

        @rest.route("/contact/:account_id#[0-9]+#/:id#[0-9]+#/postal_addresses")
        def postal_addresses_by_contact(client_session, account_id, id):
            log_session_contact_access(client_session)
            data = contact.get_postal_addresses_by_contact_id(client_session, account_id, id)
            return self.json_response(data)

        @rest.route("/contact/:account_id#[0-9]+#/:contact_id#[0-9]+#/favourite_actions", method=["POST", "GET", "PUT", "DELETE"])
        def contact_favourite_actions(client_session, account_id, contact_id):
            log_session_contact_access(client_session)
            result = {}
            if bottle.request.method == "GET":
                result = contact.get_favourite_actions(client_session, account_id, contact_id)
            elif bottle.request.method == "POST":
                data = self.json_request()
                result = contact.update_favourite_actions(client_session, account_id, contact_id, data)
            elif bottle.request.method == "PUT":
                data = self.json_request()
                result = contact.create_favourite_actions(client_session, account_id, contact_id, data)
            elif bottle.request.method == "DELETE":
                data = self.json_request()
                result = contact.delete_favourite_actions(client_session, account_id, contact_id, data)
            else:
                raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
            return self.json_response(result)

        @rest.route("/contacts/search_autocomplete")
        def contacts_search_autocomplete(client_session):
            log_session_contact_access(client_session)
            data = contact.search_contacts(client_session, bottle.request.GET, True)
            logger.debug("search_contacts_autocomplete found %d contacts" % len(data))
            return self.json_response(data)

        @rest.route("/contacts/is_enterprise")
        def is_enterprise(client_session):
            log_session_contact_access(client_session)
            field = bottle.request.GET.get("field")
            value = bottle.request.GET.get("value")
            if field and value:
                data = contact.is_enterprise(client_session, field, value)
                return self.json_response(data)
            else:
                return self.json_response("Missing or invalid required parameter(s): field,value", 500)

        @rest.route("/contacts/search", method=["GET", "POST"])
        def contacts_search(client_session):
            log_session_contact_access(client_session)
            exclude_ids = None
            if "json" in bottle.request.POST:
                data = self.json_request()
                if data:
                    exclude_ids = data.get("exclude_ids")
            data = contact.search_contacts(client_session, bottle.request.GET, exclude_ids=exclude_ids)
            return self.json_response(data)

        @rest.route("/contacts/search/batch", method=["POST"])
        def contacts_search_batch(client_session):
            log_session_contact_access(client_session)
            exclude_ids = None
            search_values = None
            if "json" in bottle.request.POST:
                data = self.json_request()
                if data:
                    exclude_ids = data.get("exclude_ids")
                    search_values = data.get("values")
            data = contact.search_contacts_batch(client_session, bottle.request.GET, search_values, exclude_ids=exclude_ids)
            return self.json_response(data)

        @rest.route("/contacts/search_phone")
        def contacts_search_phone(client_session):
            log_session_contact_access(client_session)
            params = bottle.request.GET
            if params.get("mnemonic"):
                data = contact.contacts_search_phone_mnemonic(client_session, params)
            else:
                data = contact.contacts_search_phone(client_session, params)
            return self.json_response(data)

        @rest.route("/contacts/search_phone/batch", method=["POST"])
        def contacts_search_phone_batch(client_session):
            log_session_contact_access(client_session)
            search_values = None
            if "json" in bottle.request.POST:
                data = self.json_request()
                if data:
                    search_values = data.get("values")
            data = contact.contacts_search_phone_batch(client_session, bottle.request.GET, search_values)
            return self.json_response(data)

        @rest.route("/contacts/is_remote_search_available")
        def contacts_is_remote_search_available(client_session):
            log_session_contact_access(client_session)
            return self.json_response(contact.is_remote_search_available(client_session))

        @rest.route("/contacts/is_remote_search_available_account/:account_id#[0-9]+#")
        def contacts_is_remote_search_available_account(client_session, account_id):
            log_session_contact_access(client_session)
            return self.json_response(contact.is_remote_search_available_account(client_session, account_id))

        @rest.route("/contacts/get_remote_search_accounts")
        def contacts_get_remote_search_accounts(client_session):
            log_session_contact_access(client_session)
            account_ids = [a.id for a in contact.get_remote_search_supported_accounts(client_session)]
            return self.json_response(account_ids)

        @rest.route("/contacts/remote_search")
        def contacts_remote_search(client_session):
            log_session_contact_access(client_session)
            query = bottle.request.GET.get("value")
            start_index = bottle.request.GET.get("start_index")
            end_index = bottle.request.GET.get("end_index")
            account_id = bottle.request.GET.get("account")
            if query:
                return self.json_response(contact.remote_search(client_session, query, start_index, end_index, account_id))
            return self.json_response({}, 200)

        @rest.route("/contacts/groups/:account_id#[0-9]+#")
        def contact_groups(client_session, account_id):
            log_session_contact_access(client_session)
            data = contact.get_groups(client_session, account_id, bottle.request.GET)
            return self.json_response(data)

        @rest.route("/contacts/group/:account_id#[0-9]+#/:group_id#[0-9]+#/contacts", method=["GET", "POST"])
        def contacts_by_group(client_session, account_id, group_id):
            log_session_contact_access(client_session)
            if bottle.request.method == "GET":
                data = contact.get_contacts_by_group_id(client_session, account_id, group_id, bottle.request.GET)
                return self.json_response(data)
            else:
                data = self.json_request()
                ids = data.get("contacts")
                result = contact.add_contacts_to_group(client_session, account_id, group_id, ids)
                return self.json_response(result)

        @rest.route("/contacts/groups/:account_id#[0-9]+#", method="POST")
        def contacts_group_create(client_session, account_id):
            log_session_contact_access(client_session)
            data = self.json_request()
            result = contact.create_contact_group(client_session, account_id, data)
            return self.json_response(result)

        @rest.route("/contacts/group/:account_id#[0-9]+#/:group_id#[0-9]+#/contacts/:contact_id#[0-9]+#", method="DELETE")
        def contacts_remove_from_group(client_session, account_id, group_id, contact_id):
            log_session_contact_access(client_session)
            result = contact.contacts_remove_from_group(client_session, account_id, group_id, contact_id)
            return self.json_response(result)

        @rest.route("/contacts/group/:account_id#[0-9]+#/:group_id#[0-9]+#", method=["GET", "PUT", "DELETE"])
        def contact_group_by_id(client_session, account_id, group_id):
            log_session_contact_access(client_session)
            if not account_id or int(account_id) <= 0:
                return self.json_response("Invalid account id.", 500)
            else:
                if group_id is not None and int(group_id) <= 0:
                    return self.json_response("Invalid contact id.", 500)
                if bottle.request.method == "GET":
                    result = contact.get_contact_group_by_id(client_session, account_id, group_id)
                elif bottle.request.method == "PUT":
                    data = self.json_request()
                    updated_group = data.get("group")
                    removed_contact_ids = data.get("removed_contact_ids")
                    added_contact_ids = data.get("added_contact_ids")
                    memberships = data.get("memberships")
                    result = contact.update_contact_group(client_session, account_id, group_id, updated_group, removed_contact_ids, added_contact_ids, memberships)
                elif bottle.request.method == "DELETE":
                    result = contact.delete_contact_group(client_session, account_id, group_id)
                else:
                    raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
                return self.json_response(result)

        @rest.route("/contacts/group/:account_id#[0-9]+#/:group_id#[0-9]+#/memberships", method=["GET", "POST", "PUT"])
        def contact_group_memberships(client_session, account_id, group_id):
            log_session_contact_access(client_session)
            result = None
            if bottle.request.method == "GET":
                result = contact.contact_group_membership(client_session, account_id, group_id)
            elif bottle.request.method == "POST":
                data = self.json_request()
                memberships = data.get("memberships")
                result = contact.add_contacts_to_group_with_attributes(client_session, account_id, group_id, memberships)
            elif bottle.request.method == "PUT":
                data = self.json_request()
                memberships = data.get("memberships")
                result = contact.update_group_membership(client_session, account_id, group_id, memberships)
            else:
                raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
            return self.json_response(result)

        @rest.route("/contacts/group/:account_id#[0-9]+#/:group_id#[0-9]+#/memberships/:contact_id#[0-9]+#", method=["GET"])
        def contact_group_memberships(client_session, account_id, group_id, contact_id):
            log_session_contact_access(client_session)
            result = contact.contact_group_membership_by_contact_id(client_session, account_id, group_id, contact_id)
            return self.json_response(result)

        @rest.route("/contacts/group/:account_id#[0-9]+#/:group_id#[0-9]+#/attributes", method=["GET"])
        def contact_group_attributes(client_session, account_id, group_id):
            log_session_contact_access(client_session)
            result = contact.contact_group_attributes(client_session, account_id, group_id)
            return self.json_response(result)

        @rest.route("/contact/merge", method=["PUT"])
        def contacts_merge(client_session):
            log_session_contact_access(client_session)
            data = self.json_request()
            ids = data.get("ids")
            status = 500
            result = None
            if bottle.request.method == "PUT" and ids is not None and isinstance(ids, list) and len(ids) > 1:
                status = 200
                result = contact.merge_contacts(client_session, ids)
            else:
                result = 'Missing or invalid required parameters in the json body: {"ids":[1,2,3]}'
            return self.json_response(result, status)

        @rest.route("/contact/:id#[0-9]+#/unmerge", method=["PUT"])
        def contacts_unmerge(client_session, id):
            log_session_contact_access(client_session)
            data = self.json_request()
            contacts_list = data["contacts"]
            contact.unmerge_contacts(client_session, id, contacts_list)
            return self.json_response({}, 200)

        @rest.route("/contact/:id#[0-9]+#/merged")
        def merged_contacts(client_session, id):
            log_session_contact_access(client_session)
            data = contact.get_merged_contacts(client_session, id)
            return self.json_response(data)

        @rest.route("/contacts/automerge", method=["GET", "PUT"])
        def contacts_automerge(client_session):
            log_session_contact_access(client_session)
            if bottle.request.method == "GET":
                unified_account = get_account(client_session, UNIFIED_CONTACTS_ACCOUNTID)
                result = unified_account.rpc.is_automerge_enabled()
                return self.json_response({"enabled": (1 if result else 0)}, 200)
            if bottle.request.method == "PUT":
                enable = bottle.request.GET.get("enable")
                update = bottle.request.GET.get("update")
                if enable and update:
                    unified_account = get_account(client_session, UNIFIED_CONTACTS_ACCOUNTID)
                    data = {"enable": enable,  "update": update}
                    unified_account.rpc.automerge_enable(**data)
                    return self.json_response({}, 200)
            return self.json_response("'Missing or invalid required parameters", 500)

        @rest.route("/contact/activity/:id#[0-9]+#")
        def contact_activity(client_session, id):
            log_session_contact_access(client_session)
            activity_filter = int(bottle.request.GET.get("filter", "2147483647"))
            data = contact.get_contact_activity(client_session, id, activity_filter)
            return self.json_response(data)

        @rest.route("/contact/preview_merge")
        def contact_merge_preview(client_session):
            log_session_contact_access(client_session)
            ids = bottle.request.GET.get("ids")
            if ids is not None:
                ids = ids.split(",")
            json_response = None
            if ids is None or len(ids) < 2:
                json_response = self.json_response("Missing or invalid required parameter(s): ids", 500)
            else:
                data = contact.get_merge_preview(client_session, ids)
                json_response = self.json_response(data)
            return json_response

        @rest.route("/contact/online_status/:contact_id#[0-9]+#")
        def contact_online_status(client_session, contact_id):
            log_session_contact_access(client_session)
            data = contact.get_online_status(client_session, contact_id)
            logger.debug("<PII9> social online status update: %s", data)
            return self.json_response(data)

        @rest.route("/contact/online_status/:account_id#[0-9]+#/:contact_id#[0-9]+#")
        def contact_online_status_for_account(client_session, account_id, contact_id):
            log_session_contact_access(client_session)
            data = contact.get_online_status_for_account(client_session, account_id, contact_id)
            logger.debug("<PII9> social online status update: %s", data)
            return self.json_response(data)

        @rest.route("/contact/sim_export", method=["POST"])
        def contact_export_to_sim(client_session):
            log_session_contact_access(client_session)
            data = contact.export_to_sim(client_session)
            return self.json_response(data)

        @rest.route("/contact/sim_import", method=["POST"])
        def contact_import_from_sim(client_session):
            log_session_contact_access(client_session)
            data = contact.import_from_sim(client_session)
            return self.json_response(data)

        @rest.route("/contact/:account_id#[0-9]+#/clear_log", method=["POST"])
        def contact_clear_log(client_session, account_id):
            log_session_contact_access(client_session)
            return self.json_response(contact.delete_sync_log(client_session, account_id))

        @rest.route("/contact/:account_id#[0-9]+#/sync", method=["POST"])
        def contact_sync(client_session, account_id):
            log_session_contact_access(client_session)
            return self.json_response(contact.contact_sync(client_session, account_id))

        @rest.route("/bblink/contacts/:account_id#[0-9]+#", method=["GET", "POST"])
        def contact_batch_bblink(client_session, account_id):
            log_session_contact_access(client_session)
            result = {}
            if bottle.request.method == "GET":
                result = "GET will return a batch of contacts"
                contacts = bblink.get_contacts(client_session, account_id, bottle.request.GET)
                return self.json_response(contacts)
            if bottle.request.method == "POST":
                data = self.json_request()
                logger.debug("Contacts from the POST is %s", data)
                if data:
                    if len(data) > 1000:
                        raise ContactRequestedSizeTooLarge("contacts batch create received too many contacts")
                    logger.debug("Contacts from the PUT is %s account_id %s", data, account_id)
                    result = bblink.bblink_contact_new_update_batch(client_session, account_id, data)
                    logger.debug("Result after inserting to contacts DB is %s", result)
            if not result:
                return self.json_response({}, 404)
            return self.json_response(result, 200)

        @rest.route("/bblink/contacts/:account_id#[0-9]+#/get", method=["POST"])
        def bblink_contacts_get_by_ids(client_session, account_id):
            log_session_contact_access(client_session)
            ids = None
            if "json" in bottle.request.POST:
                data = self.json_request()
                ids = data.get("ids")
            contacts = bblink.get_contacts(client_session, account_id, data, ids)
            return self.json_response(contacts)

        @rest.route("/bblink/contacts/:account_id#[0-9]+#/delete", method=["POST"])
        def bblink_batch_delete_contacts(client_session, account_id):
            log_session_contact_access(client_session)
            data = self.json_request()
            logger.info("IDS for delete are %s", data)
            result = {}
            if data:
                result = bblink.bblink_contacts_batch_delete(client_session, account_id, data)
            if not result:
                return self.json_response({}, 404)
            return self.json_response(result, 200)

        @rest.route("/bblink/contacts/:account_id#[0-9]+#/sync", method=["PUT", "GET"])
        def bblink_contacts_sync(client_session, account_id):
            log_session_contact_access(client_session)
            result = {}
            if bottle.request.method == "GET":
                result = bblink.contacts_sync_get(client_session, account_id)
                return self.json_response(result, 200)
            if bottle.request.method == "PUT":
                data = self.json_request()
                result = bblink.contacts_sync(client_session, account_id, data["updated_time"])
                return self.json_response(result)
            if not result:
                return self.json_response({}, 404)

        @rest.route("/bblink/contacts/:account_id#[0-9]+#/photos", method=["POST"])
        def bblink_contacts_photos(client_session, account_id):
            log_session_contact_access(client_session)
            result = {}
            if bottle.request.method == "POST":
                data = self.json_request()
                if data:
                    logger.info("Data is %s", data)
                    result = bblink.put_contacts_photo(client_session, account_id, data)
                    return self.json_response(result)
            return self.json_response(result, 404)

        @rest.route("/mail/ooo/:account_id#[0-9]+#", method=["GET", "PUT", "POST"])
        def mail_out_of_office(client_session, account_id):
            if bottle.request.method == "GET":
                result = message.get_out_of_office(client_session, account_id)
                return self.json_response(result)
            if bottle.request.method == "PUT" or bottle.request.method == "POST":
                data = self.json_request()
                result = message.set_out_of_office(client_session, account_id, data)
                return self.json_response(result)

        @rest.route("/mail/changes/:account_id#[0-9]+#")
        def mail_changes(client_session, account_id):
            try:
                limit = int(bottle.request.GET.get("limit", "0"))
                logger.info("changes limit:%d", limit)
            except:
                limit = None

            changes = message.get_changes(client_session, account_id, limit)
            return self.json_response(changes)

        @rest.route("/mail/changes/:account_id#[0-9]+#/count")
        def mail_changes_count(client_session, account_id):
            count = message.get_changes_count(client_session, account_id)
            return self.json_response({"count": count})

        @rest.route("/mail/changes/:account_id#[0-9]+#", method="POST")
        def mail_changes_clear(client_session, account_id):
            transaction_id = bottle.request.POST.get("transaction_id")
            result = message.clear_changes(client_session, account_id, transaction_id)
            return self.json_response(result)

        @rest.route("/mail/folders")
        def mail_list_folders(client_session):
            folders = message.list_folders(client_session)
            return self.json_response(folders)

        @rest.route("/mail/folders/:account_id#[0-9]+#")
        def mail_folders_by_account(client_session, account_id):
            folders = message.list_folders(client_session, account_id)
            return self.json_response(folders)

        @rest.route("/mail/folder/suggest/:account_id#[0-9]+#/:id#[0-9]+#")
        def suggest_folder_for_filing(client_session, account_id, id):
            result = message.suggest_folder_for_filing(client_session, account_id, id)
            return self.json_response(result)

        @rest.route("/mail/folder/suggest/id/:account_id#[0-9]+#/:id#[0-9]+#")
        def suggest_folder_id_for_filing(client_session, account_id, id):
            result = message.suggest_folder_id_for_filing(client_session, account_id, id)
            return self.json_response(result)

        @rest.route("/mail/folder/:account_id#[0-9]+#/:id#[0-9]+#")
        def mail_folder(client_session, account_id, id):
            folder = message.get_folder(client_session, account_id, id)
            return self.json_response(folder)

        @rest.route("/mail/folder/:account_id#[0-9]+#/:id#[0-9]+#/sync")
        def mail_sync_folder(client_session, account_id, id):
            return message.sync_folder(client_session, account_id, id)

        @rest.route("/mail/folder/:account_id#[0-9]+#/:id#[0-9]+#/empty")
        def mail_empty_folder(client_session, account_id, id):
            return message.empty_folder(client_session, account_id, id)

        @rest.route("/mail/folder/:account_id#[0-9]+#/:id#[0-9]+#/sync/configuration/:sync_config#[0-1]+#", method="POST")
        def mail_folder_sync_config(client_session, account_id, id, sync_config):
            return message.folder_sync_config(client_session, account_id, id, sync_config)

        def _mail_list_messages(client_session, account_id=None, folder_id=None, conversation_id=None):
            data = bottle.request.GET
            if account_id is not None:
                account_id = int(account_id)
            id_qs, ids = _get_ids_data(data)
            params = {}
            filter = "deleted = 0"
            if folder_id:
                filter += " AND folder_id = :folder_id"
                params["folder_id"] = int(folder_id)
            if conversation_id:
                filter += " AND conversation_id = :conversation_id"
                params["conversation_id"] = int(conversation_id)
            if ids:
                filter += " AND id IN " + id_qs
                params.update(ids)
            args = self.read_anchor_args(data, ["date_sent"], ["DESC"], {})
            args["filter"] = filter
            args["params"] = params
            msgs = message.list_messages_anchor(client_session, account_id=account_id, **args)
            remove_secure_attachments_from_secure_messages(account_id, msgs)
            return self.json_response(msgs)

        def _mail_list_messages_unified(client_session, account_id=None, conversation_id=None):
            data = bottle.request.GET
            if account_id is not None:
                account_id = int(account_id)
            args = self.read_anchor_args(data, ["date_sent"], ["DESC"], {})
            del args["filter"]
            del args["params"]
            show_sent = data.get("show_sent", "1") == "1"
            show_foldered = data.get("show_foldered", "0") == "1"
            show_enterprise = data.get("show_enterprise", "1") == "1"
            id_data = _get_ids_data(data)
            msgs = message.list_messages_anchor_unified(client_session, account_id=account_id, conversation_id=conversation_id, show_sent=show_sent, show_foldered=show_foldered, show_enterprise=show_enterprise, id_data=id_data, **args)
            return self.json_response(msgs)

        @rest.route("/mail/messages")
        def mail_list_messages(client_session):
            return _mail_list_messages_unified(client_session)

        @rest.route("/mail/messages/:account_id#[0-9]+#")
        def mail_list_messages_by_account(client_session, account_id):
            return _mail_list_messages_unified(client_session, account_id)

        @rest.route("/mail/folders/sync/:account_id#[0-9]+#", method="POST")
        def mail_folders_to_sync(client_session, account_id):
            data = self.json_request()
            return message.mail_folders_to_sync(client_session, account_id, data)

        @rest.route("/mail/folders/change_type/:account_id#[0-9]+#", method="POST")
        def mail_folders_change_type(client_session, account_id):
            data = self.json_request()
            return message.mail_folders_change_type(client_session, account_id, data)

        @rest.route("/contact/folders/:account_id#[0-9]+#")
        def contact_list_folders(client_session, account_id):
            folders = contact.list_folders(client_session, account_id)
            response = self.json_response(folders)
            logger.debug("contact_list_folders: %s", response)
            return response

        @rest.route("/contact/folders/sync/:account_id#[0-9]+#", method="POST")
        def contact_folders_to_sync(client_session, account_id):
            data = self.json_request()
            logger.debug("contact_folders_to_sync: %s", data)
            return contact.contact_folders_to_sync(client_session, account_id, data)

        @rest.route("/mail/folders/hierarchy/:account_id#[0-9]+#")
        def mail_folders_hierarchy(client_session, account_id):
            folders = message.mail_folders_hierarchy(client_session, account_id)
            if folders is not None:
                return self.json_response(folders)
            else:
                return {"result": "not_ready"}
                return

        @rest.route("/mail/messages/delete_prior", method="POST")
        def mail_delete_prior(client_session):
            data = self.json_request()
            return message.delete_prior(client_session, data)

        @rest.route("/mail/messages/bulk_mark_read/:flag#[0-1]#", method="POST")
        def mail_bulk_mark_read(client_session, flag):
            data = self.json_request()
            return message.bulk_mark_read_unread(client_session, flag, data)

        @rest.route("/mail/messages/search/prior_op", method="POST")
        def mail_search_view_prior_op(client_session):
            data = self.json_request()
            local_tz = self.settings_service.tzname
            return message.mail_search_view_prior_op(client_session, local_tz, data, True)

        @rest.route("/mail/messages/unified/prior_op", method="POST")
        def mail_unifiedview_prior_op(client_session):
            data = self.json_request()
            return message.mail_unified_view_prior_op(client_session, data, True)

        @rest.route("/mail/messages/prior_op/:account_id#[0-9]+#/:folder_id#[0-9]+#", method="POST")
        def mail_folder_prior_op(client_session, account_id, folder_id):
            data = self.json_request()
            return message.mail_folder_prior_op(client_session, account_id, folder_id, data, True)

        @rest.route("/mail/messages/multi_delete", method="POST")
        def mail_messages_multi_delete(client_session):
            data = self.json_request()
            return message.mail_messages_multi_delete(client_session, data, True)

        @rest.route("/mail/messages/multi_update", method="POST")
        def mail_messages_multi_update(client_session):
            data = self.json_request()
            return message.mail_messages_multi_update(client_session, data, True)

        @rest.route("/mail/messages/:account_id#[0-9]+#/conversation/:conversation_id#[0-9]+#")
        def mail_list_messages_by_conversation(client_session, account_id, conversation_id):
            return _mail_list_messages_unified(client_session, account_id, conversation_id=conversation_id)

        @rest.route("/mail/messages/:account_id#[0-9]+#/:folder_id#[0-9]+#")
        def mail_list_messages_by_folder(client_session, account_id, folder_id):
            return _mail_list_messages(client_session, account_id, folder_id=folder_id)

        @rest.route("/mail/messages/:account_id#[0-9]+#/:folder_id#[0-9]+#/conversation/:conversation_id#[0-9]+#")
        def mail_list_messages_by_folder_and_conversation(client_session, account_id, folder_id, conversation_id):
            return _mail_list_messages(client_session, account_id, folder_id=folder_id, conversation_id=conversation_id)

        @rest.route("/mail/messages/:account_id#[0-9]+#/sync")
        def mail_sync_messages(client_session, account_id):
            return message.sync_messages(client_session, account_id)

        @rest.route("/mail/draft/:account_id#[0-9]+#", method="POST")
        def mail_message_draft(client_session, account_id):
            return self._save_or_send_message(client_session, account_id, "save", 0)

        def _mail_list_attachments(client_session, account_id, conversation_id=None):
            data = bottle.request.GET
            if account_id is not None:
                account_id = int(account_id)
            args = self.read_anchor_args(data, ["date_sent"], ["DESC"], {})
            filter = "deleted=0 AND hidden=0"
            filter += ' AND mimetype NOT LIKE "application/ics%" AND mimetype NOT LIKE "text/calendar%"'
            if SMIME_supported:
                for mtype in SMIME_ATTACHMENT_CONTENT_TYPES:
                    filter += ' AND mimetype NOT LIKE "%s%%"' % mtype

                filter += ' AND NOT (mimetype is "" AND name in (%s))' % ",".join('"%s"' % x for x in SMIME_ATTACHMENT_EXTENDED_NAMES)
            params = {}
            if conversation_id is not None:
                filter += " AND conversation_id=:conversation_id"
                params["conversation_id"] = int(conversation_id)
            search = data.get("search", None)
            if search is not None:
                search += "%"
                filter += " AND name LIKE :search"
                params["search"] = search
            include_inline = int(data.get("include_inline", 0))
            if not include_inline:
                filter += " AND inline=0"
            if include_inline == 2:
                filter += " AND inline=1"
            args["filter"] = filter
            args["params"] = params
            attachments = message.list_attachments_anchor(client_session, account_id, **args)
            return self.json_response(attachments)

        @rest.route("/mail/attachments")
        def mail_list_attachments(client_session):
            return _mail_list_attachments(client_session, None)

        @rest.route("/mail/attachments/:account_id#[0-9]+#")
        def mail_list_attachments_by_accountid(client_session, account_id):
            return _mail_list_attachments(client_session, account_id)

        @rest.route("/mail/attachments/:account_id#[0-9]+#/conversation/:conversation_id#[0-9]+#")
        def mail_list_attachments_by_conversationid(client_session, account_id, conversation_id):
            return _mail_list_attachments(client_session, account_id, conversation_id)

        def _mail_list_conversations_unified(client_session, account_id):
            data = bottle.request.GET
            if account_id is not None:
                account_id = int(account_id)
            args = self.read_anchor_args(data, ["date_sent"], ["DESC"], {})
            del args["filter"]
            del args["params"]
            show_sent = data.get("show_sent", "1") == "1"
            show_foldered = data.get("show_foldered", "0") == "1"
            id_data = _get_ids_data(data)
            convs = message.list_conversations_anchor_unified(client_session, account_id=account_id, show_sent=show_sent, show_foldered=show_foldered, id_data=id_data, **args)
            return self.json_response(convs)

        @rest.route("/mail/conversations")
        def mail_list_conversations(client_session):
            return _mail_list_conversations_unified(client_session, None)

        @rest.route("/mail/conversations/:account_id#[0-9]+#")
        def mail_list_conversations(client_session, account_id):
            return _mail_list_conversations_unified(client_session, account_id)

        @rest.route("/mail/conversations/:account_id#[0-9]+#/multi")
        def mail_get_multiple_conversations(client_session, account_id):
            return _mail_list_conversations_unified(client_session, account_id)

        @rest.route("/mail/conversations/:account_id#[0-9]+#/:folder_id#[0-9]+#")
        def mail_list_conversations_by_folder(client_session, account_id, folder_id):
            data = bottle.request.GET
            if account_id is not None:
                account_id = int(account_id)
            filter = "folder_id = :folder_id"
            params = {"folder_id": (int(folder_id))}
            id_qs, ids = _get_ids_data(data)
            if ids:
                filter += " AND conversation_id IN " + id_qs
                params.update(ids)
            args = self.read_anchor_args(data, ["date_sent"], ["DESC"], {})
            args["filter"] = filter
            args["params"] = params
            convs = message.list_conversations_anchor_folder(client_session, account_id=account_id, **args)
            return self.json_response(convs)

        @rest.route("/mail/conversation/:account_id#[0-9]+#/:id#[0-9]+#", method=["GET", "PUT", "DELETE"])
        def mail_conversation(client_session, account_id, id):
            if bottle.request.method == "GET":
                result = message.get_conversation(client_session, account_id, id)
            elif bottle.request.method == "PUT":
                data = self.json_request()
                result = message.update_conversation(client_session, account_id, id, data, True)
            elif bottle.request.method == "DELETE":
                result = message.delete_conversation(client_session, account_id, id, True)
            else:
                raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
            return self.json_response(result)

        @rest.route("/mail/certificate/ldap_ocsp/:account_id#[0-9]+#/:request_id#[0-9]+#", method=["POST", "DELETE"])
        def certificate_ldap_ocsp_request(client_session, account_id, request_id):
            secure_email_api_access_check(client_session)
            initialize_secure_email()
            result = {"status": "failure"}
            request_id = int(request_id)
            if bottle.request.method == "POST":
                request_type = int(bottle.request.POST.get("request_type", default=0))
                from pim.utils.secureemail.base.secureemail_certificate_utils import CertificateUtils
                if request_type & CertificateUtils.LDAP_REQUEST:
                    encoding_type = int(bottle.request.POST.get("encoding_type", default=EncodingType.UNINITIALIZED))
                    emails_list = json.loads(bottle.request.POST.get("recipient_list", default="[]"))
                    result = certs_ldap_request(account_id, request_id, request_type, encoding_type, emails_list)
                elif request_type & CertificateUtils.OCSP_REQUEST:
                    certs_list = json.loads(bottle.request.POST.get("certs", default="[]"))
                    encoding_type = int(bottle.request.POST.get("encoding_type", default=EncodingType.UNINITIALIZED))
                    encoding_action = int(bottle.request.POST.get("encoding_action", EncodingAction.SIGN_AND_ENCRYPT))
                    result = certs_ocsp_request(account_id, request_id, certs_list, encoding_action, encoding_type)
            elif bottle.request.method == "DELETE":
                result = certs_clean_cache_request(account_id, request_id)
            return self.json_response(result)

        @rest.route("/mail/certificate/import/:account_id#[0-9]+#", method=["POST"])
        def mail_import_certificate(client_session, account_id):
            secure_email_api_access_check(client_session)
            initialize_secure_email()
            filepath = json.loads(bottle.request.POST.get("filepath", default=None))
            password = json.loads(bottle.request.POST.get("password", default=None))
            fileext = json.loads(bottle.request.POST.get("fileext", default=None))
            result = import_cert(filepath, password, fileext, account_id)
            return self.json_response(result)

        @rest.route("/mail/certificate/view/:account_id#[0-9]+#", method=["POST"])
        def mail_view_certificate(client_session, account_id):
            secure_email_api_access_check(client_session)
            initialize_secure_email()
            id_type = bottle.request.POST.get("id_type", default=None)
            if id_type is not None:
                id_type = json.loads(id_type)
                id_string = json.loads(bottle.request.POST.get("id_string", default=None))
                encoding_type = int(bottle.request.POST.get("encoding_type", default=EncodingType.UNINITIALIZED))
                result = view_cert(None, id_type, id_string, None, None, account_id, encoding_type)
            else:
                filepath = json.loads(bottle.request.POST.get("filepath", default=None))
                password = json.loads(bottle.request.POST.get("password", default=None))
                fileext = json.loads(bottle.request.POST.get("fileext", default=None))
                result = view_cert(filepath, None, None, password, fileext, account_id)
            return self.json_response(result)

        @rest.route("/mail/certificate/retrieve/:account_id#[0-9]+#", method=["POST"])
        def mail_retrieve_personal_certificate(client_session, account_id):
            if not SMIME_supported:
                result = []
                return self.json_response(result)
            else:
                secure_email_api_access_check(client_session)
                initialize_secure_email()
                encoding_type = int(bottle.request.POST.get("encoding_type", default=EncodingType.SMIME))
                encoding_action = int(bottle.request.POST.get("encoding_action", default=EncodingAction.UNINITIALIZED))
                email = json.loads(bottle.request.POST.get("subject_email_address", default=None))
                from pim.utils.secureemail.base.secureemail_certificate_utils import CertificateUtils
                result = CertificateUtils.retrieve_personal_certificates(account_id, email, encoding_type, encoding_action)
                return self.json_response(result)

        @rest.route("/mail/securemessage/settings/:account_id#[0-9]+#", method=["GET", "PUT"])
        def mail_secure_message_settings(client_session, account_id):
            secure_email_api_access_check(client_session)
            if bottle.request.method == "GET":
                if is_secureemail_supported(account_id) and SMIME_supported:
                    initialize_secure_email()
                    options = get_secureemail_options(account_id)
                    result = options.get_json()
                else:
                    result = {"supported": False}
                return self.json_response(result)
            if bottle.request.method == "PUT":
                initialize_secure_email()
                settings = self.json_request()
                result = {}
                error = 0
                try:
                    set_secureemail_options(account_id, settings)
                except Exception as e:
                    logger.info(e)
                    error = -1

                result["status"] = error
                return self.json_response(result)

        @rest.route("/mail/securemessage/server_enroll/:account_id#[0-9]+#", method=["GET", "POST", "DELETE"])
        def mail_secure_message_server_enroll(client_session, account_id):
            if not SMIME_supported:
                return self.json_response({})
            else:
                secure_email_api_access_check(client_session)
                initialize_secure_email()
                status = False
                from pim.utils.secureemail.base.secureemail_server import SecureEmailServer
                server = SecureEmailServer(account_id)
                if bottle.request.method == "GET":
                    status = server.is_enrolled()
                elif bottle.request.method == "POST":
                    use_server_downloaded_key_request = bool(bottle.request.POST.get("use_key", False))
                    if use_server_downloaded_key_request:
                        status = server.use_server_downloaded_key()
                    else:
                        user_id = json.loads(bottle.request.POST.get("user_id", default=None))
                        password = json.loads(bottle.request.POST.get("password", default=None))
                        status = server.enroll(user_id, password)
                elif bottle.request.method == "DELETE":
                    status = server.abort_enroll()
                else:
                    raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
                result = {"status": status}
                return self.json_response(result)

        @rest.route("/mail/securemessage/server_policy/:account_id#[0-9]+#", method=["GET"])
        def mail_secure_message_server_policy(client_session, account_id):
            if not SMIME_supported:
                return self.json_response({})
            secure_email_api_access_check(client_session)
            initialize_secure_email()
            status = False
            policy = {}
            from pim.utils.secureemail.base.secureemail_server import SecureEmailServer
            server = SecureEmailServer(account_id)
            if bottle.request.method == "GET":
                status, policy = server.get_policy_json()
            else:
                raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
            result = {"status": status,  "policy": policy}
            return self.json_response(result)

        @rest.route("/mail/securemessage/:account_id#[0-9]+#/:id#[0-9]+#", method=["GET", "PUT", "DELETE"])
        def mail_secure_message(client_session, account_id, id):
            secure_email_api_access_check(client_session)
            initialize_secure_email()
            status = None
            if bottle.request.method == "GET":
                result, status = message.get_secure_message(client_session, account_id, id, bottle.request.GET.get("filepath", None))
            elif bottle.request.method == "PUT":
                data = self.json_request()
                result = message.update_secure_message(client_session, account_id, id, data)
            elif bottle.request.method == "DELETE":
                result = message.delete_attached_secure_message(client_session, account_id, bottle.request.GET.get("message_id", None))
            else:
                raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
            return self.json_response(result, status)

        @rest.route("/mail/message/prefetch/:account_id#[0-9]+#/:id#[0-9]+#", method="GET")
        def mail_message_prefetch(client_session, account_id, id):
            msg = message.get_message(client_session, account_id, id)
            self.cached_message = CachedMessageResponse(account_id, id, msg)
            body = msg.get("html_body_filename")
            if not body:
                body = msg.get("text_body_filename")
            try:
                if body:
                    with open(body, "rb") as f:
                        pass
            except Exception:
                logger.warn("Unable to prefetch the body file for account: %s message id: %s", account_id, id)

        @rest.route("/mail/message/:account_id#[0-9]+#/:id#[0-9]+#", method=["GET", "PUT", "DELETE"])
        def mail_message(client_session, account_id, id):
            cached_message = self.cached_message
            if bottle.request.method == "GET":
                if cached_message and cached_message.account_id == account_id and cached_message.message_id == id:
                    age = time.time() - cached_message.cached_time
                    if age < CachedMessageResponse.MAX_AGE:
                        return cached_message.json_message
                    self.cached_message = None
                return message.get_message(client_session, account_id, id)
            else:
                if cached_message and cached_message.account_id == account_id and cached_message.message_id == id:
                    self.cached_message = None
                if bottle.request.method == "PUT":
                    data = self.json_request()
                    agent = bottle.request.headers.get("User-Agent")
                    if agent:
                        data["fromCard"] = "EmailCard.so" in agent
                    else:
                        data["fromCard"] = False
                    result = message.update_message(client_session, account_id, id, data, True)
                elif bottle.request.method == "DELETE":
                    result = message.delete_message(client_session, account_id, id, True)
                else:
                    raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
                return self.json_response(result)
                return

        @rest.route("/mail/messages/:account_id#[0-9]+#/multi", method=["GET", "DELETE", "PUT"])
        def mail_get_multiple_messages(client_session, account_id):
            if bottle.request.method == "GET":
                d = bottle.request.GET.get("details")
                if d == "analyticd":
                    ids = bottle.request.GET.get("ids")
                    if ids:
                        resp = {}
                        account = Account(int(account_id))
                        db_context = None
                        if account.exists and account.enterprise:
                            db_context = DatabaseContextTypes.LOCK
                        with client_session.open_session(account_id, db_context_type=db_context) as s:
                            ids = list(map(int, ids.split(",")))
                            id_qmarks = (len(ids) * "?,")[:-1]
                            message_select = "\n                                SELECT  m.id, m.conversation_id, m.date_sent, m.title,\n                                        m.message_class, m.from_address, f.type\n                                FROM    Message         AS m\n                                JOIN    MessageFolder   AS f ON m.folder_id = f.id\n                                WHERE   m.deleted = 0\n                                    AND m.id IN ({})".format(id_qmarks)
                            attachment_select = "\n                                SELECT  a.message_id, a.id, a.mimetype, a.name, a.inline\n                                FROM    Message             AS m\n                                JOIN    MessageAttachment   AS a ON m.id = a.message_id\n                                WHERE   m.deleted = 0\n                                    AND m.id IN ({})".format(id_qmarks)
                            recipient_select = "\n                                SELECT  r.message_id, r.address\n                                FROM    Message             AS m\n                                JOIN    MessageRecipient    AS r ON m.id = r.message_id\n                                WHERE   m.deleted = 0\n                                    AND r.type != 'from'\n                                    AND m.id IN ({})".format(id_qmarks)
                            c = s.connection()
                            for m in c.execute(message_select, ids):
                                mess = dict(m)
                                mess["attachments"] = []
                                mess["recipients"] = []
                                name, addr = parseaddr(m.from_address)
                                mess["from_display_name"] = name
                                mess["from_addrspec"] = addr
                                del mess["from_address"]
                                resp[m.id] = mess

                            for a in c.execute(attachment_select, ids):
                                att = dict(a)
                                del att["message_id"]
                                resp[a.message_id]["attachments"].append(att)

                            for r in c.execute(recipient_select, ids):
                                name, addr = parseaddr(r.address)
                                resp[r.message_id]["recipients"].append({"display_name": name,  "addrspec": addr})

                        return self.json_response(list(resp.values()))
                elif "ids" in bottle.request.GET:
                    return _mail_list_messages(client_session, account_id)
                return self.json_response([], 200)
            else:
                if bottle.request.method == "DELETE":
                    data = self.json_request()
                    result = message.delete_messages(client_session, account_id, data)
                    return self.json_response(result)
                if bottle.request.method == "PUT":
                    data = self.json_request()
                    result = message.update_messages(client_session, account_id, data)
                    return self.json_response(result)
                raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
                return

        @rest.route("/mail/message/:account_id#[0-9]+#/:id#[0-9]+#/body")
        def mail_message_body(client_session, account_id, id):
            body_type = int(bottle.request.GET.get("body_type", "0"))
            get_partial_body = bool(int(bottle.request.GET.get("partial_body", "0")))
            logger.info("Requesting body for message id: %s (body type:%s)", id, body_type)
            result, content_type, body = message.get_message_body(client_session, account_id, id, body_type, get_partial_body)
            logger.info("Finished request for body for message id: %s", id)
            bottle.response.content_type = content_type
            if result == "success":
                logger.info("Returning body for message id: %s", id)
                bottle.response.status = 200
                return body
            else:
                if result == "downloading":
                    logger.info("Downloading body for message id: %s", id)
                    return self.json_response({"status": "downloading",  "id": id}, 202)
                logger.info("Error Requesting body for message id: %s", id)
                return self.json_response({"status": "error",  "id": id}, 500)

        @rest.route("/mail/message/:account_id#[0-9]+#", method="POST")
        def mail_message_send(client_session, account_id):
            raise HttpRouteGoneError(route=bottle.request.path)

        @rest.route("/mail/message/:account_id#[0-9]+#/send", method="POST")
        def mail_message_new(client_session, account_id):
            return self._save_or_send_message(client_session, account_id, "send", 0)

        @rest.route("/mail/message/:account_id#[0-9]+#/reply/:orig_msg_id#[0-9]+#", method="POST")
        def mail_message_reply(client_session, account_id, orig_msg_id):
            return self._save_or_send_message(client_session, account_id, "reply", orig_msg_id)

        @rest.route("/mail/message/:account_id#[0-9]+#/smartreply/:orig_msg_id#[0-9]+#", method="POST")
        def mail_message_smart_reply(client_session, account_id, orig_msg_id):
            return self._save_or_send_message(client_session, account_id, "smart_reply", orig_msg_id)

        @rest.route("/mail/message/:account_id#[0-9]+#/smartforward/:orig_msg_id#[0-9]+#", method="POST")
        def mail_message_smart_forward(client_session, account_id, orig_msg_id):
            return self._save_or_send_message(client_session, account_id, "smart_forward", orig_msg_id)

        @rest.route("/mail/message/:account_id#[0-9]+#/:id#[0-9]+#/attachments")
        def mail_message_list_attachments(client_session, account_id, id):
            return

        @rest.route("/mail/message/:account_id#[0-9]+#/attachment/:id#[0-9]+#")
        def mail_message_attachment(client_session, account_id, id):
            result, progress = message.download_attachment(client_session, account_id, id, immediate=True)
            http_code = 202 if result == "downloading" else 200
            return self.json_response({"status": result,  "id": id,  "progress": progress}, http_code)

        @rest.route("/mail/messages/search/local", method="GET")
        def mail_unified_local_search(client_session):
            return mail_local_search_by_folder(client_session, None, None)

        @rest.route("/mail/messages/search/local/:account_id#[0-9]+#", method="GET")
        def mail_local_search(client_session, account_id):
            return mail_local_search_by_folder(client_session, account_id, None)

        @rest.route("/mail/messages/search/local/:account_id#[0-9]+#/:folder_id#[0-9]+#", method="GET")
        def mail_local_search_by_folder(client_session, account_id, folder_id):
            search_terms = _get_search_terms_from_bottle()
            local_tz = self.settings_service.tzname
            msgs = message.local_search(client_session, account_id, folder_id, search_terms, local_tz)
            return self.json_response(msgs)

        @rest.route("/mail/messages/search/mail_is_remote_search_available/:account_id#[0-9]+#", method="GET")
        def mail_is_remote_search_available(client_session, account_id):
            return self.json_response(message.is_remote_search_available(client_session, account_id))

        @rest.route("/mail/messages/search/remote/:account_id#[0-9]+#", method="GET")
        def mail_remote_search(client_session, account_id):
            return mail_remote_search_by_folder(client_session, account_id, None)

        @rest.route("/mail/messages/search/remote/purge/:account_id#[0-9]+#", method="GET")
        def mail_purge_remote_results(client_session, account_id):
            return self.json_response(message.purge_remote_results(client_session, account_id))

        @rest.route("/mail/messages/search/remote/:account_id#[0-9]+#/:folder_id#[0-9]+#", method="GET")
        def mail_remote_search_by_folder(client_session, account_id, folder_id):
            search_terms = _get_search_terms_from_bottle()
            local_tz = self.settings_service.tzname
            msgs = message.remote_search(client_session, account_id, folder_id, search_terms, local_tz)
            return self.json_response(msgs)

        @rest.route("/mail/messages/search/universal", method="GET")
        def mail_universal_search(client_session):
            search_terms = _get_search_terms_from_bottle()
            msgs = message.universal_search(client_session, search_terms)
            return self.json_response(msgs)

        @rest.route("/mail/messages/move/:account_id#[0-9]+#/:msg_id#[0-9]+#/:target_id#[0-9]+#")
        def mail_message_move(client_session, account_id, msg_id, target_id):
            result = message.message_move(client_session, account_id, msg_id, target_id, True)
            return self.json_response(result)

        @rest.route("/mail/messages/move/multi/:account_id#[0-9]+#", method="POST")
        def mail_messages_move(client_session, account_id):
            data = self.json_request()
            result = message.messages_move(client_session, account_id, data, True)
            return self.json_response(result)

        @rest.route("/mail/folder/mail_is_folder_management_available/:account_id#[0-9]+#")
        def is_mail_folder_management_available(client_session, account_id):
            return self.json_response(message.is_mail_folder_management_available(client_session, account_id))

        @rest.route("/mail/folder/rename/:account_id#[0-9]+#/:id#[0-9]+#/:name")
        def mail_folder_rename(client_session, account_id, id, name):
            name = _convert_unicode(name)
            name = name.replace("%2F", "/")
            return message.mail_folder_rename(client_session, account_id, id, name)

        @rest.route("/mail/folder/add/:account_id#[0-9]+#/:id#[0-9]+#/:name")
        def mail_folder_add(client_session, account_id, id, name):
            name = _convert_unicode(name)
            name = name.replace("%2F", "/")
            return message.mail_folder_add(client_session, account_id, id, name)

        @rest.route("/mail/folder/delete/:account_id#[0-9]+#/:id#[0-9]+#")
        def mail_folder_delete(client_session, account_id, id):
            return message.mail_folder_delete(client_session, account_id, id)

        @rest.route("/mail/rights/templates/:account_id#[0-9]+#", method="GET")
        def mail_list_rights_management_templates(client_session, account_id):
            return self.json_response(message.get_rights_management_templates(client_session, account_id))

        @rest.route("/social/facebook/status", method="POST")
        def facebook_status(client_session):
            request = self.json_request()
            logger.debug("facebook_status %s", request)
            status = request.get("facebook-status")
            if not status:
                raise MissingDataError(key="facebook-status")
            logger.debug("status %s", status)
            res = social.facebook_status(client_session, status)
            return self.json_response(res)

        @rest.route("/social/linkedin/status", method="POST")
        def linkedin_status(client_session):
            request = self.json_request()
            status = request.get("linkedin-status")
            privacy = request.get("everyone")
            if not status:
                raise MissingDataError(key="linkedin-status")
            if not privacy:
                raise MissingDataError(key="everyone")
            everyone = True
            if privacy == "false":
                everyone = False
            res = social.linkedin_status(client_session, status, everyone)
            return self.json_response(res)

        @rest.route("/social/friendcount/:account_id#[0-9]+#/:user_id", method="GET")
        def friend_count(client_session, account_id, user_id):
            result = social.friend_count(client_session, account_id, user_id)
            logger.debug("friend_count result: %s", result)
            return self.json_response(result)

        @rest.route("/social/twitter/tweet", method="POST")
        def tweet(client_session):
            request = self.json_request()
            tweet = request.get("tweet")
            if not tweet:
                raise MissingDataError(key="tweet")
            logger.debug("tweet %s", tweet)
            res = social.tweet(client_session, tweet)
            return self.json_response(res)

        @rest.route("/social/status/retweet/:tweet_id", method="POST")
        def status_retweet(client_session, tweet_id):
            result = social.status_retweet(client_session, tweet_id)
            return self.json_response(result)

        @rest.route("/social/status/reply/:tweet_id", method="POST")
        def status_reply(client_session, tweet_id):
            request = self.json_request()
            status = request.get("status")
            if not status:
                raise MissingDataError(key="status")
            logger.debug("status_reply status and tweet id: %s, %s", status, tweet_id)
            result = social.status_reply(client_session, status, tweet_id)
            logger.debug("status_reply result: %s", result)
            return self.json_response(result)

        @rest.route("/social/sinaweibo/postweibo", method="POST")
        def postWeibo(client_session):
            request = self.json_request()
            newPost = request.get("send")
            imageUrl = request.get("imageUrl")
            if not newPost:
                raise MissingDataError(key="send")
            logger.debug("weibo %s", imageUrl)
            res = social.postWeibo(client_session, newPost, imageUrl)
            return self.json_response(res)

        @rest.route("/social/sinaweibo/getexpressions", method="GET")
        def getWeiboExpression(client_session):
            result = social.getWeiboExpression(client_session)
            logger.debug("sina weibo expressions result: %s", result)
            return self.json_response(result)

        @rest.route("/social/sinaweibo/repostweibo/:weibo_id", method="POST")
        def status_repostWeibo(client_session, weibo_id):
            request = self.json_request()
            repost = request.get("send")
            imageUrl = request.get("imageUrl")
            if not repost:
                raise MissingDataError(key="send")
            logger.debug("weibo %s", repost)
            result = social.status_repostWeibo(client_session, repost, weibo_id, imageUrl)
            return self.json_response(result)

        @rest.route("/social/sinaweibo/favorite/:weibo_id", method="POST")
        def favorite_weibo_action(client_session, weibo_id):
            result = social.favorite_weibo_action(client_session, weibo_id)
            logger.debug("favorite_action result: %s", result)
            return self.json_response(result)

        @rest.route("/social/sinaweibo/unfavorite/:weibo_id", method="POST")
        def unfavorite_weibo_action(client_session, weibo_id):
            result = social.unfavorite_weibo_action(client_session, weibo_id)
            logger.debug("unfavorite_action result: %s", result)
            return self.json_response(result)

        @rest.route("/social/friendrequest/:user_id", method="POST")
        def friend_request_action(client_session, user_id):
            accept_res = bottle.request.POST.get("accept")
            accept = bool(int(accept_res))
            logger.debug("friend_request_action accept: %s", accept)
            result = social.friend_request_action(client_session, user_id, accept)
            logger.debug("friend_request_action result: %s", result)
            return self.json_response(result)

        @rest.route("/social/eventrequest/:event_id", method="POST")
        def event_request_action(client_session, event_id):
            rsvp = bottle.request.POST.get("rsvp")
            logger.debug("event_request_action rsvp: %s", rsvp)
            result = social.event_request_action(client_session, event_id, rsvp)
            logger.debug("event_request_action result: %s", result)
            return self.json_response(result)

        @rest.route("/social/comment/:account_id#[0-9]+#/:object_id", method="POST")
        def comment_action(client_session, account_id, object_id):
            request = self.json_request()
            message = request.get("message")
            logger.debug("comment_action message: %s", message)
            if not message:
                raise MissingDataError(key="message")
            result = social.comment_action(client_session, account_id, object_id, message)
            logger.debug("comment_action result: %s", result)
            return self.json_response(result)

        @rest.route("/social/like/:account_id#[0-9]+#/:object_id", method="POST")
        def like_action(client_session, account_id, object_id):
            result = social.like_action(client_session, account_id, object_id)
            logger.debug("like_action result: %s", result)
            return self.json_response(result)

        @rest.route("/social/unlike/:account_id#[0-9]+#/:object_id", method="POST")
        def unlike_action(client_session, account_id, object_id):
            result = social.unlike_action(client_session, account_id, object_id)
            logger.debug("unlike_action result: %s", result)
            return self.json_response(result)

        @rest.route("/social/twitter/favorite/:tweet_id", method="POST")
        def favorite_action(client_session, tweet_id):
            result = social.favorite_action(client_session, tweet_id)
            logger.debug("favorite_action result: %s", result)
            return self.json_response(result)

        @rest.route("/social/twitter/unfavorite/:tweet_id", method="POST")
        def unfavorite_action(client_session, tweet_id):
            result = social.unfavorite_action(client_session, tweet_id)
            logger.debug("unfavorite_action result: %s", result)
            return self.json_response(result)

        @rest.route("/social/comments/:object_id", method="GET")
        def get_comments(client_session, object_id):
            result = social.get_comments(client_session, object_id)
            logger.debug("get_comments result: %r", result)
            return self.json_response(result)

        @rest.route("/social/addfriend/:account_id#[0-9]+#/:user_id", method="POST")
        def add_friend_action(client_session, account_id, user_id):
            params = self.json_request()
            result = social.add_friend_action(client_session, account_id, user_id, params)
            logger.debug("add_friend_action result: %s", result)
            return self.json_response(result)

        @rest.route("/social/lookup", method="POST")
        def social_contact_lookup(client_session):
            logger.debug("enter social_contact_lookup")
            request = self.json_request()
            enterpriseRequest = request.get("enterprise", 1)
            logger.info("enterprise field value :%d", enterpriseRequest)
            _assert_valid_lookup_api_request(enterpriseRequest, client_session)
            id = request.get("id")
            email = request.get("email")
            screen_name = request.get("screen_name")
            if not id and not email and not screen_name:
                raise MissingDataError(key="identifier(id or email or screen_name)")
            account_id = request.get("account_id")
            result = social.profile_lookup_action(client_session, account_id, request)
            return self.json_response(result)

        def _assert_valid_lookup_api_request(enterpriseRequest, client_session):
            if not isPimEnhancementAllowed():
                logger.info("pim enhancement disabled")
                if not client_session:
                    raise FunctionAccessDenied("invalid session, deny access")
                else:
                    hasSocialLookupAccess = client_session.has_social_lookup_access()
                    logger.info("hasSocialLookupAccess:%s  ", str(hasSocialLookupAccess))
                    if client_session.has_hybrid_access():
                        logger.info("client is a hybrid app")
                        if enterpriseRequest == 1:
                            raise FunctionAccessDenied("query is for enterprise data, deny access")
                        else:
                            logger.info("query is not for enterprise data, continue")
                    elif not hasSocialLookupAccess:
                        raise FunctionAccessDenied("no social lookup access is allowed. deny access")
                    else:
                        logger.info("social lookup access is allowed. continue")
            else:
                logger.info("pim enhancement is enabled, continue")

        def isPimEnhancementAllowed():
            logger.debug("enter isPimEnhancementAllowed")
            ENTERPRISE_POLICY_PPS = "/pps/system/perimeter/settings/1000-enterprise/policy"
            try:
                pps = PpsFile(ENTERPRISE_POLICY_PPS, wait=False, readonly=True)
            except Exception as ex:
                logger.exception("Exception while opening pps file %s: '%s'", ENTERPRISE_POLICY_PPS, ex)
                return True

            logger.debug("policy pps opened")
            if not pps.fd:
                logger.error("ERROR - couldn't open file %s in read-only mode - EXITING", ENTERPRISE_POLICY_PPS)
                return True
            logger.debug("valid policy pps fd")
            try:
                try:
                    pps_data = pps.read()
                except Exception as ex:
                    logger.exception("Unexpected exception. '%s' ", ex)
                    return True

            finally:
                pps.close()

            try:
                policyValue = pps_data["policy_block_pim_enhancement"]
                logger.info("found policy_block_pim_enhancement data: %s", policyValue)
                if policyValue in (b'on', b'1'):
                    logger.info("policy_block_pim_enhancement is set to true")
                    return False
            except Exception as ex:
                logger.info("no policy_block_pim_enhancement data found")
                return True

            return True

        @rest.route("/linking/ref/:domain#[0-9]+#/:euid", method="POST")
        def linking_reference_new(client_session, domain, euid):
            result = linking.reference_new(int(domain), euid)
            return self.json_response(result)

        @rest.route("/linking/ref/:domain#[0-9]+#", method="POST")
        def linking_reference_new_batch(client_session, domain):
            result = linking.reference_new_batch(int(domain), self.json_request())
            return self.json_response(result)

        @rest.route("/linking/ref/:domain#[0-9]+#/:euid", method="DELETE")
        def linking_reference_delete(client_session, domain, euid):
            bulk = int(bottle.request.GET.get("bulk", "0"))
            result = linking.reference_delete(int(domain), euid, bulk)
            return self.json_response(result)

        @rest.route("/linking/link/:domain1#[0-9]+#/:euid1/:domain2#[0-9]+#/:euid2", method="POST")
        def linking_link_new(client_session, domain1, euid1, domain2, euid2):
            symmetric = int(bottle.request.GET.get("symmetric", "0"))
            type_id = int(bottle.request.GET.get("type"))
            relationship_id = int(bottle.request.GET.get("relationship"))
            result = linking.link_new(int(domain1), euid1, int(domain2), euid2, type_id, relationship_id, symmetric)
            return self.json_response(result)

        @rest.route("/linking/link/:domain1#[0-9]+#/:euid1/:domain2#[0-9]+#", method="POST")
        def linking_link_new_batch(client_session, domain1, euid1, domain2):
            symmetric = int(bottle.request.GET.get("symmetric", "0"))
            type_id = int(bottle.request.GET.get("type"))
            relationship_id = int(bottle.request.GET.get("relationship"))
            euids = set(bottle.request.GET.get("euids", "").split(","))
            result = linking.link_new_batch(int(domain1), euid1, int(domain2), euids, type_id, relationship_id, symmetric)
            return self.json_response(result)

        @rest.route("/linking/link/:domain#[0-9]+#/:euid", method="GET")
        def linking_link_get(client_session, domain, euid):
            type_id = int(bottle.request.GET.get("type"))
            relationship_id = int(bottle.request.GET.get("relationship"))
            target_domain = int(bottle.request.GET.get("target_domain", 0))
            result = linking.link_get(int(domain), euid, type_id, relationship_id, target_domain)
            return self.json_response(result)

        @rest.route("/linking/link/domains/:d1#[0-9]+#/:d2#[0-9]+#", method="GET")
        def linking_link_get_by_domains(client_session, d1, d2):
            result = linking.link_get_by_domains(int(d1), int(d2))
            return self.json_response(result)

        @rest.route("/linking/link/:domain1#[0-9]+#/:euid1/:domain2#[0-9]+#/:euid2", method="DELETE")
        def linking_link_delete(client_session, domain1, euid1, domain2, euid2):
            result = linking.link_delete(int(domain1), euid1, int(domain2), euid2)
            return self.json_response(result)

        @rest.route("/tag/:type_id#[0-9]+#/:value", method="PUT")
        def tags_new(client_session, type_id, value):
            value = _convert_unicode(value)
            value = value.replace("%2F", "/")
            result = tag.tag_new(int(type_id), value)
            return self.json_response(result)

        @rest.route("/tag/:id#[0-9]+#", method="DELETE")
        def tags_delete(client_session, id):
            result = tag.tag_delete(int(id))
            return self.json_response(result)

        @rest.route("/tag/:id#[0-9]+#", method="POST")
        def tags_update(client_session, id):
            value = bottle.request.GET.get("value")
            result = tag.tag_update(int(id), value)
            return self.json_response(result)

        @rest.route("/tag/:id#[0-9]+#", method="GET")
        def tags_get(client_session, id):
            result = tag.tag_get(int(id))
            return self.json_response(result)

        @rest.route("/tag/type/:type_id#[0-9]+#", method="GET")
        def tags_get_by_type(client_session, type_id):
            result = tag.tag_get_by_type(int(type_id))
            return self.json_response(result)

        @rest.route("/analytics/drilldown/people", method="POST")
        def analytics_drilldown_people(client_session):
            request = None
            if "json" in bottle.request.POST:
                request = self.json_request()
            result = analytics.people_in_events(client_session, request)
            return self.json_response(result)

        @rest.route("/analytics/event/last", method="POST")
        def analytics_events_last(client_session):
            num = int(bottle.request.POST.get("n", 1))
            vis = int(bottle.request.POST.get("visible_only", "0"))
            res = {}
            for key, emails in self.json_request().items():
                r = analytics.last_n_events(client_session, vis, emails, num)
                if r:
                    res[key] = r
                    continue

            return self.json_response(res)

        @rest.route("/analytics/event/next", method="POST")
        def analytics_events_next(client_session):
            num = int(bottle.request.POST.get("n", 1))
            vis = int(bottle.request.POST.get("visible_only", "0"))
            res = {}
            for key, emails in self.json_request().items():
                r = analytics.next_n_events(client_session, vis, emails, num)
                if r:
                    res[key] = r
                    continue

            return self.json_response(res)

        @rest.route("/analytics/person/common", method="POST")
        def analytics_person_common(client_session):
            num = int(bottle.request.POST.get("n", 1))
            vis = int(bottle.request.POST.get("visible_only", "0"))
            res = {}
            for key, emails in self.json_request().items():
                r = analytics.common_people(client_session, vis, emails, num)
                if r:
                    res[key] = r
                    continue

            return self.json_response(res)

        @rest.route("/analytics/location/common", method="POST")
        def analytics_location_common(client_session):
            num = int(bottle.request.POST.get("n", 1))
            vis = int(bottle.request.POST.get("visible_only", "0"))
            res = {}
            for key, emails in self.json_request().items():
                r = analytics.common_locations(client_session, vis, emails, num)
                if r:
                    res[key] = r
                    continue

            return self.json_response(res)

        @rest.route("/profile/linkedin", method="POST")
        def linkedin_profile_get(client_session):
            logger.debug("enter linkedin_profile_get")
            if not client_session or not client_session.has_social_access():
                raise DomainAccessDenied()
            request = self.json_request()
            enterpriseRequest = request.get("enterprise", 1)
            logger.info("enterprise field value :%d", enterpriseRequest)
            _assert_valid_lookup_api_request(enterpriseRequest, client_session)
            logger.debug("continue to query linkedin")
            id = request.get("id")
            email = request.get("email")
            if not id and not email:
                raise MissingDataError(key="identifier(id or email)")
            conn = request.get("connections")
            connections = 0
            if conn is None:
                connections = int(request.get("connections", "0"))
            elif conn == "True" or conn == True:
                connections = 1
            photo_async = int(request.get("photo_async", "1"))
            cache_only = int(request.get("cache_only", "0"))
            result = profile.linkedin(client_session, id, email, connections, photo_async, cache_only)
            json_result = self.json_response(result)
            return json_result

        @rest.route("/connect/linkedin", method="POST")
        def linkedin_connect(client_session):
            logger.warning("WARNING: Method /connect/linkedin is deprecated. Please use /social/addfriend instead.")
            return self.json_response({})

        @rest.route("/news/:keyword", method="GET")
        def news_get(client_session, keyword):
            keyword = _convert_unicode(keyword)
            n = int(bottle.request.GET.get("n", "1"))
            if n < 1:
                raise MissingDataError(key="n")
            unified_acc = account.get_account(client_session, UNIFIED_CONTACTS_ACCOUNTID)
            result = unified_acc.rpc.news_get(keyword=keyword, num_results=n)
            return self.json_response(result)

        @rest.route("/contact/news", method="GET")
        def news_get_extended(client_session):
            results_limit = int(bottle.request.GET.get("limit", "10"))
            user_id = bottle.request.GET.get("user_id", "foo")
            company = bottle.request.GET.get("company")
            email = bottle.request.GET.get("email")
            name = bottle.request.GET.get("name")
            if company is None and email is None and name is None:
                raise MissingDataError(key="company, email, name")
            result = news_fetch.news_fetch(user_id, results_limit, company, name, email)
            return self.json_response(result)

        @rest.route("/accounts", method="GET")
        def accounts_list(client_session):
            nohardlocks()
            include_disabled = boolean_get_paramater("include_disabled", False)
            accounts = account.list_accounts(client_session, include_disabled)
            display_credentials = account.get_credential_access(client_session)
            d = Defaults()
            content = json.dumps([o.to_json(display_credentials=display_credentials, defaults=d) for o in accounts], cls=PIMEncoder)
            bottle.response.content_type = "application/json"
            return content

        @rest.route("/accounts/:id#[0-9]+#", method=('GET', 'PUT', 'DELETE'))
        def accounts_info(client_session, id):
            result = {}
            id = int(id)
            requesting_app = bottle.request.headers.get("User-Agent")
            display_credentials = account.get_credential_access(client_session)
            if bottle.request.method == "GET":
                result = account.info(client_session, id)
                return self.json_response([result], status=None, display_credentials=display_credentials)[1:-1]
            else:
                if bottle.request.method == "PUT":
                    data = self.json_request()
                    test = not bottle.request.forms.get("no_test")
                    result = account.modify(client_session, id, data, requesting_app, test=test)
                elif bottle.request.method == "DELETE":
                    result = account.delete_using_session(client_session, id, requesting_app)
                else:
                    raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
                return self.json_response(result)

        @rest.route("/accounts/:id#[0-9]+#/test")
        def account_test(client_session, id):
            return self.json_response(account.test(client_session, id))

        @rest.route("/accounts/group", method="POST")
        def accounts_create_group(client_session):
            data = self.json_request()
            group_name = data.get("group_name") if data else None
            if not group_name:
                raise MissingDataError(key="group_name")
            group_username = data.get("group_username") if data else None
            if not group_username:
                raise MissingDataError(key="group_username")
            acc_ids = data.get("acc_ids") if data else None
            if not acc_ids:
                raise MissingDataError(key="acc_ids")
            return self.json_response(account.create_group(client_session, group_name, group_username, acc_ids))

        @rest.route("/accounts/group/:id#[0-9]+#", method="DELETE")
        def accounts_delete_group(client_session, id):
            id = int(id)
            result = account.delete_group(client_session, id) if id > 0 else False
            return self.json_response({"success": result})

        @rest.route("/accounts/supported", method="POST")
        def account_supported(client_session):
            nohardlocks()
            data = self.json_request()
            return self.json_response(account.supported(client_session, data))

        @rest.route("/accounts/:type/sync")
        def account_sync(client_session, type):
            return self.json_response(account.sync(client_session, type))

        @rest.route("/accounts", method="POST")
        def accounts_create(client_session):
            nohardlocks()
            requesting_app = bottle.request.headers.get("User-Agent")
            data = self.json_request()
            if "provider" in data and "/" in data["provider"]:
                raise ProviderNotFound(name=data["provider"])
            return self.json_response(account.create(client_session, data, requesting_app))

        @rest.route("/accounts/default")
        def accounts_all_default(client_session):
            d = Defaults()
            account_dict = {}
            get_enterprise_defaults = account.enterprise_account_exists()
            for full_type in d.keys():
                if full_type in ACCOUNT_TYPES:
                    if get_enterprise_defaults:
                        continue
                    type = full_type
                elif get_enterprise_defaults:
                    type = full_type.split("_enterprise")[0]
                else:
                    continue
                acc = Account.default(full_type)
                if acc and acc.enterprise and not client_session.has_access(acc, DOMAIN_ACCOUNTS):
                    acc = Account.default(type, persist=False)
                if acc is None or client_session.has_access(acc, DOMAIN_ACCOUNTS):
                    account_dict[type] = acc
                    continue

            return self.json_response(account_dict)

        @rest.route("/accounts/default/:type", method=('GET', 'PUT'))
        def accounts_default(client_session, type):
            is_getter = True
            full_type = type
            if account.enterprise_account_exists():
                full_type = type + "_enterprise"
            if bottle.request.method == "PUT":
                data = self.json_request()
                acc_id = data.get("account")
                if not acc_id:
                    raise MissingDataError(key="account")
                acc = Account.get_default_candidate(int(acc_id), type)
                if acc:
                    if not client_session.has_access(acc, DOMAIN_ACCOUNTS, DOMAIN_SUBTYPE_MANAGE_ACCOUNTS):
                        raise AccountPermissionError(account_id=acc.id)
                    if full_type.find("calendars") >= 0:
                        logger.error("A calendar requires an account and folder ID to assign as default - ignoring request and returning current default")
                        acc = Account.default(full_type)
                    else:
                        acc.set_default(full_type)
                        is_getter = False
                else:
                    logger.error("The account with ID=%d cannot be set as the default for type=%s as it does not meet the criteria - ignoring request and returning current default", int(acc_id), type)
                    acc = Account.default(full_type)
            else:
                acc = Account.default(full_type)
                logger.info("ACCOUNTS - GET default for type=%s has account ID=%d (full_type=%s)", type, int(acc.id) if acc else -1, full_type)
            if is_getter and acc and not client_session.has_access(acc, DOMAIN_ACCOUNTS):
                if acc.enterprise:
                    acc = Account.default(type, persist=False)
                    logger.info("ACCOUNTS - downgrading the default to non-enterprise account ID=%d only for this app due to permission issues", int(acc.id) if acc else -1)
                if acc and not client_session.has_access(acc, DOMAIN_ACCOUNTS):
                    logger.info("ACCOUNTS - GET/SET default returning no account as DOMAIN_ACCOUNTS permission is absent for account ID=%d", int(acc.id))
                    acc = None
            if acc:
                display_credentials = account.get_credential_access(client_session)
            else:
                display_credentials = False
            return self.json_response([acc], status=None, display_credentials=display_credentials)[1:-1]

        @rest.route("/accounts/defaultobject/:type", method=('GET', 'PUT'))
        def accounts_default_object(client_session, type):
            is_getter = True
            full_type = type
            if account.enterprise_account_exists():
                full_type = type + "_enterprise"
            if bottle.request.method == "PUT":
                data = self.json_request()
                account_id = data.get("account_id")
                if not account_id:
                    raise MissingDataError(key="account_id")
                object_id = data.get("object_id")
                if not object_id:
                    raise MissingDataError(key="object_id")
                acc = Account.get_default_candidate(int(account_id), type)
                if acc:
                    if not client_session.has_access(acc, DOMAIN_ACCOUNTS, DOMAIN_SUBTYPE_MANAGE_ACCOUNTS):
                        raise AccountPermissionError(account_id=account_id)
                    candidate_folder = None
                    found = False
                    for folder in calendar.list_folders_in_account(client_session, acc):
                        if folder.id == object_id:
                            found = True
                            break
                        elif not candidate_folder:
                            candidate_folder = folder.id
                            continue

                    if not found:
                        if candidate_folder:
                            logger.warning("Invalid or unavailable calendar folder included in set default request - using folder ID=%d from account with ID=%d instead", int(candidate_folder), int(account_id))
                        else:
                            logger.error("Invalid or unavailable calendar folder included in set default request and no calendar folder was found in account with ID=%d - ignoring request and returning current default", int(account_id))
                            rc = account.default_object(client_session, full_type)
                        object_id = candidate_folder
                    if object_id:
                        account.set_default_object(full_type, account_id, object_id)
                        is_getter = False
                        rc = (account_id, object_id)
                else:
                    logger.error("The account with ID=%d cannot be set as the default for type=%s as it does not meet the criteria - ignoring request and returning current default", int(account_id), type)
                    rc = account.default_object(client_session, full_type)
            else:
                rc = account.default_object(client_session, full_type)
                logger.info("ACCOUNTS - GET default for type=%s has account ID=%d, object ID=%d (full_type=%s)", type, int(rc[0]) if rc else -1, int(rc[1]) if rc else -1, full_type)
            if is_getter:
                if rc is None and type == "calendars" and type != full_type:
                    rc = account.default_object(client_session, type)
                    if rc:
                        logger.info("ACCOUNTS - temporarily setting default to non-enterprise account ID=%d as the folders for the enterprise account are unavailable", int(rc[0]))
                if rc:
                    acc = Account(rc[0])
                    if not client_session.has_access(acc, DOMAIN_ACCOUNTS):
                        if acc.enterprise:
                            rc = account.default_object(client_session, type, persist=False)
                            logger.info("ACCOUNTS - downgrading the default to non-enterprise account ID=%d only for this app due to permission issues", int(rc[0]) if rc else -1)
                        if rc:
                            acc = Account(rc[0])
                            if not client_session.has_access(acc, DOMAIN_ACCOUNTS):
                                logger.info("ACCOUNTS - GET/SET default returning no account-object pair as DOMAIN_ACCOUNTS permission is absent for account ID=%d", int(acc.id))
                                rc = None
            return self.json_response(rc)

        @rest.route("/accounts/providers")
        def accounts_providers_list(client_session):
            providers = account.list_providers(client_session)
            return self.json_response(providers)

        @rest.route("/accounts/providers/:name")
        def accounts_providers_get(client_session, name):
            provider = account.get_provider(client_session, name)
            return self.json_response(provider)

        @rest.route("/accounts/providers/aab_terms", method="POST")
        def accounts_providers_aab_terms(client_session):
            account = self.json_request()
            settings = account["settings"]
            production = settings["production_mode"]
            msisdn = settings["x_up_calling_line_id"]
            subno = settings["x_up_subno"]
            aab_info = get_aab_terms_and_version(production, msisdn, subno)
            return self.json_response(aab_info)

        @rest.route("/accounts/providers/aab_already_registered", method="POST")
        def accounts_providers_aab_already_registered(client_session):
            account = self.json_request()
            production = account.get("settings", {}).get("production_mode", True)
            msisdn = account.get("settings", {}).get("x_up_calling_line_id", "")
            subno = account.get("settings", {}).get("x_up_subno", "")
            return self.json_response(aab_already_registered(production, msisdn, subno))

        @rest.route("/accounts/:id#[0-9]+#/update_status")
        def account_update_status(client_session, id):
            access_token = bottle.request.GET.get("token", None)
            status = int(bottle.request.GET.get("status", "-1"))
            if access_token is None or status not in (0, 1, 255):
                raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
            return self.json_response(account.update_status(client_session, id, access_token, status))

        @rest.route("/accounts/oauth2/cache/add_token", method="POST")
        def account_oauth2_cache_add_token(client_session):
            service = bottle.request.POST.get("service")
            result, status = oauth2.add_token_rest(service, self.json_request())
            return self.json_response(result, status)

        @rest.route("/accounts/:id#[0-9]+#/invalidate", method="PUT")
        def accounts_invalidate(client_session, id):
            result = {}
            id = int(id)
            data = self.json_request()
            reason = data.get("exception") if data else None
            result = account.invalidate(client_session, id, reason) if id > 0 else False
            return self.json_response({"success": result})

        @rest.route("/accounts/:id#[0-9]+#/classification", method=('GET', 'POST',
                                                                    'PUT', 'DELETE'))
        def message_classification(client_session, id):
            with threading.RLock():
                result = {}
                if bottle.request.method == "GET":
                    result = get_account(client_session, id).get_msg_classification()
                    if result is None:
                        return self.json_response({}, 404)
                elif bottle.request.method in ('POST', 'PUT'):
                    data = self.json_request()
                    if not get_account(client_session, id).set_msg_classification(data):
                        return self.json_response({}, 403)
                elif bottle.request.method == "DELETE":
                    if not get_account(client_session, id).remove_msg_classification():
                        return self.json_response({}, 404)
                else:
                    raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)
                return self.json_response(result)
            return

        @rest.route("/perimeter/:name/delete_notify", method="POST")
        def perimeter_delete_notification(client_session, name):
            if name == "enterprise":
                if not UnifiedContactsService.handle_enterprise_perimeter_delete():
                    unified_account = account.get_account(client_session, UNIFIED_CONTACTS_ACCOUNTID)
                    unified_account.rpc.handle_enterprise_perimeter_delete()
                priorityinbox.handle_enterprise_perimeter_delete()
            return self.json_response("success")

        @rest.route("/perimeter/status", method="GET")
        def perimeter_status(client_session):
            enterprise.refresh_status()
            result = UnifiedContactsService.handle_enterprise_perimeter_status(bottle.request.GET.get("wait_for_unlock"))
            return self.json_response(result)

        @rest.route("/perimeter/enterprise/contact_count", method="GET")
        def perimeter_enterprise_contact_count(client_session):
            result = contact.get_enterprise_perimter_contact_count(client_session, bottle.request.GET)
            return self.json_response(result)

        @rest.route("/app/policy", method="GET")
        def app_type(client_session):
            policy = ""
            if client_session.has_hybrid_access():
                policy = "hybrid"
            return self.json_response(policy)

        @rest.route("/local/remove_contacts", method="POST")
        def local_remove_contacts(client_session):
            result = contact.remove_local_contacts(client_session)
            return self.json_response(result)

        @rest.route("/tasks", method="GET")
        def task_list_all(client_session):
            tasks = task.list(client_session)
            return self.json_response(tasks)

        @rest.route("/tasks/:account_id#[0-9]+#", method="GET")
        def task_list_all_for_account(client_session, account_id):
            tasks = task.list(client_session, int(account_id))
            return self.json_response(tasks)

        @rest.route("/tasks/:account_id#[0-9]+#/:id#[0-9]+#", method="GET")
        def task_get(client_session, account_id, id):
            result = task.get(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/tasks/:account_id#[0-9]+#/:id#[0-9]+#", method="PUT")
        def task_edit(client_session, account_id, id):
            result = task.edit(client_session, int(account_id), int(id), self.json_request())
            return self.json_response(result)

        @rest.route("/tasks/:account_id#[0-9]+#/:id#[0-9]+#", method="DELETE")
        def task_delete(client_session, account_id, id):
            result = task.delete(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/tasks/:account_id#[0-9]+#", method="POST")
        def task_create(client_session, account_id):
            result = task.create(client_session, int(account_id), self.json_request())
            return self.json_response(result)

        @rest.route("/tasks/folders", method="GET")
        def task_list_all_folders(client_session):
            folders = task.list_task_folders(client_session)
            return self.json_response(folders)

        @rest.route("/tasks/folders/:account_id#[0-9]+#", method="GET")
        def task_list_all_folders_for_account(client_session, account_id):
            folders = task.list_task_folders(client_session, int(account_id))
            return self.json_response(folders)

        @rest.route("/tasks/folder/:account_id#[0-9]+#", method="POST")
        def task_create_folder(client_session, account_id):
            data = self.json_request()
            result = task.create_folder(client_session, int(account_id), self.json_request())
            return self.json_response(result)

        @rest.route("/tasks/folder/:account_id#[0-9]+#/:id#[0-9]+#", method="GET")
        def task_get_folder(client_session, account_id, id):
            result = task.get_folder(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/tasks/folder/:account_id#[0-9]+#/:id#[0-9]+#", method="PUT")
        def task_edit_folder(client_session, account_id, id):
            result = task.edit_folder(client_session, int(account_id), int(id), self.json_request())
            return self.json_response(result)

        @rest.route("/tasks/folder/:account_id#[0-9]+#/:id#[0-9]+#", method="DELETE")
        def task_delete_folder(client_session, account_id, id):
            result = task.delete_folder(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/tasks/contexts", method="GET")
        def task_list_all_contexts(client_session):
            contexts = task.list_task_contexts(client_session)
            return self.json_response(contexts)

        @rest.route("/tasks/contexts/:account_id#[0-9]+#", method="GET")
        def task_list_all_contexts_for_account(client_session, account_id):
            contexts = task.list_task_contexts(client_session, int(account_id))
            return self.json_response(contexts)

        @rest.route("/tasks/context/:account_id#[0-9]+#", method="POST")
        def task_create_context(client_session, account_id):
            result = task.create_context(client_session, int(account_id), self.json_request())
            return self.json_response(result)

        @rest.route("/tasks/context/:account_id#[0-9]+#/:id#[0-9]+#", method="GET")
        def task_get_context(client_session, account_id, id):
            result = task.get_context(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/tasks/context/:account_id#[0-9]+#/:id#[0-9]+#", method="PUT")
        def task_edit_context(client_session, account_id, id):
            result = task.edit_context(client_session, int(account_id), int(id), self.json_request())
            return self.json_response(result)

        @rest.route("/tasks/context/:account_id#[0-9]+#/:id#[0-9]+#", method="DELETE")
        def task_delete_context(client_session, account_id, id):
            result = task.delete_context(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/memos", method="GET")
        def memo_list_all(client_session):
            result = memo.list(client_session)
            return self.json_response(result)

        @rest.route("/memos/:account_id#[0-9]+#", method="POST")
        def memo_create(client_session, account_id):
            result = memo.create(client_session, int(account_id), self.json_request())
            return self.json_response(result)

        @rest.route("/memos/:account_id#[0-9]+#/:id#[0-9]+#", method="GET")
        def memo_get(client_session, account_id, id):
            result = memo.get(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/memos/:account_id#[0-9]+#/:id#[0-9]+#", method="DELETE")
        def memo_delete(client_session, account_id, id):
            result = memo.delete(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/memos/:account_id#[0-9]+#/:id#[0-9]+#", method="POST")
        def memo_update(client_session, account_id, id):
            result = memo.edit(client_session, int(account_id), id, self.json_request())
            return self.json_response(result)

        @rest.route("/focalpoints/:account_id#[0-9]+#", method="PUT")
        @rest.route("/focalpoints/:account_id#[0-9]+#", method="POST")
        def focal_point_create(client_session, account_id):
            result = focalpoint.create_focal_point(client_session, int(account_id), self.json_request())
            return self.json_response(result)

        @rest.route("/focalpoints/:account_id#[0-9]+#/:id#[0-9]+#", method="POST")
        def focal_point_edit(client_session, account_id, id):
            result = focalpoint.edit_focal_point(client_session, int(account_id), int(id), self.json_request())
            return self.json_response(result)

        @rest.route("/focalpoints/:account_id#[0-9]+#/:id#[0-9]+#", method="DELETE")
        def focal_point_delete(client_session, account_id, id):
            result = focalpoint.focal_point_delete(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/focalpoints", method="GET")
        def focal_point_list_all(client_session):
            result = focalpoint.list_focal_points(client_session)
            return self.json_response(result)

        @rest.route("/focalpoints/search", method="GET")
        def focal_point_search(client_session):
            search_params = _get_search_terms_from_bottle()
            result = focalpoint.focal_point_search(client_session, search_params)
            return self.json_response(result)

        @rest.route("/focalpoints/getdefault/:account_id#[0-9]+#", method="GET")
        def focal_point_get_default(client_session, account_id):
            result = focalpoint.get_default_focal_point(client_session, account_id)
            return self.json_response(result)

        @rest.route("/focalpoints/getdefault", method="GET")
        def focal_point_get_default(client_session):
            result = focalpoint.get_default_focal_point(client_session)
            return self.json_response(result)

        @rest.route("/focalpoints/gettasksdefault/:account_id#[0-9]+#", method="GET")
        def focal_point_get_tasks_default(client_session, account_id):
            result = focalpoint.get_default_tasks_focal_point(client_session, account_id)
            return self.json_response(result)

        @rest.route("/focalpoints/gettasksdefault", method="GET")
        def focal_point_get_tasks_default(client_session):
            result = focalpoint.get_default_tasks_focal_point(client_session)
            return self.json_response(result)

        @rest.route("/focalpoints/getnotesdefault/:account_id#[0-9]+#", method="GET")
        def focal_point_get_notes_default(client_session, account_id):
            result = focalpoint.get_default_notes_focal_point(client_session, account_id)
            return self.json_response(result)

        @rest.route("/focalpoints/getnotesdefault", method="GET")
        def focal_point_get_notes_default(client_session):
            result = focalpoint.get_default_notes_focal_point(client_session)
            return self.json_response(result)

        @rest.route("/focalpoints/:account_id#[0-9]+#", method="GET")
        def focal_point_list_all_by_account(client_session, account_id):
            result = focalpoint.list_focal_points(client_session, int(account_id))
            return self.json_response(result)

        @rest.route("/focalpoints/:account_id#[0-9]+#/:id#[0-9]+#", method="GET")
        def focal_point_get(client_session, account_id, id):
            result = focalpoint.get(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/focalpoints/count/:account_id#[0-9]+#/:id#[0-9]+#", method="GET")
        def focal_point_get_with_count(client_session, account_id, id):
            result = focalpoint.get_with_count(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/focalpoints/items/:account_id#[0-9]+#/:focal_point_id#[0-9]+#", method="PUT")
        @rest.route("/focalpoints/items/:account_id#[0-9]+#/:focal_point_id#[0-9]+#", method="POST")
        def focal_point_item_create(client_session, account_id, focal_point_id):
            result = focalpoint.create_focal_point_item(client_session, int(account_id), int(focal_point_id), self.json_request())
            return self.json_response(result)

        @rest.route("/focalpoints/items/valid_fps", method="GET")
        def new_focal_point_item_valid_focal_points(client_session):
            result = focalpoint.list_valid_focal_points(client_session)
            return self.json_response(result)

        @rest.route("/focalpoints/items/valid_fps/:type#[0-9]+#", method="GET")
        def focal_point_item_valid_focal_points(client_session, type):
            result = focalpoint.list_valid_focal_points(client_session, None, None, int(type))
            return self.json_response(result)

        @rest.route("/focalpoints/items/valid_fps/:account_id#[0-9]+#/:id#[0-9]+#", method="GET")
        def focal_point_item_valid_focal_points(client_session, account_id, id):
            result = focalpoint.list_valid_focal_points(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/focalpoints/items/:account_id#[0-9]+#/:focal_point_id#[0-9]+#/:id#[0-9]+#", method="POST")
        def focal_point_item_edit(client_session, account_id, focal_point_id, id):
            result = focalpoint.edit_focal_point_item(client_session, int(account_id), int(focal_point_id), int(id), self.json_request())
            return self.json_response(result)

        @rest.route("/focalpoints/items", method="GET")
        def focal_point_item_list_all(client_session):
            result = focalpoint.focal_point_item_list_all(client_session)
            return self.json_response(result)

        @rest.route("/focalpoints/items/search", method="GET")
        def focal_point_item_search(client_session):
            search_terms = _get_search_terms_from_bottle()
            result = focalpoint.focal_point_item_search(client_session, search_terms)
            return self.json_response(result)

        @rest.route("/focalpoints/items/headers", method="GET")
        def focal_point_item_headers(client_session):
            search_terms = _get_search_terms_from_bottle()
            result = focalpoint.focal_point_item_headers(client_session, search_terms)
            return self.json_response(result)

        @rest.route("/focalpoints/items/items_and_headers", method="GET")
        def focal_point_items_and_headers(client_session):
            search_terms = _get_search_terms_from_bottle()
            result = focalpoint.focal_point_items_and_headers(client_session, search_terms)
            return self.json_response(result)

        @rest.route("/focalpoints/items/count", method="GET")
        def focal_point_item_count(client_session):
            search_terms = _get_search_terms_from_bottle()
            result = focalpoint.focal_point_item_count(client_session, search_terms)
            return self.json_response(result)

        @rest.route("/focalpoints/items/counts_per_focalpoint", method="GET")
        def focal_point_item_counts_per_focalpoint(client_session):
            search_terms = _get_search_terms_from_bottle()
            result = focalpoint.focal_point_item_counts_per_focalpoint(client_session, search_terms)
            return self.json_response(result)

        @rest.route("/focalpoints/items/items_and_total_count", method="GET")
        def focal_point_items_and_total_count(client_session):
            search_terms = _get_search_terms_from_bottle()
            result = focalpoint.focal_point_items_and_total_count(client_session, search_terms)
            return self.json_response(result)

        @rest.route("/focalpoints/items/:account_id#[0-9]+#", method="GET")
        def focal_point_item_list_all_by_account(client_session, account_id):
            result = focalpoint.focal_point_item_list_by_account(client_session, int(account_id))
            return self.json_response(result)

        @rest.route("/focalpoints/items/:account_id#[0-9]+#/:focal_point_id#[0-9]+#", method="GET")
        def focal_point_item_list_all_by_focal_point(client_session, account_id, focal_point_id):
            result = focalpoint.focal_point_item_list_by_focal_point(client_session, int(account_id), int(focal_point_id))
            return self.json_response(result)

        @rest.route("/focalpoints/items/filter/due/:account_id#[0-9]+#", method="POST")
        def focal_point_item_list_all_by_due_date(client_session, account_id):
            result = focalpoint.focal_point_item_list_filtered_by_due_date(client_session, int(account_id), self.json_request())
            return self.json_response(result)

        @rest.route("/focalpoints/items/filter/due/count/:account_id#[0-9]+#", method="POST")
        def focal_point_item_list_all_by_due_date_count(client_session, account_id):
            result = focalpoint.focal_point_item_list_filtered_by_due_date_count(client_session, int(account_id), self.json_request())
            return self.json_response(result)

        @rest.route("/focalpoints/items/count/:account_id#[0-9]+#", method="GET")
        def focal_point_item_list_all_by_account_count(client_session, account_id):
            result = focalpoint.focal_point_item_list_filtered_by_account_count(client_session, int(account_id))
            return self.json_response(result)

        @rest.route("/focalpoints/items/modified/count/:account_id#[0-9]+#", method="GET")
        def focal_point_item_list_all_by_account_modified_count(client_session, account_id):
            result = focalpoint.focal_point_item_list_filtered_by_account_modified_count(client_session, int(account_id))
            return self.json_response(result)

        @rest.route("/focalpoints/items/ids/:account_id#[0-9]+#", method="GET")
        def focal_point_item_list_all_by_account_ids(client_session, account_id):
            result = focalpoint.focal_point_item_list_filtered_by_account_ids(client_session, int(account_id))
            return self.json_response(result)

        @rest.route("/focalpoints/items/modified/ids/:account_id#[0-9]+#", method="GET")
        def focal_point_item_list_all_by_account_modified_ids(client_session, account_id):
            result = focalpoint.focal_point_item_list_filtered_by_account_modified_ids(client_session, int(account_id))
            return self.json_response(result)

        @rest.route("/focalpoints/items/sync_id/ids/:account_id#[0-9]+#", method="GET")
        def focal_point_item_list_all_by_account_sync_ids(client_session, account_id):
            search_terms = _get_search_terms_from_bottle()
            data = {}
            for key, value in search_terms:
                data[key] = value

            result = focalpoint.focal_point_item_list_filtered_by_account_sync_ids(client_session, int(account_id), data)
            return self.json_response(result)

        @rest.route("/focalpoints/item/:account_id#[0-9]+#/:id#[0-9]+#", method="GET")
        def focal_point_item_get(client_session, account_id, id):
            result = focalpoint.get_item(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/focalpoints/item/:account_id#[0-9]+#/:id#[0-9]+#", method="PUT")
        def focal_point_item_set(client_session, account_id, id):
            result = focalpoint.edit_focal_point_item(client_session, int(account_id), -1, int(id), self.json_request())
            return self.json_response(result)

        @rest.route("/focalpoints/items/:account_id#[0-9]+#/:focal_point_id#[0-9]+#/:id#[0-9]+#", method="DELETE")
        def focal_point_item_delete(client_session, account_id, focal_point_id, id):
            result = focalpoint.delete_item(client_session, int(account_id), int(id))
            return self.json_response(result)

        @rest.route("/focalpoints/item/dirty/:account_id#[0-9]+#/:id#[0-9]+#", method="POST")
        def focal_point_item_dirty_flag(client_session, account_id, id):
            result = focalpoint.update_focal_point_item_dirty_flag(client_session, int(account_id), int(id), self.json_request())
            return self.json_response(result)

        @rest.route("/focalpoints/ccl", method="GET")
        def focal_point_ccl_data(client_session):
            result = focalpoint.focal_point_ccl_data(client_session)
            return self.json_response(result)

        @rest.route("/system/network")
        def network_status(client_session):
            connected = self.settings_service.connected
            roaming = self.settings_service.roaming
            return self.json_response({"connected": connected,  "roaming": roaming})

        @rest.route("/system/network/mobile")
        def mobile_network_status(client_session):
            return self.json_response({"data_connected": (getSourceAddress() is not None)})

        @rest.route("/system/settings", ["GET", "PUT"])
        def settings(client_session):
            if bottle.request.method == "GET":
                return self.json_response(self.settings_service.properties)
            if bottle.request.method == "PUT":
                data = self.json_request()
                for key, value in data.items():
                    pim.internal.settings.write_custom_setting(key, value)

                return self.json_response(self.settings_service.properties)
            raise HttpMethodNotAllowed(method=bottle.request.method, route=bottle.request.path)

        @rest.route("/system/locale")
        def locale(client_session):
            result = {"language": (LocaleSettingsProperties.language)}
            return self.json_response(result)

        self.rest = rest

        @rest.route("/profiler/:action", ["GET"])
        def profiler(client_session, action):
            if not PIM_YAPPI_PROFILER_ENABLED:
                logger.info("PIM_YAPPI_PROFILER_ENABLED is not set, profiler is disabled")
                return {"success": False}
            else:
                result = {"success": True}
                with yappiLock:
                    if action == "start":
                        logger.info("Starting Yappi profiler.")
                        if yappi.is_running():
                            yappi.stop()
                        yappi.set_clock_type("WALL")
                        yappi.start()
                    elif action == "stop":
                        if yappi.is_running():
                            logger.info("Stopping Yappi profiler.")
                            yappi.stop()
                    elif action == "start_clean" or action == "clear":
                        logger.info("Starting Yappi profiler with cleared stats.")
                        if yappi.is_running():
                            yappi.stop()
                        yappi.clear_stats()
                        yappi.set_clock_type("WALL")
                        yappi.start()
                    elif action == "report":
                        title = bottle.request.GET.get("title", None)
                        self.dump_profile_stats(title)
                    else:
                        logger.info("Unknown action:%s", action)
                        result = {"success": False}
                return self.json_response(result)

        @rest.route("/stats/:action", method="PUT")
        def stats(client_session, action):
            if action == "file":
                RestCounter.instance().log(True)
                result = {"success": True}
            elif action == "quip":
                RestCounter.instance().log(False)
                result = {"success": True}
            else:
                result = {"success": False}
            return result

        @rest.route("/bblink/calendars/:account_id#[0-9]+#", method="POST")
        def bblink_calendar_create_update(client_session, account_id):
            result = bblink.calendar_create_update(client_session, account_id, self.json_request())
            return self.json_response(result)

        @rest.route("/bblink/calendars/:account_id#[0-9]+#", method="GET")
        def bblink_calendars_get(client_session, account_id):
            result = bblink.calendars_get(client_session, account_id)
            return self.json_response(result)

        @rest.route("/bblink/calendar/:account_id#[0-9]+#/:calendar_id#[0-9]+#", method="DELETE")
        def bblink_calendar_delete(client_session, account_id, calendar_id):
            result = bblink.calendar_delete(client_session, account_id, calendar_id)
            return self.json_response(result)

        @rest.route("/bblink/events/:account_id#[0-9]+#", method="GET")
        def bblink_events_get(client_session, account_id):
            result = bblink.events_get(client_session, account_id, bottle.request.GET)
            return self.json_response(result)

        @rest.route("/bblink/events/:account_id#[0-9]+#", method="POST")
        def bblink_events_push(client_session, account_id):
            ids = bblink.events_push(client_session, account_id, self.json_request())
            return self.json_response(ids)

        @rest.route("/bblink/events/:account_id#[0-9]+#/get", method="POST")
        def bblink_events_get_by_ids(client_session, account_id):
            events = bblink.events_get_by_ids(client_session, account_id, self.json_request())
            return self.json_response(events)

        @rest.route("/bblink/events/:account_id#[0-9]+#/delete", method="POST")
        def bblink_events_delete(client_session, account_id):
            result = bblink.events_delete(client_session, account_id, self.json_request())
            return self.json_response(result, 200)

        @rest.route("/bblink/events/:account_id#[0-9]+#/sync", method="PUT")
        def bblink_events_sync(client_session, account_id):
            data = self.json_request()
            result = bblink.events_sync(client_session, account_id, data["updated_time"])
            return self.json_response(result)

        @rest.route("/bblink/events/:account_id#[0-9]+#/sync", method="GET")
        def bblink_events_get_sync(client_session, account_id):
            result = bblink.events_get_sync(client_session, account_id)
            return self.json_response(result)

        @rest.route("/send_messages", method="POST")
        def pin_send_messages():
            return self.json_response({"status": "ok"})

        def boolean_get_paramater(param, default_val=False):
            get_param = bottle.request.GET.get(param, str(default_val))
            if get_param.lower() in ('true', 'yes', 'y', '1'):
                return True
            return False

        return

    @staticmethod
    def dump_profile_stats(title=None, path=None):
        try:
            if not yappi.is_running():
                return
            else:
                yappi.stop()
                thread_stats = yappi.get_thread_stats()
                thread_stats.sort(sort_type="totaltime")
                if not title:
                    file_identifier = datetime.now().strftime("%Y-%m-%d-%H:%M:%S")
                else:
                    file_identifier = title
                func_stats = yappi.get_func_stats()
                if path == None:
                    path = "/var/tmp/profiler/"
                d = os.path.dirname(path)
                if not os.path.exists(d):
                    os.makedirs(d)
                threadfname = path + file_identifier + ".tstats"
                funcfname = path + file_identifier + ".fstats"
                func_stats.save(path=path + "/ystat-" + file_identifier + ".out", type="YSTAT")
                func_stats.save(path=path + "/callgrind-" + file_identifier + ".out", type="CALLGRIND")
                func_stats.save(path=path + "/pstat-" + file_identifier + ".out", type="PSTAT")
                func_stats.sort(sort_type="totaltime")
                thread_file = open(threadfname, "w+", encoding="utf-8")
                func_file = open(funcfname, "w+", encoding="utf-8")
                thread_stats.print_all(out=thread_file)
                func_stats.print_all(out=func_file)
                thread_file.close()
                func_file.close()
                return (threadfname, funcfname)
        except:
            logger.exception("yappi is not installed, no profiling stats available.")

        return

    def _save_or_send(self, client_session, account_id, action, orig_msg_id, ics_attachment=None, to_list_override=None, from_addr=None, reply_to_addr=None, provider_data=None):
        pin_relay_success = False
        if provider_data is None:
            provider_data = {}
        _confirm_good_file_path_on_attachments(account_id)
        to_list = json.loads(bottle.request.POST.get("to", default="[]")) if to_list_override is None else to_list_override
        cc_list = json.loads(bottle.request.POST.get("cc", default="[]"))
        bcc_list = json.loads(bottle.request.POST.get("bcc", default="[]"))
        references_sync_id = None
        if bottle.request.POST.get("references_sync_id") is not None:
            references_sync_id = json.loads(bottle.request.POST.get("references_sync_id", default=""))
        if not from_addr:
            from_addr = bottle.request.POST.get("from")
        if int(account_id) == LOCAL_PINMESSAGES_ACCOUNTID and action in ("send", "reply", "smart_reply") and os.environ.get("PIN_RELAY_PRE_SEND", "0") == "1":
            try:
                _pin_relay_register_client_session(client_session)
                _pin_relay_register_provider(get_account(client_session, account_id))
            except Exception as e:
                _pin_relay_debug("provider register at send failed %s" % e)
            try:
                relay_to_list = json.loads(bottle.request.POST.get("to", default="[]")) if to_list_override is None else to_list_override
                if not relay_to_list:
                    relay_to_list = _get_pin_relay_reply_to(orig_msg_id)
                    to_list = relay_to_list
                relay_subject = _get_pin_relay_subject()
                relay_priority = _get_pin_relay_priority()
                relay_body = _get_pin_relay_body()
                relay_sender = _get_pin_relay_sender(from_addr)
                _pin_relay_debug("send hook action=%s orig_msg_id=%s to=%s subject=%r priority=%s body_len=%s" % (action, orig_msg_id, relay_to_list, relay_subject, relay_priority, len(relay_body) if relay_body else 0))
                payload = json.dumps({"from": relay_sender, "to": relay_to_list, "subject": relay_subject, "priority": relay_priority, "body": relay_body}).encode("utf-8")
                req = urllib.request.Request(os.environ.get("PIN_RELAY_URL", "http://10.58.53.142:8080/send-pin"), data=payload)
                req.add_header("Content-Type", "application/json")
                relay_response = urllib.request.urlopen(req, timeout=10)
                relay_code = relay_response.getcode()
                relay_body_bytes = relay_response.read()
                if relay_code < 200 or relay_code >= 300:
                    raise Exception("PIN relay returned HTTP status %s" % relay_code)
                relay_json = json.loads(relay_body_bytes.decode("utf-8")) if relay_body_bytes else {}
                relay_status = relay_json.get("status", "")
                relay_id = relay_json.get("id")
                if relay_status not in ("ok", "sent", "delivered"):
                    raise Exception("PIN relay did not confirm delivery: %s" % relay_status)
                provider_data["pin_relay_status"] = relay_status
                if relay_id:
                    provider_data["pin_relay_id"] = relay_id
                    provider_data["pin_relay_backend_id"] = str(relay_id)
                pin_relay_success = True
            except Exception as e:
                logger.error("Hook PIM: PIN relay failed or did not confirm delivery: %s", e)
                bottle.request.__setitem__("wsgi.input", None)
                try:
                    bottle.request.__delitem__("bottle.post")
                except KeyError:
                    pass
                self._schedule_gc()
                return {"id": (-1), "status": "pin_relay_failed"}
        if not orig_msg_id:
            orig_msg_id = bottle.request.POST.get("orig_msg_id", default="0")
        draft_id = bottle.request.POST.get("draft_id")
        logger.info("_save_or_send: action='%s', orig_msg_id=%s, draft_id=%s, ics=%s", action, orig_msg_id, draft_id, ics_attachment)
        subject = bottle.request.POST.get("subject").replace("\r\n", "\r").replace("\r", "\r\n")
        body = bottle.request.POST.get("body", default="")
        full_body = bottle.request.POST.get("full_body", default=None)
        if full_body:
            provider_data["full_body"] = full_body
        body_content_type = bottle.request.POST.get("body_content_type", default="text/plain")
        options = json.loads(bottle.request.POST.get("options", default="{}"))
        attachment_list = json.loads(bottle.request.POST.get("attachments", default="{}"))
        orig_msg_edited = json.loads(bottle.request.POST.get("orig_msg_edited", default="false"))
        provider_data["orig_msg_edited"] = orig_msg_edited
        user_action = bottle.request.POST.get("user_action")
        if not user_action:
            if action == "smart_reply":
                user_action = str(USER_ACTION_REPLY)
            elif action == "smart_forward":
                user_action = str(USER_ACTION_FORWARD)
        if user_action:
            provider_data["user_action"] = user_action
        if ics_attachment is not None:
            if attachment_list:
                attachment_list.append(ics_attachment)
            else:
                attachment_list = [
                 ics_attachment]
        encoding_type = int(json.loads(bottle.request.POST.get("encoding_type", default=str(EncodingType.UNINITIALIZED))))
        encoding_action = int(json.loads(bottle.request.POST.get("encoding_action", default=str(EncodingAction.UNINITIALIZED))))
        options["classification_id"] = json.loads(bottle.request.POST.get("classification_id")) if "classification_id" in bottle.request.POST else "none"
        body_plain_text = None
        secure_send_id = None
        if encoding_type != EncodingType.UNINITIALIZED:
            options["message_type"] = convert_message_type(encoding_type, encoding_action)
            if encoding_type != EncodingType.PLAIN_TEXT:
                initialize_secure_email()
                logger.info("########### encoding options is mt:[%s] et[%s], ea[%s] #############" % (options["message_type"], encoding_type, encoding_action))
                body_plain_text = bottle.request.POST.get("body_plaintext", default=None)
                secure_send_id = int(bottle.request.POST.get("request_id", default=0))
                if body_plain_text is not None:
                    body_plain_text = json.loads(body_plain_text)
                full_body_plain_text = bottle.request.POST.get("full_body_plaintext", default=None)
                if full_body_plain_text is not None:
                    full_body_plain_text = json.loads(full_body_plain_text)
                    provider_data["full_body_plain_text"] = full_body_plain_text
        send_action = "save" if int(account_id) == LOCAL_PINMESSAGES_ACCOUNTID and action in ("send", "reply", "smart_reply") else action
        retval = message.send_message(client_session, account_id=account_id, to=to_list, body=body, body_plain_text=body_plain_text, from_addr=from_addr, body_type=body_content_type, subject=subject, cc=cc_list, bcc=bcc_list, options=options, attachments=attachment_list, action=send_action, orig_msg_id=orig_msg_id, draft_id=draft_id, references_sync_id=references_sync_id, reply_to_addr=reply_to_addr, provider_data=provider_data, secure_send_id=secure_send_id)
        if int(account_id) == LOCAL_PINMESSAGES_ACCOUNTID and action in ("send", "reply", "smart_reply"):
            try:
                message_id_for_pending = int(retval.get("id"))
                db_path_for_pending = os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db")
                db_for_pending = sqlite3.connect(db_path_for_pending, timeout=10)
                cursor_for_pending = db_for_pending.cursor()
                cursor_for_pending.execute("SELECT id FROM MessageFolder WHERE type = 3 LIMIT 1")
                pending_folder_row = cursor_for_pending.fetchone()
                pending_folder_id = pending_folder_row[0] if pending_folder_row else 2
                cursor_for_pending.execute("SELECT conversation_id, folder_id, status FROM Message WHERE id = ?", (message_id_for_pending,))
                pending_row = cursor_for_pending.fetchone()
                pending_conversation_id = pending_row[0] if pending_row else None
                cursor_for_pending.execute("UPDATE Message SET folder_id = ?, status = ?, status_description = NULL, sync_dirty = 0 WHERE id = ?", (pending_folder_id, 205, message_id_for_pending))
                db_for_pending.commit()
                db_for_pending.close()
                try:
                    account_obj = get_account(client_session, account_id)
                    notify_obj = getattr(account_obj, "notify", None)
                    if notify_obj is None and getattr(account_obj, "provider", None) is not None:
                        notify_obj = getattr(account_obj.provider, "notify", None)
                    if notify_obj is not None:
                        notify_obj.notify_message_changed(message_id_for_pending, pending_conversation_id, pending_folder_id, changes={"status": 205, "folder_id": pending_folder_id}, from_UI=False)
                except Exception as e:
                    _pin_relay_debug("pending notify failed message_id=%s err=%s" % (message_id_for_pending, e))
                _pin_relay_debug("PIN message saved as pending message_id=%s folder_id=%s status=205" % (message_id_for_pending, pending_folder_id))
            except Exception as e:
                _pin_relay_debug("PIN pending state update failed err=%s" % e)
        if int(account_id) == LOCAL_PINMESSAGES_ACCOUNTID and action in ("send", "reply", "smart_reply") and not pin_relay_success:
            try:
                message_id_for_late_relay = int(retval.get("id"))
                db_path_for_late_relay = os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db")
                db_for_late_relay = sqlite3.connect(db_path_for_late_relay, timeout=10)
                cursor_for_late_relay = db_for_late_relay.cursor()
                cursor_for_late_relay.execute("SELECT sync_id FROM Message WHERE id = ?", (message_id_for_late_relay,))
                row_for_late_relay = cursor_for_late_relay.fetchone()
                db_for_late_relay.close()
                relay_client_refid = str(row_for_late_relay[0]) if row_for_late_relay and row_for_late_relay[0] else str(random.randint(1, 2147483647))
                relay_to_list = json.loads(bottle.request.POST.get("to", default="[]")) if to_list_override is None else to_list_override
                if not relay_to_list:
                    relay_to_list = _get_pin_relay_reply_to(orig_msg_id)
                relay_subject = _get_pin_relay_subject()
                relay_priority = _get_pin_relay_priority()
                relay_body = _get_pin_relay_body()
                relay_sender = _get_pin_relay_sender(from_addr)
                _pin_relay_debug("late send hook message_id=%s refid=%s to=%s subject=%r priority=%s body_len=%s" % (message_id_for_late_relay, relay_client_refid, relay_to_list, relay_subject, relay_priority, len(relay_body) if relay_body else 0))
                payload = json.dumps({"from": relay_sender, "to": relay_to_list, "subject": relay_subject, "priority": relay_priority, "body": relay_body, "client_refid": relay_client_refid}).encode("utf-8")
                req = urllib.request.Request(os.environ.get("PIN_RELAY_URL", "http://10.58.53.142:8080/send-pin"), data=payload)
                req.add_header("Content-Type", "application/json")
                relay_response = urllib.request.urlopen(req, timeout=10)
                relay_code = relay_response.getcode()
                relay_body_bytes = relay_response.read()
                if relay_code < 200 or relay_code >= 300:
                    raise Exception("PIN relay returned HTTP status %s" % relay_code)
                relay_json = json.loads(relay_body_bytes.decode("utf-8")) if relay_body_bytes else {}
                relay_status = relay_json.get("status", "")
                relay_id = relay_json.get("id")
                if relay_status not in ("ok", "sent", "delivered"):
                    raise Exception("PIN relay did not confirm delivery: %s" % relay_status)
                provider_data["pin_relay_status"] = relay_status
                provider_data["pin_relay_client_refid"] = str(relay_json.get("client_refid") or relay_client_refid)
                if relay_id:
                    provider_data["pin_relay_id"] = relay_id
                    provider_data["pin_relay_backend_id"] = str(relay_id)
                pin_relay_success = True
                db_for_late_relay = sqlite3.connect(db_path_for_late_relay, timeout=10)
                cursor_for_late_relay = db_for_late_relay.cursor()
                cursor_for_late_relay.execute("UPDATE Message SET sync_id = ?, sync_version = NULL, provider_data = ? WHERE id = ?", (provider_data["pin_relay_client_refid"], json.dumps(provider_data), message_id_for_late_relay))
                db_for_late_relay.commit()
                db_for_late_relay.close()
            except Exception as e:
                _pin_relay_debug("late send hook failed err=%s" % e)
                logger.error("Hook PIM: late PIN relay failed: %s", e)
        if int(account_id) == LOCAL_PINMESSAGES_ACCOUNTID and pin_relay_success:
            try:
                sent_status = int(os.environ.get("PIN_RELAY_SENT_STATUS", "2"))
                message_id = int(retval.get("id"))

                def mark_pin_relay_sent():
                    try:
                        db_path = os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db")
                        db = sqlite3.connect(db_path, timeout=10)
                        cursor = db.cursor()
                        cursor.execute("SELECT id FROM MessageFolder WHERE type = 2 LIMIT 1")
                        row = cursor.fetchone()
                        sent_folder_id = row[0] if row else 3
                        cursor.execute("SELECT conversation_id FROM Message WHERE id = ?", (message_id,))
                        row = cursor.fetchone()
                        conversation_id = row[0] if row else None
                        relay_id = provider_data.get("pin_relay_id")
                        if relay_id:
                            relay_refid = str(provider_data.get("pin_relay_client_refid") or relay_id)
                            cursor.execute("UPDATE Message SET folder_id = ?, status = ?, status_description = '', sync_dirty = 0, sync_id = ?, sync_version = ?, provider_data = ? WHERE id = ?", (sent_folder_id, sent_status, relay_refid, relay_refid, json.dumps(provider_data), message_id))
                        else:
                            cursor.execute("UPDATE Message SET folder_id = ?, status = ?, status_description = '', sync_dirty = 0, provider_data = ? WHERE id = ?", (sent_folder_id, sent_status, json.dumps(provider_data), message_id))
                        db.commit()
                        db.close()
                        if relay_id and os.environ.get("PIN_RELAY_ENABLE_NATIVE_ACCEPTED_STATUS", "1") == "1":
                            try:
                                accepted_ok = _pin_relay_call_original_status_update(str(relay_refid), False, status_override=5)
                                _pin_relay_debug("original accepted status result sync_id=%s relay_id=%s ok=%s" % (relay_refid, relay_id, accepted_ok))
                                if accepted_ok:
                                    db = sqlite3.connect(os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db"), timeout=10)
                                    cursor = db.cursor()
                                    cursor.execute("UPDATE Message SET status = ?, folder_id = ?, status_description = '', sync_dirty = 0 WHERE id = ?", (sent_status, sent_folder_id, message_id))
                                    db.commit()
                                    db.close()
                                if not accepted_ok and os.environ.get("PIN_RELAY_ENABLE_ACCEPTED_FALLBACK", "0") == "1":
                                    accepted_status = int(os.environ.get("PIN_RELAY_NATIVE_ACCEPTED_MESSAGE_STATUS", "103"))
                                    db = sqlite3.connect(os.environ.get("PIN_RELAY_DB_PATH", "/accounts/1000/_startup_data/sysdata/pim/db/199-pim.db"), timeout=10)
                                    cursor = db.cursor()
                                    cursor.execute("UPDATE Message SET status = ?, sync_dirty = 0 WHERE id = ?", (accepted_status, message_id))
                                    db.commit()
                                    db.close()
                                    _pin_relay_debug("original accepted fallback sync_id=%s message_id=%s status=%s" % (relay_id, message_id, accepted_status))
                            except Exception as e:
                                _pin_relay_debug("original accepted status failed sync_id=%s err=%s" % (relay_id, e))
                        try:
                            native_notified = False
                            try:
                                account_obj = get_account(client_session, account_id)
                                _pin_relay_register_provider(account_obj)
                                notify_obj = getattr(account_obj, "notify", None)
                                if notify_obj is None and getattr(account_obj, "provider", None) is not None:
                                    notify_obj = getattr(account_obj.provider, "notify", None)
                                if notify_obj is not None:
                                    notify_obj.notify_message_changed(message_id, conversation_id, sent_folder_id, changes={"status": sent_status, "folder_id": sent_folder_id}, from_UI=False)
                                    native_notified = True
                            except Exception as e:
                                logger.error("Hook PIM: Native notify_message_changed via account failed: %s", e)
                            if not native_notified:
                                try:
                                    from pim.providers.ProviderNotification import ProviderNotification
                                    notify_obj = ProviderNotification(int(account_id))
                                    notify_obj.notify_message_changed(message_id, conversation_id, sent_folder_id, changes={"status": sent_status, "folder_id": sent_folder_id}, from_UI=False)
                                    try:
                                        notify_obj.close()
                                    except:
                                        pass
                                    native_notified = True
                                except Exception as e:
                                    logger.error("Hook PIM: Native notify_message_changed direct failed: %s", e)
                            pps_data = {
                                "priority": "nominal",
                                "folder_id": sent_folder_id,
                                "from_UI": False,
                                "conversation_id": conversation_id,
                                "changes": {"status": sent_status, "folder_id": sent_folder_id},
                                "id": message_id
                            }
                            pps = PpsFile("/pps/services/pim/status", "w")
                            pps.write({"account_id": int(account_id), "data": json.dumps(pps_data), "name": "message_changed", "type": "messages"})
                            pps.close()
                        except Exception as e:
                            logger.error("Hook PIM: Failed to notify Hub via PPS: %s", e)
                        logger.info("Hook PIM: PIN relay confirmed and message %s marked as sent", message_id)
                    except Exception as e:
                        logger.error("Hook PIM: Failed to mark confirmed PIN relay as sent: %s", e)

                mark_pin_relay_sent()
                for mark_sent_delay in os.environ.get("PIN_RELAY_MARK_SENT_DELAYS", "").split(","):
                    try:
                        timer = threading.Timer(float(mark_sent_delay.strip()), mark_pin_relay_sent)
                        timer.daemon = True
                        timer.start()
                    except:
                        pass
                retval["status"] = "success"
            except Exception as e:
                logger.error("Hook PIM: Failed to schedule confirmed PIN relay status update: %s", e)
        bottle.request.__setitem__("wsgi.input", None)
        bottle.request.__delitem__("bottle.post")
        self._schedule_gc()
        return retval

    def _save_or_send_calendar(self, client_session, account_id, action, orig_msg_id, ics_attachment=None, to_list_override=None, from_addr=None, reply_to_addr=None, provider_data=None):
        return self._save_or_send(client_session, account_id, action, orig_msg_id, ics_attachment=ics_attachment, to_list_override=to_list_override, from_addr=from_addr, reply_to_addr=reply_to_addr, provider_data=provider_data)

    def _save_or_send_message(self, client_session, account_id, action, orig_msg_id, ics_attachment=None, to_list_override=None, from_addr=None, reply_to_addr=None, provider_data=None):
        options = json.loads(bottle.request.POST.get("options", default="{}"))
        options["classification_id"] = json.loads(bottle.request.POST.get("classification_id")) if "classification_id" in bottle.request.POST else "none"
        if options["classification_id"] == "none":
            account = get_account(client_session, account_id)
            if account.is_msg_classification_enabled():
                requesting_app = bottle.request.headers.get("User-Agent")
                if requesting_app and "libsys.pim.calendar" in requesting_app:
                    pass
                elif action != "save":
                    logger.error("Message Classification - no classification_id")
                    return {"id": (-1),  "status": "no_classification_id"}
        return self._save_or_send(client_session, account_id, action, orig_msg_id, ics_attachment=ics_attachment, to_list_override=to_list_override, from_addr=from_addr, reply_to_addr=reply_to_addr, provider_data=provider_data)

    def json_response(self, obj, status=None, display_credentials=False):
        if display_credentials:
            content = json.dumps([o.to_json(display_credentials=display_credentials) for o in obj], cls=PIMEncoder)
        else:
            content = json.dumps(obj, cls=PIMEncoder)
        bottle.response.content_type = "application/json"
        if status is not None:
            bottle.response.status = status
        return content

    def json_request(self, field="json"):
        if field in bottle.request.forms:
            return json.loads(bottle.request.forms[field])
        raise MissingDataError(key=field)

    def read_anchor_args(self, data, default_columns, default_orders, filters):
        args = {"anchor_account_id": (int(data.get("anchor_account_id", 0))), 
         "anchor_object_id": (int(data.get("anchor_object_id", 0))), 
         "quantity": (int(data.get("quantity", 0)))}

        def _get_list(data, key):
            return [x.strip() for x in data.get(key, "").split(",") if x.strip()]

        columns = _get_list(data, "sort_columns")
        orders = _get_list(data, "sort_orders")
        args["anchor_values"] = _get_list(data, "anchor_values")
        if len(columns) == 0:
            columns = default_columns
            orders = default_orders
        elif len(orders) == 0:
            orders = ["ASC" for x in columns]
        args["columns"] = columns
        args["sort_orders"] = orders
        args["filter"] = " AND ".join("%s=:%s" % (k, k) for k in filters.keys())
        args["params"] = filters.copy()
        return args

    def start(self):
        if PPS_EMULATION:
            logger.info("Disable session service because of PPS emulation")
        else:
            self.session_service.start()
            self.notification_service.start()
            _navigator_listener.register_callback(self.handle_navigator_notification)
            _navigator_listener.start()
        bottle.debug(True)
        try:
            with open(route_cache_file, "rb") as f:
                routes, static, dynamic = pickle.load(f)
        except Exception:
            self.rest.router._compile()
        else:
            if routes != self.rest.router.routes:
                self.rest.router._compile()
            else:
                self.rest.router.static, self.rest.router.dynamic = static, dynamic
        re.purge()
        bottlerun = threading.Thread(target=bottle.run, name="bottle.run", kwargs={"app": (self.rest), 
         "host": REST_HOST, 
         "port": REST_PORT, 
         "server": WSGIServerBottle})
        bottlerunE = threading.Thread(target=bottle.run, name="bottle.run", kwargs={"app": (self.rest), 
         "host": REST_HOST, 
         "port": REST_PORT_ENTERPRISE, 
         "server": WSGIServerBottle, 
         "enterprise": True})
        bottlerun.daemon = True
        bottlerun.start()
        bottlerun.prio = HIGH_THREAD_PRIORITY
        bottlerunE.daemon = True
        bottlerunE.start()
        bottlerunE.prio = HIGH_THREAD_PRIORITY

    def stop(self):
        try:
            if _navigator_listener:
                _navigator_listener.stop()
            from cherrypy.wsgiserver import http_server_listening_event
            http_server_listening_event.set()
            self.session_service.stop()
        except Exception:
            logger.exception("error while stopping session service")

        try:
            self.notification_service.rpc.quit()
        except Exception:
            logger.exception("error while stopping notification service")

    def _schedule_gc(self):
        self._request_gc = True

    def handle_navigator_notification(self, pps_obj, order):
        if not self._request_gc:
            return
        else:
            current_mode_val = pps_obj.get(IDLE_MODE, None)
            if current_mode_val != "idle":
                return
            self._request_gc = False
            logger.debug("Device went idle - starting GC")
            gc.collect()
            logger.info("Device went idle - completed GC")
            return


class ApiMonitor:
    _log_file = None
    _log_file_name = "/var/tmp/pim_api.log"
    _pps_monitor = None

    def __init__(self):
        self._log_lock = threading.Lock()

    def _get_log_file(self):
        if not os.path.exists(self._log_file_name):
            self._log_file = os.open(self._log_file_name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_TRUNC, 384)
        if self._log_file is None:
            self._log_file = os.open(self._log_file_name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_TRUNC, 384)
        return self._log_file

    def _close_log_file(self):
        os.close(self._log_file)
        self._log_file = None
        return

    def log_debug(self, context, function=None, message=None, show_time=False):
        try:
            f = ' function="' + function + '"' if function is not None else ""
            m = ' message="' + message + '"' if message is not None else ""
            t = ' time="%s' % time.ctime() + '"' if show_time else ""
            os.write(self._get_log_file(), bytes("%s: %s%s%s" % (context, t, f, m) + "\n", "UTF-8"))
        except OSError:
            print("OS Error during logging")

        return

    def log(self, content, function=None, message=None, show_time=True):
        return

    def log_request_parameters_debug(self, bottle):
        with self._log_lock:
            try:
                request = bottle.request
                self.log("Rest-%s" % request.method, request.path[1:], "entering call", show_time=True)
                query_string_params = request.GET
                self.log("GetParameters")
                for q in query_string_params.keys():
                    self.log(q, message=query_string_params[q])

                self.log("End GetParameters")
                post_params = request.POST
                self.log("PostParameters")
                for p in post_params.keys():
                    self.log(p, message=post_params[p])

                self.log("End PostParameters")
                self.log("End Rest-%s" % request.method)
            except OSError:
                self.log("Rest-Error", message="encountered an OS Error in accessing bottle contents")

    def log_request_parameters(self, bottle):
        return

    class _PpsMonitor(threading.Thread):

        def __init__(self):
            super(ApiMonitor._PpsMonitor, self).__init__()
            self._statusfd = PpsFile("/pps/services/pim/status", wait=True, readonly=True, delta=True)

        def run(self):
            while True:
                st = self._statusfd.read()
                ApiMonitor.log("PPS", st["name"], pformat(st), show_time=True)

        def stop(self):
            self._statusfd.close()
            self.join()

    def shut_down_monitoring_debug(self):
        logger.info("Terminating API logging")
        self._close_log_file()
        self._pps_monitor.stop()

    def start_monitoring(self):
        if PIM_API_LOGGING_ENABLED:
            logger.info("Starting API logging")
            self._enable_api_monitoring()
            self._pps_monitor = ApiMonitor._PpsMonitor()
            self._pps_monitor.start()

    def shut_down_monitoring(self):
        return

    def _enable_api_monitoring(self):
        self.shut_down_monitoring = self.shut_down_monitoring_debug
        self.log_request_parameters = self.log_request_parameters_debug
        self.log = self.log_debug


ApiMonitor = ApiMonitor()

def main():
    import sys
    if len(sys.argv) == 2 and sys.argv[1] == "--build-cache":
        router = RestService().rest.router
        router._compile()
        with open(route_cache_file, "wb") as f:
            pickle.dump((router.routes, router.static, router.dynamic), f)
        print("Cache build completed.")
        return
    logging_config("pimapi-deprecated")
    logging.fatal("You are trying to push_pim using an image that is too old. CFP or autoupdate a new image to use the latest PIM")


if __name__ == "__main__":
    main()

# okay decompiling rest.pyc
