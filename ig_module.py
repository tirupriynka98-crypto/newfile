"""Instagram settings, session validation, and bounded browser commands."""

import asyncio
import hmac
import json
import logging
import os
import random
import re
import stat
import tempfile
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from importlib import import_module
from urllib.parse import urlsplit


class SettingsError(RuntimeError):
    """Raised when module-owned persistent settings cannot be read or saved."""


ActiveKey = tuple[int, int]
active_operations: dict[ActiveKey, asyncio.Task[Any]] = {}
igstart_operations: dict[
    ActiveKey, dict[asyncio.Task[Any], dict[str, Any]]
] = {}
pending_logins: dict[ActiveKey, float] = {}
pending_igstarts: dict[ActiveKey, dict[str, Any]] = {}
_logger = logging.getLogger(__name__)
MAX_MESSAGE_FILE_BYTES = 64 * 1024
MAX_MESSAGE_LENGTH = 1_000
MAX_IGSTART_SENDS = 100000000
MAX_CONCURRENT_IGSTARTS = 20
MESSAGE_EMOJIS = ("😀", "😊", "🙌", "✨", "🌟", "🎉")
VAULT_KEY_ENV_VAR = "VI_INSTAGRAM_VAULT_KEY"
LOGIN_PROMPT_TTL_SECONDS = 300
IGSTART_PROMPT_TTL_SECONDS = 300
MAX_SESSION_ID_LENGTH = 4096


def _settings_path(data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        return data_dir / "settings.json"
    data_home = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    )
    return data_home / "vi" / "instagram" / "settings.json"


