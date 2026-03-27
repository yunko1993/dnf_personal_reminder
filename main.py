import asyncio
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from astrbot.api.all import *


PLUGIN_ID = "dnf_personal_reminder"
PLUGIN_TITLE = "\u0044\u004e\u0046 \u79c1\u4eba\u63d0\u9192\u79d8\u4e66"
PLUGIN_VERSION = "1.5.0"

CMD_ADD = "\u63d0\u9192\u6dfb\u52a0"
CMD_LIST = "\u63d0\u9192\u5217\u8868"
CMD_DELETE = "\u63d0\u9192\u5220\u9664"
CMD_TEST = "\u63d0\u9192\u6d4b\u8bd5"

DEFAULT_DATA_DIR_NAME = "dnf_personal_reminder"
LEGACY_DATA_DIR_NAMES = [
    "astrbot_plugin_dnf_reminder",
]
DATA_FILE_NAME = "reminders.json"

MSG_INVALID_FORMAT = "\u683c\u5f0f\u9519\u8bef\uff0c\u7528\u6cd5\uff1a/\u63d0\u9192\u6dfb\u52a0 10:30 \u5185\u5bb9"
MSG_INVALID_TIME = "\u65f6\u95f4\u683c\u5f0f\u4e0d\u5bf9\uff0c\u8bf7\u4f7f\u7528 24 \u5c0f\u65f6\u5236 HH:MM"
MSG_NO_ORIGIN = "\u83b7\u53d6\u6d88\u606f\u6765\u6e90\u5931\u8d25\uff0c\u5f53\u524d\u73af\u5883\u53ef\u80fd\u4e0d\u652f\u6301\u4e3b\u52a8\u6d88\u606f\u3002"
MSG_ADD_OK = "\u8bbe\u7f6e\u6210\u529f\uff1a\u6bcf\u5929 {time} \u63d0\u9192\u4f60 {content}"
MSG_EMPTY_LIST = "\u4f60\u8fd8\u6ca1\u6709\u8bbe\u7f6e\u4efb\u4f55\u63d0\u9192\u3002"
MSG_LIST_TITLE = "\u4f60\u7684\u63d0\u9192\u5217\u8868\uff1a\n"
MSG_DELETE_USAGE = "\u7528\u6cd5\uff1a/\u63d0\u9192\u5220\u9664 [\u7f16\u53f7]"
MSG_DELETE_NOT_NUMBER = "\u5220\u9664\u5931\u8d25\uff1a\u7f16\u53f7\u5fc5\u987b\u662f\u6570\u5b57\u3002"
MSG_DELETE_NOT_FOUND = "\u5220\u9664\u5931\u8d25\uff1a\u7f16\u53f7\u4e0d\u5b58\u5728\u3002"
MSG_DELETE_NOT_OWNER = "\u5220\u9664\u5931\u8d25\uff1a\u8fd9\u4e2a\u63d0\u9192\u4e0d\u5c5e\u4e8e\u4f60\u3002"
MSG_DELETE_OK = "\u5df2\u5220\u9664 {time} \u7684\u63d0\u9192\u3002"
MSG_TEST_EMPTY = "\u4f60\u6ca1\u6709\u53ef\u6d4b\u8bd5\u7684\u63d0\u9192\u3002"
MSG_TEST_START = "\u5f00\u59cb\u53d1\u9001 {count} \u6761\u6d4b\u8bd5\u63d0\u9192\u3002"
MSG_RECREATE_REQUIRED = "\u8fd9\u6761\u63d0\u9192\u7f3a\u5c11\u53d1\u9001\u6240\u9700\u7684\u4f1a\u8bdd\u4fe1\u606f\uff0c\u8bf7\u5220\u9664\u540e\u91cd\u65b0\u6dfb\u52a0\u3002"


@register(PLUGIN_ID, "yunko1993", PLUGIN_TITLE, PLUGIN_VERSION)
class PersonalReminder(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self._scheduler_synced = False
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._scheduler_retry_task: Optional[asyncio.Task] = None

        self.data_dir = self._resolve_data_dir()
        os.makedirs(self.data_dir, exist_ok=True)

        self.data_file = os.path.join(self.data_dir, DATA_FILE_NAME)
        self.reminders = self._load_data()
        self._capture_loop()
        self._ensure_scheduler_ready()
        self._schedule_scheduler_retry()

        logging.info("DNF reminder: using data file %s", self.data_file)
        logging.info("DNF reminder: loaded %s reminders", len(self.reminders))

    def _candidate_data_dirs(self) -> List[str]:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        base_dirs = []
        cursor = current_dir
        for _ in range(4):
            base_dirs.append(cursor)
            parent = os.path.dirname(cursor)
            if parent == cursor:
                break
            cursor = parent

        candidates: List[str] = []
        seen = set()
        for base_dir in base_dirs:
            for dir_name in [DEFAULT_DATA_DIR_NAME] + LEGACY_DATA_DIR_NAMES:
                path = os.path.join(base_dir, "plugin_data", dir_name)
                if path not in seen:
                    candidates.append(path)
                    seen.add(path)
        return candidates

    def _resolve_data_dir(self) -> str:
        candidates = self._candidate_data_dirs()
        existing_files = []
        for path in candidates:
            file_path = os.path.join(path, DATA_FILE_NAME)
            if os.path.exists(file_path):
                try:
                    mtime = os.path.getmtime(file_path)
                except OSError:
                    mtime = -1
                existing_files.append((mtime, path))

        if existing_files:
            existing_files.sort(reverse=True)
            chosen = existing_files[0][1]
            logging.info("DNF reminder: found existing data dir %s", chosen)
            return chosen

        preferred = candidates[0]
        logging.info("DNF reminder: no existing data file found, default to %s", preferred)
        return preferred

    def _normalize_reminder(self, item: Dict[str, Any]) -> Optional[Dict[str, str]]:
        time_value = item.get("time") or item.get("remind_time")
        content_value = item.get("content") or item.get("message") or item.get("text")
        if not time_value or not content_value:
            return None

        time_text = str(time_value).strip()
        try:
            datetime.strptime(time_text, "%H:%M")
        except ValueError:
            logging.warning("DNF reminder: invalid time format in stored item: %s", item)
            return None

        user_id = item.get("user_id") or item.get("uid") or item.get("sender_id") or ""
        umo = (
            item.get("umo")
            or item.get("unified_msg_origin")
            or item.get("msg_origin")
            or item.get("origin")
        )

        return {
            "user_id": str(user_id),
            "umo": "" if umo is None else str(umo),
            "group_id": str(item.get("group_id", "")),
            "time": time_text,
            "content": str(content_value).strip(),
        }

    def _load_data(self) -> List[Dict[str, str]]:
        if not os.path.exists(self.data_file):
            return []

        try:
            with open(self.data_file, "r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception as exc:
            logging.error("DNF reminder: failed to load data from %s: %s", self.data_file, exc)
            return []

        if not isinstance(data, list):
            logging.error("DNF reminder: %s is not a list", self.data_file)
            return []

        normalized = []
        for item in data:
            if not isinstance(item, dict):
                continue
            normalized_item = self._normalize_reminder(item)
            if normalized_item:
                normalized.append(normalized_item)

        return normalized

    def _save_data(self):
        try:
            with open(self.data_file, "w", encoding="utf-8") as file:
                json.dump(self.reminders, file, ensure_ascii=False, indent=2)
        except Exception as exc:
            logging.error("DNF reminder: failed to save data to %s: %s", self.data_file, exc)
            return

        self._ensure_scheduler_ready(force=True)

    def _get_config_value(self, key: str, default):
        try:
            value = self.config.get(key, default)
        except Exception:
            value = default
        return default if value is None else value

    def _get_group_targets(self) -> List[str]:
        raw_targets = self._get_config_value("group_targets", [])
        if isinstance(raw_targets, str):
            raw_targets = [line.strip() for line in raw_targets.splitlines()]
        if not isinstance(raw_targets, Sequence) or isinstance(raw_targets, (bytes, str)):
            return []

        targets = []
        seen = set()
        for item in raw_targets:
            text = str(item).strip()
            if text and text not in seen:
                targets.append(text)
                seen.add(text)
        return targets

    def _send_to_groups_enabled(self) -> bool:
        return bool(self._get_config_value("send_to_configured_groups", False))

    def _mention_all_enabled(self) -> bool:
        return bool(self._get_config_value("mention_all_on_group", False))

    def _send_private_copy_enabled(self) -> bool:
        return bool(self._get_config_value("send_private_copy", True))

    def _capture_loop(self):
        loop = self._get_runtime_loop()
        if loop:
            self._main_loop = loop

    def _schedule_scheduler_retry(self):
        if self._scheduler_synced:
            return

        loop = self._main_loop or self._get_runtime_loop()
        if not loop:
            logging.warning("DNF reminder: cannot start scheduler retry task without event loop")
            return

        current_task = self._scheduler_retry_task
        if current_task and not current_task.done():
            return

        self._main_loop = loop
        try:
            current_running_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_running_loop = None

        if current_running_loop is loop:
            self._scheduler_retry_task = loop.create_task(self._retry_scheduler_until_ready())
        else:
            asyncio.run_coroutine_threadsafe(self._retry_scheduler_until_ready(), loop)

        logging.info("DNF reminder: scheduler retry task started")

    async def _retry_scheduler_until_ready(self):
        while not self._scheduler_synced:
            scheduler = self._get_scheduler()
            if scheduler:
                self._refresh_scheduler(scheduler)
                self._scheduler_synced = True
                logging.info("DNF reminder: scheduler became ready during background retry")
                break

            await asyncio.sleep(5)

        self._scheduler_retry_task = None

    def _get_runtime_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        candidates = []
        if hasattr(self.context, "get_event_loop"):
            candidates.append(("context.get_event_loop", self.context.get_event_loop))

        runtime = getattr(self.context, "runtime", None)
        if runtime:
            runtime_loop = getattr(runtime, "loop", None)
            if runtime_loop:
                return runtime_loop

        context_loop = getattr(self.context, "loop", None)
        if context_loop:
            return context_loop

        for label, getter in candidates:
            try:
                loop = getter()
                if loop:
                    return loop
            except Exception as exc:
                logging.warning("DNF reminder: failed to get loop from %s: %s", label, exc)

        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def _get_scheduler(self):
        if hasattr(self.context, "get_scheduler"):
            try:
                scheduler = self.context.get_scheduler()
                if scheduler:
                    return scheduler
            except Exception as exc:
                logging.warning("DNF reminder: failed to get scheduler from context: %s", exc)

        runtime = getattr(self.context, "runtime", None)
        return getattr(runtime, "scheduler", None)

    def _ensure_scheduler_ready(self, force: bool = False):
        self._capture_loop()
        scheduler = self._get_scheduler()
        if not scheduler:
            if force or not self._scheduler_synced:
                logging.warning(
                    "DNF reminder: scheduler is not ready yet, will retry when plugin is used again"
                )
            self._scheduler_synced = False
            self._schedule_scheduler_retry()
            return

        if self._scheduler_synced and not force:
            return

        self._refresh_scheduler(scheduler)
        self._scheduler_synced = True
        self._scheduler_retry_task = None

    def _refresh_scheduler(self, scheduler=None):
        scheduler = scheduler or self._get_scheduler()
        if not scheduler:
            self._scheduler_synced = False
            return

        removed_count = 0
        try:
            for job in scheduler.get_jobs():
                job_id = getattr(job, "id", "")
                if isinstance(job_id, str) and job_id.startswith(f"{PLUGIN_ID}_"):
                    scheduler.remove_job(job_id)
                    removed_count += 1
        except Exception as exc:
            logging.error("DNF reminder: failed to clear old scheduler jobs: %s", exc)

        registered_count = 0
        for idx, item in enumerate(self.reminders):
            try:
                hour_text, minute_text = item["time"].split(":")
                scheduler.add_job(
                    self._scheduled_job_entry,
                    "cron",
                    hour=int(hour_text),
                    minute=int(minute_text),
                    args=[idx],
                    id=f"{PLUGIN_ID}_{idx}",
                    replace_existing=True,
                )
                registered_count += 1
            except Exception as exc:
                logging.error("DNF reminder: failed to register job %s: %s", item, exc)

        logging.info(
            "DNF reminder: scheduler refreshed, removed=%s registered=%s",
            removed_count,
            registered_count,
        )

    def _scheduled_job_entry(self, reminder_index: int):
        if reminder_index < 0 or reminder_index >= len(self.reminders):
            logging.warning("DNF reminder: invalid reminder index %s", reminder_index)
            return

        item = self.reminders[reminder_index]
        loop = self._main_loop or self._get_runtime_loop()
        if not loop:
            logging.error("DNF reminder: no event loop available for scheduled job")
            return

        self._main_loop = loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        try:
            if running_loop is loop:
                loop.create_task(self._send_private_notification(item))
            else:
                asyncio.run_coroutine_threadsafe(self._send_private_notification(item), loop)
            logging.info(
                "DNF reminder: scheduled job dispatched for user_id=%s time=%s on thread=%s",
                item.get("user_id"),
                item.get("time"),
                threading.current_thread().name,
            )
        except Exception as exc:
            logging.error("DNF reminder: failed to dispatch scheduled job: %s", exc)

    def _get_event_group_id(self, event: AstrMessageEvent) -> str:
        message_obj = getattr(event, "message_obj", None)
        group_id = getattr(message_obj, "group_id", "") if message_obj else ""
        return str(group_id or "")

    def _build_message_text(self, item: Dict[str, str]) -> str:
        msg_text = (
            "\u0044\u004e\u0046 \u79c1\u4eba\u63d0\u9192\n"
            "--------------------\n"
            f"\u5185\u5bb9\uff1a{item['content']}\n"
            f"\u65f6\u95f4\uff1a{item['time']}\n"
            "--------------------\n"
            "\u8bb0\u5f97\u9886\u53d6\u3002"
        )
        return msg_text

    def _create_group_chain(self, msg_text: str):
        parts = []
        if self._mention_all_enabled():
            try:
                import astrbot.api.message_components as Comp

                at_all_cls = getattr(Comp, "AtAll", None)
                if at_all_cls:
                    parts.append(at_all_cls())
                    parts.append(Comp.Plain(text="\n"))
                else:
                    at_cls = getattr(Comp, "At", None)
                    if at_cls:
                        parts.append(at_cls(qq="all"))
                        parts.append(Comp.Plain(text="\n"))
                    else:
                        parts.append(Plain("@\u5168\u4f53\u6210\u5458\n"))
            except Exception as exc:
                logging.warning("DNF reminder: failed to create @all component: %s", exc)
                parts.append(Plain("@\u5168\u4f53\u6210\u5458\n"))

        parts.append(Plain(msg_text))
        return parts

    def _get_notification_targets(self, item: Dict[str, str]) -> List[Dict[str, str]]:
        targets: List[Dict[str, str]] = []
        seen = set()

        private_umo = str(item.get("umo", "")).strip()
        if private_umo and self._send_private_copy_enabled():
            seen.add(private_umo)
            targets.append({"umo": private_umo, "kind": "private"})

        if self._send_to_groups_enabled():
            for target in self._get_group_targets():
                if target in seen:
                    continue
                seen.add(target)
                targets.append({"umo": target, "kind": "group"})

        return targets

    async def _send_private_notification(self, item: Dict[str, str]):
        targets = self._get_notification_targets(item)
        if not targets:
            logging.error("DNF reminder: no valid notification targets configured. item=%s", item)
            return

        msg_text = self._build_message_text(item)
        delivered = 0
        for target in targets:
            umo = target["umo"]
            chain_payload = self._create_group_chain(msg_text) if target["kind"] == "group" else None
            logging.info("DNF reminder: sending message to umo=%s kind=%s", umo, target["kind"])

            try:
                if chain_payload is None:
                    from astrbot.api.event import MessageChain

                    chain = MessageChain().message(msg_text)
                    await self.context.send_message(umo, chain)
                else:
                    await self.context.send_message(umo, chain_payload)
                delivered += 1
                logging.info("DNF reminder: send succeeded to %s", umo)
                continue
            except Exception as exc:
                logging.warning("DNF reminder: primary send failed to %s: %s", umo, exc)

            try:
                await self.context.send_message(umo, [Plain(msg_text)])
                delivered += 1
                logging.info("DNF reminder: plain fallback send succeeded to %s", umo)
            except Exception as exc:
                logging.error("DNF reminder: all send methods failed for %s: %s", umo, exc)

        if delivered <= 0:
            logging.error("DNF reminder: notification delivery failed for all targets. item=%s", item)

    def _get_user_id(self, event: AstrMessageEvent) -> str:
        try:
            return str(event.get_sender_id())
        except Exception:
            return str(event.message_obj.sender.user_id)

    def _get_umo(self, event: AstrMessageEvent) -> Optional[str]:
        for attr in ("unified_msg_origin", "msg_origin", "origin"):
            value = getattr(event, attr, None)
            if value:
                return str(value)

        try:
            session = event.get_session_id()
            if session:
                return str(session)
        except Exception:
            pass

        return None

    @command(CMD_ADD)
    async def add(self, event: AstrMessageEvent):
        self._ensure_scheduler_ready()

        raw_msg = event.message_str.strip()
        parts = raw_msg.split()
        if len(parts) < 3:
            yield event.plain_result(MSG_INVALID_FORMAT)
            return

        time_str = parts[1]
        content = " ".join(parts[2:]).strip()
        try:
            datetime.strptime(time_str, "%H:%M")
        except ValueError:
            yield event.plain_result(MSG_INVALID_TIME)
            return

        umo = self._get_umo(event)
        if not umo:
            yield event.plain_result(MSG_NO_ORIGIN)
            return

        self.reminders.append(
            {
                "user_id": self._get_user_id(event),
                "umo": umo,
                "group_id": self._get_event_group_id(event),
                "time": time_str,
                "content": content,
            }
        )
        self._save_data()
        yield event.plain_result(MSG_ADD_OK.format(time=time_str, content=content))

    @command(CMD_LIST)
    async def list_reminders(self, event: AstrMessageEvent):
        self._ensure_scheduler_ready()

        user_id = self._get_user_id(event)
        my_items = [
            f"[{index}] {item['time']} - {item['content']}"
            for index, item in enumerate(self.reminders)
            if str(item.get("user_id")) == user_id
        ]
        if not my_items:
            yield event.plain_result(MSG_EMPTY_LIST)
            return

        yield event.plain_result(MSG_LIST_TITLE + "\n".join(my_items))

    @command(CMD_DELETE)
    async def delete(self, event: AstrMessageEvent):
        self._ensure_scheduler_ready()

        raw_msg = event.message_str.strip()
        parts = raw_msg.split()
        if len(parts) < 2:
            yield event.plain_result(MSG_DELETE_USAGE)
            return

        try:
            index = int(parts[1])
        except ValueError:
            yield event.plain_result(MSG_DELETE_NOT_NUMBER)
            return

        user_id = self._get_user_id(event)
        if not (0 <= index < len(self.reminders)):
            yield event.plain_result(MSG_DELETE_NOT_FOUND)
            return

        if str(self.reminders[index].get("user_id")) != user_id:
            yield event.plain_result(MSG_DELETE_NOT_OWNER)
            return

        removed = self.reminders.pop(index)
        self._save_data()
        yield event.plain_result(MSG_DELETE_OK.format(time=removed["time"]))

    @command(CMD_TEST)
    async def test(self, event: AstrMessageEvent):
        self._ensure_scheduler_ready(force=True)

        user_id = self._get_user_id(event)
        my_items = [item for item in self.reminders if str(item.get("user_id")) == user_id]
        if not my_items:
            yield event.plain_result(MSG_TEST_EMPTY)
            return

        yield event.plain_result(MSG_TEST_START.format(count=len(my_items)))
        for item in my_items:
            if not item.get("umo"):
                await event.send(event.plain_result(MSG_RECREATE_REQUIRED))
                continue
            await self._send_private_notification(item)
            await asyncio.sleep(0.5)