def _read_settings(data_dir: Path | None = None) -> dict[str, Any]:
    path = _settings_path(data_dir)
    try:
        with path.open(encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise SettingsError(
            f"Instagram settings could not be read ({type(error).__name__})."
        ) from None
    if not isinstance(settings, dict):
        raise SettingsError("Instagram settings have an invalid format.")
    if "session_id" in settings:
        try:
            _write_settings(settings, data_dir)
        except SettingsError:
            raise SettingsError(
                "A legacy plaintext Instagram session was found but could not be "
                "removed from settings; no session value was read."
            ) from None
        settings.pop("session_id", None)
    return settings


def _write_settings(settings: dict[str, Any], data_dir: Path | None = None) -> None:
    path = _settings_path(data_dir)
    temporary_path: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".settings-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            if hasattr(os, "fchmod"):
                os.fchmod(temporary.fileno(), 0o600)
            else:
                os.chmod(temporary.name, 0o600)
            json.dump(
                {
                    key: value
                    for key, value in settings.items()
                    if key != "session_id"
                },
                temporary,
                ensure_ascii=False,
            )
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except OSError as error:
        raise SettingsError(
            f"Instagram settings could not be saved ({type(error).__name__})."
        ) from None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except OSError as error:
                _logger.warning(
                    "Could not remove a temporary Instagram settings file (%s).",
                    type(error).__name__,
                )


def save_message_text(text: str, data_dir: Path | None = None) -> None:
    settings = _read_settings(data_dir)
    settings["message_text"] = text
    _write_settings(settings, data_dir)


def read_message_text(data_dir: Path | None = None) -> str | None:
    settings = _read_settings(data_dir)
    message_text = settings.get("message_text")
    if message_text is None:
        return None
    if not isinstance(message_text, str):
        raise SettingsError("Instagram settings have an invalid message_text value.")
    return message_text


def _vault_path(data_dir: Path | None = None) -> Path:
    return _settings_path(data_dir).with_name("sessions.enc")


def _vault_key_path(data_dir: Path | None = None) -> Path:
    return _settings_path(data_dir).with_name("vault.key")


def _load_or_create_vault_key(data_dir: Path | None = None) -> str:
    from cryptography.fernet import Fernet

    key_path = _vault_key_path(data_dir)
    vault_path = _vault_path(data_dir)
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(key_path.parent, 0o700)
        if key_path.exists():
            key_stat = key_path.lstat()
            if not stat.S_ISREG(key_stat.st_mode):
                raise SettingsError(
                    "The local Instagram vault key must be a regular file."
                )
            mode = stat.S_IMODE(key_stat.st_mode)
            if mode & 0o077:
                raise SettingsError(
                    "The local Instagram vault key has unsafe permissions; restrict "
                    "it to its owner before continuing."
                )
            return key_path.read_text(encoding="ascii").strip()
        if vault_path.exists():
            raise SettingsError(
                f"An encrypted Instagram vault already exists, but its local key is "
                f"missing. Restore {VAULT_KEY_ENV_VAR} or the original protected "
                "vault key; a new key cannot decrypt existing sessions."
            )

        key = Fernet.generate_key()
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=key_path.parent,
                prefix=".vault-key-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = temporary.name
                os.fchmod(temporary.fileno(), 0o600)
                temporary.write(key)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.link(temporary_path, key_path)
            except FileExistsError:
                pass
            return key_path.read_text(encoding="ascii").strip()
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
    except SettingsError:
        raise
    except (OSError, UnicodeDecodeError) as error:
        raise SettingsError(
            "A protected local key for encrypted Instagram sessions could not be "
            f"loaded or created ({type(error).__name__})."
        ) from None


def _vault_cipher(data_dir: Path | None = None) -> Any:
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise SettingsError(
            "Encrypted Instagram session storage requires the cryptography package."
        ) from None

    configured_key = os.environ.get(VAULT_KEY_ENV_VAR)
    if configured_key:
        try:
            return Fernet(configured_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            raise SettingsError(
                f"{VAULT_KEY_ENV_VAR} is not a valid Fernet key."
            ) from None

    local_key = _load_or_create_vault_key(data_dir)
    try:
        return Fernet(local_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise SettingsError(
            "The protected local Instagram vault key is invalid; restore a valid "
            f"{VAULT_KEY_ENV_VAR} or recover the original local key."
        ) from None


def _read_vault(data_dir: Path | None = None) -> dict[str, str]:
    path = _vault_path(data_dir)
    if not path.exists():
        return {}
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise SettingsError(
                "The encrypted Instagram vault has unsafe permissions; restrict it "
                "to its owner before continuing."
            )
        encrypted = path.read_bytes()
    except SettingsError:
        raise
    except OSError as error:
        raise SettingsError(
            f"Encrypted Instagram sessions could not be read ({type(error).__name__})."
        ) from None
    try:
        from cryptography.fernet import InvalidToken
    except ImportError:
        raise SettingsError(
            "Encrypted Instagram session storage requires the cryptography package."
        ) from None
    try:
        payload = _vault_cipher(data_dir).decrypt(encrypted)
        vault = json.loads(payload)
    except SettingsError:
        raise
    except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError):
        raise SettingsError(
            "The encrypted Instagram vault could not be decrypted. Check the "
            "configured vault key."
        ) from None
    if not isinstance(vault, dict) or not all(
        isinstance(account, str) and isinstance(value, str)
        for account, value in vault.items()
    ):
        raise SettingsError("The encrypted Instagram vault has an invalid format.")
    return vault


def _write_vault(vault: dict[str, str], data_dir: Path | None = None) -> None:
    path = _vault_path(data_dir)
    temporary_path: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        encrypted = _vault_cipher(data_dir).encrypt(
            json.dumps(vault, ensure_ascii=False).encode("utf-8")
        )
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=".sessions-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            os.fchmod(temporary.fileno(), 0o600)
            temporary.write(encrypted)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except (OSError, SettingsError) as error:
        if isinstance(error, SettingsError):
            raise
        raise SettingsError(
            f"Encrypted Instagram sessions could not be saved ({type(error).__name__})."
        ) from None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except OSError as error:
                _logger.warning(
                    "Could not remove a temporary encrypted session file (%s).",
                    type(error).__name__,
                )


def _normalize_account_label(label: str) -> str:
    normalized = label.strip().casefold()
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", normalized):
        raise ValueError(
            "Account labels must be 1-32 letters, numbers, underscores, or hyphens."
        )
    return normalized


def register_account(
    account: str, session_id: str, data_dir: Path | None = None
) -> None:
    normalized = _normalize_account_label(account)
    if not session_id or any(character.isspace() for character in session_id):
        raise SettingsError("Session configuration is invalid.")
    settings_path = _settings_path(data_dir)
    settings = _read_settings(data_dir) if settings_path.exists() else {}
    accounts = settings.get("accounts", [])
    if not isinstance(accounts, list) or any(
        not isinstance(value, str) for value in accounts
    ):
        raise SettingsError("Instagram account settings have an invalid format.")
    vault = _read_vault(data_dir)
    vault[normalized] = session_id
    _write_vault(vault, data_dir)
    if normalized not in accounts:
        accounts.append(normalized)
    settings["accounts"] = accounts
    settings.setdefault("primary_account", normalized)
    _write_settings(settings, data_dir)


def list_accounts(data_dir: Path | None = None) -> tuple[list[str], str | None]:
    settings = _read_settings(data_dir)
    accounts = settings.get("accounts", [])
    primary = settings.get("primary_account")
    if (
        not isinstance(accounts, list)
        or not all(isinstance(account, str) for account in accounts)
        or (primary is not None and not isinstance(primary, str))
    ):
        raise SettingsError("Instagram account settings have an invalid format.")
    return accounts, primary


def set_primary_account(account: str, data_dir: Path | None = None) -> None:
    normalized = _normalize_account_label(account)
    settings = _read_settings(data_dir)
    accounts = settings.get("accounts", [])
    if not isinstance(accounts, list) or normalized not in accounts:
        raise SettingsError("That Instagram account is not registered.")
    if normalized not in _read_vault(data_dir):
        raise SettingsError(
            "That account has no saved encrypted session; register it again with ~slogin."
        )
    settings["primary_account"] = normalized
    _write_settings(settings, data_dir)


def read_session(data_dir: Path | None = None) -> str | None:
    accounts, primary = list_accounts(data_dir)
    if primary is None:
        return None
    if primary not in accounts:
        raise SettingsError("The configured primary Instagram account is invalid.")
    return _read_vault(data_dir).get(primary)


def _registered_account_for_session(
    session_id: str, data_dir: Path | None = None
) -> str | None:
    session_bytes = session_id.encode("utf-8")
    return next(
        (
            account
            for account, saved_session in _read_vault(data_dir).items()
            if hmac.compare_digest(session_bytes, saved_session.encode("utf-8"))
        ),
        None,
    )


def _next_account_label(data_dir: Path | None = None) -> str:
    accounts, _ = list_accounts(data_dir)
    existing = set(accounts)
    index = 1
    while f"account-{index}" in existing:
        index += 1
    return f"account-{index}"


def _normalize_thread_url(url: str) -> str:
    return normalize_direct_thread_url(url)


def authorize_thread_destination(url: str, data_dir: Path | None = None) -> None:
    """Persist a destination after an authorized user explicitly confirms it."""
    normalized_url = _normalize_thread_url(url)
    settings = _read_settings(data_dir)
    destinations = settings.get("authorized_destinations", [])
    if not isinstance(destinations, list) or not all(
        isinstance(destination, str) for destination in destinations
    ):
        raise SettingsError(
            "Instagram destination settings have an invalid format."
        )
    try:
        normalized_destinations = [
            _normalize_thread_url(destination) for destination in destinations
        ]
    except ValueError:
        raise SettingsError(
            "Instagram destination settings contain an invalid thread URL."
        ) from None
    if normalized_url not in normalized_destinations:
        normalized_destinations.append(normalized_url)
    settings["authorized_destinations"] = normalized_destinations
    _write_settings(settings, data_dir)


def list_authorized_destinations(data_dir: Path | None = None) -> list[str]:
    settings = _read_settings(data_dir)
    destinations = settings.get("authorized_destinations", [])
    if not isinstance(destinations, list) or not all(
        isinstance(destination, str) for destination in destinations
    ):
        raise SettingsError("Instagram destination settings have an invalid format.")
    try:
        return [_normalize_thread_url(destination) for destination in destinations]
    except ValueError:
        raise SettingsError(
            "Instagram destination settings contain an invalid thread URL."
        ) from None


async def _validate_session(session_id: str) -> bool:
    try:
        return await validate_session(session_id)
    except InstagramAuthenticationError:
        return False
    except BrowserUnavailableError as error:
        raise SettingsError(str(error)) from None


@asynccontextmanager
async def _open_message_sender(
    session_id: str, url: str
) -> AsyncGenerator[Callable[[str], Awaitable[bool]], None]:
    try:
        async with open_message_sender(session_id, url) as send_message:
            yield send_message
    except InstagramAuthenticationError:
        raise SettingsError(
            "Instagram rejected the saved session; run ~slogin to validate it again."
        ) from None
    except BrowserUnavailableError as error:
        raise SettingsError(str(error)) from None
    except InstagramPageError as error:
        if error.submission_status == "not_submitted":
            raise SettingsError(str(error)) from None
        raise


async def handle_text_upload(
    update: Any,
    context: Any,
    is_authorized: Callable[[int], bool],
) -> None:
    """Validate and persist an authorized user's uploaded UTF-8 .txt file."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if not is_authorized(user.id):
        await message.reply_text("You are not authorized to configure Instagram text.")
        return

    document = getattr(message, "document", None)
    if document is None:
        return
    filename = getattr(document, "file_name", None)
    if not isinstance(filename, str) or Path(filename).suffix.lower() != ".txt":
        await message.reply_text("Upload a .txt file to configure Instagram text.")
        return
    size = getattr(document, "file_size", None)
    if not isinstance(size, int) or size < 0 or size > MAX_MESSAGE_FILE_BYTES:
        await message.reply_text(
            f"The .txt file must be no larger than {MAX_MESSAGE_FILE_BYTES} bytes."
        )
        return

    try:
        telegram_file = await context.bot.get_file(document.file_id)
        content = await telegram_file.download_as_bytearray()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _logger.warning(
            "Instagram message file download failed (%s).", type(error).__name__
        )
        await message.reply_text(
            f"The .txt file could not be downloaded ({type(error).__name__})."
        )
        return

    if len(content) > MAX_MESSAGE_FILE_BYTES:
        await message.reply_text(
            f"The .txt file must be no larger than {MAX_MESSAGE_FILE_BYTES} bytes."
        )
        return
    try:
        text = bytes(content).decode("utf-8").strip()
    except UnicodeDecodeError:
        await message.reply_text("The .txt file must contain valid UTF-8 text.")
        return
    if not text:
        await message.reply_text("The .txt file is empty.")
        return

    try:
        save_message_text(text)
    except SettingsError:
        await message.reply_text(str(error))
        return
    await message.reply_text(
        "Instagram message text saved locally. No Instagram message was sent."
    )


def start_operation(
    chat_id: int,
    user_id: int,
    operation_factory: Callable[[], Awaitable[Any]],
) -> bool:
    """Track a single module-owned operation to prevent concurrent sends."""
    key = (chat_id, user_id)
    existing = active_operations.get(key)
    if (existing is not None and not existing.done()) or any(
        not task.done() for task in active_operations.values()
    ):
        return False

    async def run() -> Any:
        current_task = asyncio.current_task()
        try:
            return await operation_factory()
        finally:
            if active_operations.get(key) is current_task:
                active_operations.pop(key, None)

    active_operations[key] = asyncio.create_task(run())
    task = active_operations[key]

    def report_completion(completed: asyncio.Task[Any]) -> None:
        if active_operations.get(key) is completed:
            active_operations.pop(key, None)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            _logger.error("Instagram operation failed (%s).", type(error).__name__)

    task.add_done_callback(report_completion)
    return True


def start_igstart_operation(
    chat_id: int,
    user_id: int,
    state: dict[str, Any],
    operation_factory: Callable[[], Awaitable[Any]],
) -> bool:
    """Start an independent thread send while tracking it for cancellation."""
    key = (chat_id, user_id)
    if key in active_operations and key not in igstart_operations:
        return False
    active_runs = igstart_operations.get(key, {})
    if sum(not task.done() for task in active_runs) >= MAX_CONCURRENT_IGSTARTS:
        return False

    async def run() -> Any:
        current_task = asyncio.current_task()
        try:
            return await operation_factory()
        finally:
            if current_task is not None:
                runs = igstart_operations.get(key)
                if runs is not None:
                    runs.pop(current_task, None)
                    if not runs:
                        igstart_operations.pop(key, None)
                if active_operations.get(key) is current_task:
                    remaining = igstart_operations.get(key, {})
                    if remaining:
                        active_operations[key] = next(iter(remaining))
                    else:
                        active_operations.pop(key, None)

    task = asyncio.create_task(run())
    igstart_operations.setdefault(key, {})[task] = state
    active_operations[key] = task

    def report_completion(completed: asyncio.Task[Any]) -> None:
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            _logger.error("Instagram operation failed (%s).", type(error).__name__)

    task.add_done_callback(report_completion)
    return True


def _has_pending_login(chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    expires_at = pending_logins.get(key)
    if expires_at is None:
        return False
    if expires_at <= time.monotonic():
        pending_logins.pop(key, None)
        return False
    return True


def has_login_prompt(chat_id: int, user_id: int) -> bool:
    """Return whether a login prompt exists, including one awaiting timeout cleanup."""
    return (chat_id, user_id) in pending_logins


def has_pending_interaction(chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    state = pending_igstarts.get(key)
    return has_login_prompt(chat_id, user_id) or (
        state is not None and state.get("stage") != "sending"
    )


def cancel_pending_login(chat_id: int, user_id: int) -> bool:
    """Clear only this user's pending prompts, without touching active tasks."""
    key = (chat_id, user_id)
    login_cleared = pending_logins.pop(key, None) is not None
    igstart_cleared = pending_igstarts.pop(key, None) is not None
    return login_cleared or igstart_cleared


async def cancel_pending_interaction(chat_id: int, user_id: int) -> str:
    """Cancel this user's prompt or thread validation, but not a confirmed send."""
    key = (chat_id, user_id)
    state = pending_igstarts.get(key)
    if state is not None and state.get("stage") == "sending":
        return "sending"
    if state is not None:
        pending_igstarts.pop(key, None)
    pending_logins.pop(key, None)
    validation_tasks = [
        task
        for task, run_state in igstart_operations.get(key, {}).items()
        if run_state.get("stage") == "validating"
    ]
    if state is not None and state.get("stage") == "validating":
        task = active_operations.get(key)
        if task is not None and task not in validation_tasks:
            validation_tasks.append(task)
    for task in validation_tasks:
        task.cancel()
    if validation_tasks:
        await asyncio.gather(*validation_tasks, return_exceptions=True)
    if state is None and not validation_tasks and any(
        run_state.get("stage") == "sending"
        for run_state in igstart_operations.get(key, {}).values()
    ):
        return "sending"
    return "cancelled"


async def _finish_account_login(
    chat: Any,
    chat_id: int,
    user_id: int,
    session_id: str,
    data_dir: Path | None,
) -> None:
    key = (chat_id, user_id)
    try:
        duplicate = _registered_account_for_session(session_id, data_dir)
        if duplicate is not None:
            await chat.send_message(
                f"This session is already registered as {duplicate}."
            )
            return
        if not await _validate_session(session_id):
            await chat.send_message(
                "Instagram rejected that session. The account was not registered."
            )
            return
        account = _next_account_label(data_dir)
        register_account(account, session_id, data_dir)
    except asyncio.CancelledError:
        raise
    except SettingsError:
        await chat.send_message(
            "The session could not be securely validated or saved. No account was registered."
        )
        return
    except Exception as error:
        _logger.warning(
            "Instagram account validation failed (%s).", type(error).__name__
        )
        await chat.send_message(
            "Instagram account validation failed; no account was registered."
        )
        return
    finally:
        pending_logins.pop(key, None)
    await chat.send_message(
        f"Instagram account {account} validated and registered securely."
    )


async def handle_login_reply(
    update: Any,
    is_authorized: Callable[[int], bool],
    data_dir: Path | None = None,
) -> bool:
    """Delete, validate, and securely register a session sent in the pending chat."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return False
    key = (chat.id, user.id)
    expires_at = pending_logins.get(key)
    if expires_at is None:
        return False
    text = getattr(message, "text", None)
    if not isinstance(text, str) or text.startswith(("~", "†", "/")):
        return False
    pending_logins.pop(key, None)
    try:
        await message.delete()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _logger.warning(
            "Could not delete sensitive Instagram login input (%s).",
            type(error).__name__,
        )
        try:
            await chat.send_message(
                "The login input could not be deleted, so it was not processed. "
                "Delete it manually and try again in a private chat."
            )
        except Exception:
            pass
        return True

    if expires_at <= time.monotonic():
        try:
            await chat.send_message(
                "The Instagram login prompt expired; run ~slogin again."
            )
        except Exception:
            pass
        return True
    if not is_authorized(user.id):
        try:
            await chat.send_message(
                "You are not authorized to register Instagram accounts."
            )
        except Exception:
            pass
        return True

    session_id = text.strip()
    if (
        not session_id
        or len(session_id) > MAX_SESSION_ID_LENGTH
        or any(character.isspace() for character in session_id)
    ):
        try:
            await chat.send_message(
                "That input is not a valid session credential; no account was registered."
            )
        except Exception:
            pass
        return True
    try:
        _vault_cipher(data_dir)
    except SettingsError as error:
        _logger.warning("Secure Instagram session storage is unavailable.")
        try:
            await chat.send_message(
                f"{error} No account was registered."
            )
        except Exception:
            pass
        return True

    if not start_operation(
        chat.id,
        user.id,
        lambda: _finish_account_login(
            chat, chat.id, user.id, session_id, data_dir
        ),
    ):
        try:
            await chat.send_message(
                "An Instagram operation is already active; no registration was started."
            )
        except Exception:
            pass
        return True
    try:
        await chat.send_message(
            "Validating Instagram session. Use ~stopatck to cancel."
        )
    except Exception:
        task = active_operations.get(key)
        if task is not None:
            task.cancel()
    return True


async def _stop_operation(chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    had_pending_login = pending_logins.pop(key, None) is not None
    had_pending_igstart = pending_igstarts.pop(key, None) is not None
    tasks = set(igstart_operations.get(key, {}))
    task = active_operations.get(key)
    if task is not None:
        tasks.add(task)
    tasks = {active_task for active_task in tasks if not active_task.done()}
    for active_task in tasks:
        active_task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    if active_operations.get(key) in tasks:
        active_operations.pop(key, None)
    return bool(tasks) or had_pending_login or had_pending_igstart


async def shutdown() -> None:
    """Cancel and await all operations owned by this module."""
    tasks = set(active_operations.values())
    for runs in igstart_operations.values():
        tasks.update(runs)
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for key, task in list(active_operations.items()):
        if active_operations.get(key) is task:
            active_operations.pop(key, None)
    igstart_operations.clear()
    pending_logins.clear()
    pending_igstarts.clear()


async def _prompt_for_thread_url(
    message: Any, key: ActiveKey, account: str
) -> None:
    pending_igstarts[key] = {
        "stage": "thread_url",
        "account": account,
        "expires_at": time.monotonic() + IGSTART_PROMPT_TTL_SECONDS,
    }
    await message.reply_text(
        f"Primary account: {account}\n\n"
        "Please send the Instagram group chat thread URL. Send /cancel to cancel."
    )


def _read_account_session(account: str) -> str:
    accounts, primary = list_accounts()
    if account not in accounts or primary != account:
        raise SettingsError("The selected primary Instagram account has changed.")
    session_id = read_session()
    if session_id is None:
        raise SettingsError(
            "The primary Instagram account has no saved encrypted session. "
            "Register it again with ~slogin."
        )
    return session_id


def _message_sequence(message_text: str) -> list[str]:
    if len(message_text.encode("utf-8")) > MAX_MESSAGE_FILE_BYTES:
        raise SettingsError(
            f"Saved Instagram messages must total no more than "
            f"{MAX_MESSAGE_FILE_BYTES} bytes."
        )
    messages = [line.strip() for line in message_text.splitlines() if line.strip()]
    if not messages:
        return []
    if any(len(line) > MAX_MESSAGE_LENGTH for line in messages):
        raise SettingsError(
            f"Each saved message must be no longer than "
            f"{MAX_MESSAGE_LENGTH} characters."
        )
    return messages


async def _submit_repeatedly(
    message: Any,
    key: ActiveKey,
    user_id: int,
    state: dict[str, Any],
    messages: list[str],
    is_authorized: Callable[[int], bool],
    send_message: Callable[[str], Awaitable[bool]],
) -> None:
    sent_count = 0
    try:
        for send_number in range(1, MAX_IGSTART_SENDS + 1):
            if not is_authorized(user_id):
                await message.reply_text(
                    "The operation stopped because Instagram authorization "
                    "is no longer available."
                )
                return
            message_index = sent_count % len(messages)
            submitted_text = messages[message_index] + random.choice(MESSAGE_EMOJIS)
            try:
                send_action_completed = await send_message(submitted_text)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                detail = (
                    str(error)
                    if isinstance(error, SettingsError)
                    or getattr(error, "submission_status", None)
                    in {"not_submitted", "uncertain", "sent"}
                    else type(error).__name__
                )
                submission_status = getattr(
                    error, "submission_status", "not_submitted"
                )
                _logger.warning(
                    "Instagram message attempt failed (%s, status=%s).",
                    type(error).__name__,
                    submission_status,
                )
                if submission_status == "sent":
                    sent_count += 1
                    state["sent_count"] = sent_count
                    await message.reply_text(
                        f"Message {send_number} ({message_index + 1}/"
                        f"{len(messages)}) was submitted, but browser "
                        f"cleanup failed ({detail}); stopping. "
                        f"{sent_count} send action(s) completed."
                    )
                elif submission_status == "not_submitted" or isinstance(
                    error, SettingsError
                ):
                    await message.reply_text(
                        f"Message {send_number} was not submitted "
                        f"({detail}); stopping. {sent_count} earlier "
                        "send action(s) completed."
                    )
                else:
                    await message.reply_text(
                        "An Instagram message may have been sent, but "
                        "delivery could not be confirmed. The operation "
                        "stopped to avoid duplicates. Check the thread "
                        "before restarting."
                    )
                return
            if not send_action_completed:
                _logger.warning(
                    "Instagram message attempt returned an uncertain result."
                )
                await message.reply_text(
                    "An Instagram message may have been sent, but "
                    "delivery could not be confirmed. The operation "
                    "stopped to avoid duplicates. Check the thread "
                    "before restarting."
                )
                return
            sent_count += 1
            state["sent_count"] = sent_count
            if send_number == MAX_IGSTART_SENDS:
                state["stage"] = "completed"
                await message.reply_text(
                    f"Reached the configured limit of {MAX_IGSTART_SENDS} "
                    "messages. Send actions completed; delivery was not "
                    "independently verified."
                )
                return
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _logger.warning(
            "Instagram repeated-message operation failed (%s).",
            type(error).__name__,
        )
        detail = (
            str(error) if isinstance(error, SettingsError) else type(error).__name__
        )
        await message.reply_text(
            "The operation stopped before another message could be "
            f"submitted ({detail}). {sent_count} earlier message(s) "
            "had completed send actions."
        )
    finally:
        if pending_igstarts.get(key) is state:
            pending_igstarts.pop(key, None)


async def _begin_thread_validation(
    message: Any,
    key: ActiveKey,
    user_id: int,
    state: dict[str, Any],
    url: str,
    is_authorized: Callable[[int], bool],
) -> None:
    account = state["account"]
    try:
        session_id = _read_account_session(account)
    except (SettingsError, ValueError) as error:
        if pending_igstarts.get(key) is state:
            pending_igstarts.pop(key, None)
        await message.reply_text(str(error))
        return

    state["stage"] = "validating"
    state["url"] = url

    async def validate_destination() -> None:
        try:
            if not is_authorized(user_id):
                await message.reply_text(
                    "You are not authorized to use Instagram commands."
                )
                return
            if state.get("expires_at", 0) <= time.monotonic():
                await message.reply_text(
                    "The ~igstart prompt expired. Run ~igstart again."
                )
                return
            message_text = read_message_text()
            if message_text is None or not message_text.strip():
                await message.reply_text(
                    "No message text is saved. Use ~txtset to upload your .txt file."
                )
                return
            messages = _message_sequence(message_text)
            if not messages:
                await message.reply_text(
                    "No message text is saved. Use ~txtset to upload your .txt file."
                )
                return

            async with _open_message_sender(session_id, url) as send_message:
                if not is_authorized(user_id):
                    await message.reply_text(
                        "You are not authorized to use Instagram commands."
                    )
                    return
                try:
                    authorize_thread_destination(url)
                except SettingsError as error:
                    await message.reply_text(str(error))
                    return
                state["stage"] = "sending"
                state.pop("expires_at", None)
                state["sent_count"] = 0
                if key not in pending_igstarts:
                    pending_igstarts[key] = state
                await message.reply_text(
                    f"Thread validated. Sending up to {MAX_IGSTART_SENDS} "
                    "messages without intentional delay. Use ~stopatck to stop."
                )
                await _submit_repeatedly(
                    message,
                    key,
                    user_id,
                    state,
                    messages,
                    is_authorized,
                    send_message,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if state.get("sent_count", 0):
                await message.reply_text(
                    "The browser session failed to close after "
                    f"{state['sent_count']} send action(s). Check the thread "
                    "before restarting."
                )
            else:
                detail = (
                    str(error)
                    if isinstance(error, SettingsError)
                    else type(error).__name__
                )
                await message.reply_text(
                    "Thread validation failed; no message was sent.\n"
                    f"Reason: {detail}"
                )

    active_runs = igstart_operations.get(key, {})
    if sum(not task.done() for task in active_runs) >= MAX_CONCURRENT_IGSTARTS:
        if pending_igstarts.get(key) is state:
            pending_igstarts.pop(key, None)
        await message.reply_text(
            f"You can run up to {MAX_CONCURRENT_IGSTARTS} Instagram threads at "
            "once. Wait for a run to finish or use ~stopatck, then start again."
        )
        return
    if pending_igstarts.get(key) is state:
        pending_igstarts.pop(key, None)
    if not start_igstart_operation(
        key[0], user_id, state, validate_destination
    ):
        state["stage"] = "thread_url"
        state.pop("url", None)
        pending_igstarts[key] = state
        await message.reply_text(
            "An Instagram operation is already active; no validation was started."
        )
        return
    await message.reply_text(
        "Checking whether this account can access the thread. Sending starts "
        "automatically if it can."
    )


async def _handle_igstart_input(
    update: Any, is_authorized: Callable[[int], bool]
) -> bool:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return False
    key = (chat.id, user.id)
    state = pending_igstarts.get(key)
    if state is None:
        return False
    if (
        state.get("stage") != "sending"
        and state["expires_at"] <= time.monotonic()
    ):
        pending_igstarts.pop(key, None)
        await message.reply_text("The ~igstart prompt expired. Run ~igstart again.")
        return True
    if not is_authorized(user.id):
        pending_igstarts.pop(key, None)
        await message.reply_text("You are not authorized to use Instagram commands.")
        return True

    text = getattr(message, "text", "")
    if not isinstance(text, str):
        return True
    stage = state["stage"]
    if stage == "account":
        if not text.strip().isdecimal():
            await message.reply_text("Choose a saved account by its number.")
            return True
        try:
            accounts, _ = list_accounts()
            account_number = int(text.strip())
            if not 1 <= account_number <= len(accounts):
                raise IndexError
            account = accounts[account_number - 1]
            set_primary_account(account)
        except IndexError:
            await message.reply_text("That account number is not in the saved list.")
            return True
        except (SettingsError, ValueError) as error:
            pending_igstarts.pop(key, None)
            await message.reply_text(str(error))
            return True
        try:
            _read_account_session(account)
        except SettingsError as error:
            pending_igstarts.pop(key, None)
            await message.reply_text(str(error))
            return True
        state["account"] = account
        if state.get("url"):
            await _begin_thread_validation(
                message, key, user.id, state, state["url"], is_authorized
            )
        else:
            await _prompt_for_thread_url(message, key, account)
        return True

    if stage == "thread_url":
        url = text.strip()
        try:
            approved_url = _normalize_thread_url(url)
        except ValueError:
            await message.reply_text(
                "That is not a valid Instagram direct-thread URL. Send an HTTPS "
                "www.instagram.com/direct/t/<numeric-thread-id>/ URL or /cancel."
            )
            return True
        await _begin_thread_validation(
            message, key, user.id, state, approved_url, is_authorized
        )
        return True

    await message.reply_text("An Instagram operation is already in progress.")
    return True


async def handle_pending_input(
    update: Any, is_authorized: Callable[[int], bool]
) -> bool:
    """Handle one pending session-registration or ~igstart interaction input."""
    if await handle_login_reply(update, is_authorized):
        return True
    return await _handle_igstart_input(update, is_authorized)


async def show_menu(chat: Any) -> None:
    await chat.send_message(
        "Instagram commands:\n"
        "~slogin - securely register a session sent in this private chat\n"
        "~igaccounts - list numbered saved accounts and the primary account\n"
        "~igprimary <number|account> - select the account for ~igstart\n"
        "~nlogin - unavailable; the browser code has no username/password login flow\n"
        "~txtset <text> - save message text locally, or upload a .txt file\n"
        f"~igstart [thread URL] - send saved messages quickly; up to "
        f"{MAX_CONCURRENT_IGSTARTS} threads in parallel\n"
        "~stopatck - cancel all your active operations in this chat\n"
        "~refresh - no disposable browser state is retained"
    )


async def handle_command(
    command: str,
    update: Any,
    is_authorized: Callable[[int], bool],
) -> None:
    """Handle Instagram commands without owning Telegram authentication/state."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return
    if not is_authorized(user.id):
        await message.reply_text("You are not authorized to use Instagram commands.")
        return

    if command == "ig":
        await show_menu(chat)
    elif command == "slogin":
        if getattr(chat, "type", "private") != "private":
            await message.reply_text(
                "For your security, ~slogin is available only in a private chat with this bot."
            )
            return
        if _has_pending_login(chat.id, user.id):
            await message.reply_text(
                "An Instagram account prompt is already waiting for your reply."
            )
            return
        await message.reply_text(
            "Please send your Instagram session ID to register your account."
        )
        pending_logins[(chat.id, user.id)] = (
            time.monotonic() + LOGIN_PROMPT_TTL_SECONDS
        )
    elif command == "igaccounts":
        try:
            accounts, primary = list_accounts()
        except SettingsError as error:
            await message.reply_text(str(error))
            return
        if not accounts:
            await message.reply_text("No Instagram accounts are registered.")
            return
        lines = [
            f"{index}. {'* ' if account == primary else ''}{account}"
            for index, account in enumerate(accounts, start=1)
        ]
        await message.reply_text(
            "Saved Instagram accounts ( * = primary ):\n" + "\n".join(lines)
        )
    elif command == "igprimary":
        _, separator, selection = (message.text or "").partition(" ")
        selection = selection.strip() if separator else ""
        if not selection:
            await message.reply_text("Usage: ~igprimary <number|account>")
            return
        try:
            accounts, _ = list_accounts()
        except SettingsError as error:
            await message.reply_text(str(error))
            return
        account = selection
        if selection.isdecimal():
            account_number = int(selection)
            if not 1 <= account_number <= len(accounts):
                await message.reply_text("That account number is not in the saved list.")
                return
            account = accounts[account_number - 1]
        try:
            set_primary_account(account)
        except (SettingsError, ValueError) as error:
            await message.reply_text(str(error))
            return
        await message.reply_text(f"Primary Instagram account set to '{account.casefold()}'.")
    elif command == "nlogin":
        await message.reply_text(
            "Normal login is not available in the current browser implementation. "
            "Use ~slogin if session-based login is supported."
        )
    elif command == "igstart":
        _, separator, arguments = (message.text or "").partition(" ")
        requested_url = None
        if separator and arguments.strip():
            try:
                requested_url = _normalize_thread_url(arguments.strip())
            except ValueError:
                await message.reply_text(
                    "Use ~igstart with an HTTPS "
                    "www.instagram.com/direct/t/<numeric-thread-id>/ URL, "
                    "or send ~igstart without a URL to be prompted."
                )
                return
        key = (chat.id, user.id)
        if has_pending_interaction(chat.id, user.id):
            await message.reply_text(
                "A pending Instagram interaction already exists. Reply to its prompt "
                "or use /cancel first."
            )
            return
        try:
            accounts, primary = list_accounts()
        except SettingsError as error:
            await message.reply_text(str(error))
            return
        if not accounts:
            await message.reply_text(
                "No Instagram account is registered. Use ~slogin first."
            )
            return
        if primary is None:
            pending_igstarts[key] = {
                "stage": "account",
                "expires_at": time.monotonic() + IGSTART_PROMPT_TTL_SECONDS,
            }
            if requested_url is not None:
                pending_igstarts[key]["url"] = requested_url
            account_list = "\n".join(
                f"{index}. {account}"
                for index, account in enumerate(accounts, start=1)
            )
            await message.reply_text(
                "No primary Instagram account is configured. Select one by number:\n"
                f"{account_list}\n\nSend /cancel to cancel."
            )
            return

        try:
            _read_account_session(primary)
        except SettingsError as error:
            await message.reply_text(str(error))
            return
        state = {
            "stage": "thread_url",
            "account": primary,
            "expires_at": time.monotonic() + IGSTART_PROMPT_TTL_SECONDS,
        }
        pending_igstarts[key] = state
        if requested_url is None:
            await message.reply_text(
                f"Primary account: {primary}\n\n"
                "Please send the Instagram group chat thread URL. "
                "Send /cancel to cancel."
            )
            return
        await _begin_thread_validation(
            message, key, user.id, state, requested_url, is_authorized
        )
    elif command == "txtset":
        raw_text = message.text or ""
        _, separator, text = raw_text.partition(" ")
        text = text.strip() if separator else ""
        if not text:
            await message.reply_text("Usage: ~txtset <message text>")
            return
        try:
            save_message_text(text)
        except SettingsError as error:
            await message.reply_text(str(error))
            return
        await message.reply_text(
            "Instagram message text saved locally. No Instagram message was sent."
        )
    elif command == "stopatck":
        submission_in_progress = (
            any(
                run_state.get("stage") == "sending"
                for run_state in igstart_operations.get((chat.id, user.id), {}).values()
            )
        )
        stopped = await _stop_operation(chat.id, user.id)
        if stopped:
            if submission_in_progress:
                await message.reply_text(
                    "Cancellation was requested during submission. Check the thread "
                    "before retrying because Instagram may already have accepted it."
                )
            else:
                await message.reply_text("Stopped this user's Instagram operation.")
        else:
            await message.reply_text(
                "No Instagram operation started by this module is running for you "
                "in this chat."
            )
    elif command == "refresh":
        await message.reply_text(
            "No disposable persistent Instagram resources exist to clean. Browser "
            "contexts are closed after each operation; saved message text and "
            "the encrypted session vault were left unchanged."
        )
    else:
        await message.reply_text("Unknown Instagram command.")


SEND_COMPLETION_TIMEOUT_SECONDS = 10
THREAD_URL_WAIT_TIMEOUT_MS = 10_000

try:
    async_playwright: Callable[[], Any] | None = import_module(
        "playwright.async_api"
    ).async_playwright
except (ImportError, AttributeError):
    async_playwright = None


class InstagramAuthenticationError(RuntimeError):
    """Raised when Instagram redirects a session to its login flow."""


class InstagramPageError(RuntimeError):
    """Raised when the expected Instagram page is not available."""

    def __init__(self, message: str, *, submission_status: str = "not_submitted"):
        super().__init__(message)
        self.submission_status = submission_status


class BrowserUnavailableError(RuntimeError):
    """Raised when the optional Playwright browser dependency is unavailable."""


def _playwright_manager():
    if async_playwright is None:
        raise BrowserUnavailableError(
            "Playwright is required for Instagram browser operations; install "
            "Playwright and its Chromium browser first."
        )
    return async_playwright()


def normalize_direct_thread_url(url: str) -> str:
    """Validate and canonicalize an Instagram direct-thread URL."""
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ValueError("Enter a valid Instagram direct thread URL.") from None
    match = re.fullmatch(r"/direct/t/([0-9]{1,32})/?", parts.path)
    if (
        parts.scheme != "https"
        or parts.netloc.lower() != "www.instagram.com"
        or match is None
        or int(match.group(1)) == 0
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "Enter an HTTPS www.instagram.com/direct/t/<numeric-thread-id>/ URL."
        )
    return f"https://www.instagram.com/direct/t/{match.group(1)}/"


def _validate_direct_url(url: str) -> str:
    return normalize_direct_thread_url(url)


def _message_box_locator(page):
    return page.locator(
        'div[aria-label="Message"][contenteditable="true"], '
        'div[role="textbox"][contenteditable="true"], '
        'textarea[aria-label="Message"], '
        'div[role="textbox"], div[aria-label="Message"]'
    ).first


async def _wait_for_message_box_clear(message_box) -> None:
    deadline = (
        asyncio.get_running_loop().time() + SEND_COMPLETION_TIMEOUT_SECONDS
    )
    while True:
        text = await message_box.evaluate(
            "(element) => String(element.value ?? element.textContent ?? '')"
        )
        if not text.strip():
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise InstagramPageError(
                "Instagram did not clear the message box after submission.",
                submission_status="uncertain",
            )
        await asyncio.sleep(0.1)


def _matches_thread_url(candidate_url: str, requested_url: str) -> bool:
    candidate = urlsplit(candidate_url)
    requested = urlsplit(requested_url)
    return (
        candidate.scheme == "https"
        and candidate.netloc.lower() == "www.instagram.com"
        and candidate.path.rstrip("/") == requested.path.rstrip("/")
    )


def _thread_redirect_error(page_url: str) -> InstagramPageError:
    resolved = urlsplit(page_url)
    if resolved.path in {"", "/"}:
        return InstagramPageError(
            "Instagram redirected the requested thread to its home page. "
            "The URL format is valid, but this account could not open that "
            "conversation. Confirm the thread still exists and the primary "
            "account is a member."
        )
    return InstagramPageError(
        "Instagram opened "
        f"{resolved.netloc}{resolved.path} instead of the requested thread."
    )


async def _wait_for_requested_thread(page, url: str) -> None:
    try:
        await page.wait_for_url(
            lambda candidate_url: _matches_thread_url(candidate_url, url),
            timeout=THREAD_URL_WAIT_TIMEOUT_MS,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if not _matches_thread_url(page.url, url):
            raise _thread_redirect_error(page.url) from error
    if not _matches_thread_url(page.url, url):
        raise _thread_redirect_error(page.url)


async def _add_session_cookie(context, session_id: str) -> None:
    if not session_id or any(character.isspace() for character in session_id):
        raise ValueError("A valid Instagram session is required.")
    await context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": session_id,
                "domain": ".instagram.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            }
        ]
    )


async def _require_authenticated_page(page) -> None:
    path = urlsplit(page.url).path.lower()
    if path.startswith("/accounts/login"):
        raise InstagramAuthenticationError("Instagram requires a valid session.")


async def _send_message_on_page(
    page, url: str, text: str, *, refresh: bool, wait_for_clear: bool = True
) -> bool:
    if not text.strip():
        raise ValueError("Message text cannot be empty.")
    if not _matches_thread_url(page.url, url):
        raise _thread_redirect_error(page.url)

    submission_attempted = False
    try:
        message_box = _message_box_locator(page)
        try:
            await message_box.wait_for(state="visible", timeout=15_000)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise InstagramPageError(
                "Instagram did not show a message box for the approved thread."
            ) from error
        await message_box.fill(text)
        send_button = page.get_by_role("button", name="Send", exact=True)
        if await send_button.count():
            submission_attempted = True
            await send_button.last.click()
        else:
            submission_attempted = True
            await message_box.press("Enter")
        if wait_for_clear:
            await _wait_for_message_box_clear(message_box)
        await _require_authenticated_page(page)
        if not _matches_thread_url(page.url, url):
            raise _thread_redirect_error(page.url)
        if refresh:
            await page.reload(wait_until="domcontentloaded", timeout=30_000)
            await _require_authenticated_page(page)
            if not _matches_thread_url(page.url, url):
                raise _thread_redirect_error(page.url)
        return True
    except asyncio.CancelledError:
        raise
    except (BrowserUnavailableError, InstagramAuthenticationError, ValueError) as error:
        if submission_attempted:
            raise InstagramPageError(
                str(error),
                submission_status="uncertain",
            ) from None
        raise
    except InstagramPageError as error:
        if submission_attempted and error.submission_status != "uncertain":
            raise InstagramPageError(
                str(error),
                submission_status="uncertain",
            ) from error
        raise
    except Exception as error:
        status = "uncertain" if submission_attempted else "not_submitted"
        detail = (
            "Instagram could not complete the send action."
            if submission_attempted
            else "Instagram failed before the message was submitted."
        )
        raise InstagramPageError(detail, submission_status=status) from error


async def _recover_thread_page(context, page, url: str):
    try:
        if _matches_thread_url(page.url, url):
            await page.reload(wait_until="domcontentloaded", timeout=30_000)
        else:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        await _require_authenticated_page(page)
        await _wait_for_requested_thread(page, url)
        message_box = _message_box_locator(page)
        await message_box.wait_for(state="visible", timeout=15_000)
    except asyncio.CancelledError:
        raise
    except Exception as refresh_error:
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await _require_authenticated_page(page)
            await _wait_for_requested_thread(page, url)
            message_box = _message_box_locator(page)
            await message_box.wait_for(state="visible", timeout=15_000)
        except asyncio.CancelledError:
            raise
        except Exception as reopen_error:
            raise InstagramPageError(
                "Instagram could not recover the approved thread after refreshing "
                f"or reopening its tab ({type(reopen_error).__name__}).",
                submission_status="not_submitted",
            ) from refresh_error
    return page


@asynccontextmanager
async def open_message_sender(
    session_id: str, url: str
) -> AsyncGenerator[Callable[[str], Awaitable[bool]], None]:
    """Open one authenticated thread for multiple sends without reloading it."""
    url = _validate_direct_url(url)
    async with _playwright_manager() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            try:
                await _add_session_cookie(context, session_id)
                page = await context.new_page()
                await page.goto(
                    "https://www.instagram.com/direct/inbox/",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                await _require_authenticated_page(page)
                if "/direct/inbox" not in urlsplit(page.url).path.lower():
                    raise InstagramAuthenticationError(
                        "Instagram did not open the authenticated direct inbox. "
                        "Run ~slogin to validate the saved session again."
                    )
                if await page.locator('input[name="username"]').count():
                    raise InstagramAuthenticationError(
                        "Instagram displayed its login form. "
                        "Run ~slogin to validate the saved session again."
                    )
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await _require_authenticated_page(page)
                await _wait_for_requested_thread(page, url)
                message_box = _message_box_locator(page)
                try:
                    await message_box.wait_for(state="visible", timeout=15_000)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    raise InstagramPageError(
                        "Instagram did not show a message box for the approved thread."
                    ) from error

                async def send_message(text: str) -> bool:
                    nonlocal page
                    try:
                        return await _send_message_on_page(
                            page, url, text, refresh=False, wait_for_clear=False
                        )
                    except InstagramPageError as error:
                        if error.submission_status != "not_submitted":
                            raise
                        page = await _recover_thread_page(context, page, url)
                        return await _send_message_on_page(
                            page, url, text, refresh=False, wait_for_clear=False
                        )

                yield send_message
            finally:
                await context.close()
        finally:
            await browser.close()


async def validate_session(session_id: str) -> bool:
    """Check that the session can open Instagram's direct inbox without logging in."""
    async with _playwright_manager() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            try:
                await _add_session_cookie(context, session_id)
                page = await context.new_page()
                await page.goto(
                    "https://www.instagram.com/direct/inbox/",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                await _require_authenticated_page(page)
                if "/direct/inbox" not in urlsplit(page.url).path.lower():
                    raise InstagramAuthenticationError(
                        "Instagram did not open the authenticated direct inbox."
                    )
                if await page.locator('input[name="username"]').count():
                    raise InstagramAuthenticationError(
                        "Instagram displayed its login form."
                    )
                return True
            finally:
                await context.close()
        finally:
            await browser.close()


async def validate_thread(session_id: str, url: str) -> None:
    """Verify the session opens the requested direct thread and its message box."""
    url = _validate_direct_url(url)
    async with _playwright_manager() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            try:
                await _add_session_cookie(context, session_id)
                page = await context.new_page()
                await page.goto(
                    "https://www.instagram.com/direct/inbox/",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                await _require_authenticated_page(page)
                if "/direct/inbox" not in urlsplit(page.url).path.lower():
                    raise InstagramAuthenticationError(
                        "Instagram did not open the authenticated direct inbox. "
                        "Run ~slogin to validate the saved session again."
                    )
                if await page.locator('input[name="username"]').count():
                    raise InstagramAuthenticationError(
                        "Instagram displayed its login form. "
                        "Run ~slogin to validate the saved session again."
                    )
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                await _require_authenticated_page(page)
                await _wait_for_requested_thread(page, url)
                message_box = _message_box_locator(page)
                try:
                    await message_box.wait_for(state="visible", timeout=15_000)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    raise InstagramPageError(
                        "Instagram did not show a message box for the requested thread."
                    ) from error
                if not _matches_thread_url(page.url, url):
                    raise _thread_redirect_error(page.url)
            finally:
                await context.close()
        finally:
            await browser.close()


async def send_message_once(session_id: str, url: str, text: str) -> bool:
    """Attempt one message and refresh the approved thread without scanning messages."""
    url = _validate_direct_url(url)
    if not text.strip():
        raise ValueError("Message text cannot be empty.")

    submission_completed = False
    try:
        async with _playwright_manager() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                try:
                    await _add_session_cookie(context, session_id)
                    page = await context.new_page()
                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    await _require_authenticated_page(page)
                    await _wait_for_requested_thread(page, url)
                    result = await _send_message_on_page(
                        page, url, text, refresh=True
                    )
                    submission_completed = True
                    return result
                finally:
                    await context.close()
            finally:
                await browser.close()
    except asyncio.CancelledError:
        raise
    except (BrowserUnavailableError, InstagramAuthenticationError, ValueError) as error:
        if submission_completed:
            raise InstagramPageError(
                str(error),
                submission_status="uncertain",
            ) from None
        raise
    except InstagramPageError:
        raise
    except Exception as error:
        status = "uncertain" if submission_completed else "not_submitted"
        detail = (
            "Instagram could not complete the send-and-refresh action."
            if submission_completed
            else "Instagram failed before the message was submitted."
        )
        raise InstagramPageError(detail, submission_status=status) from error
