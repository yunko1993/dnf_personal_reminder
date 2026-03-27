import asyncio
import json
import logging
import os
from datetime import datetime

from astrbot.api.all import *


PLUGIN_ID = "dnf_personal_reminder"
PLUGIN_DATA_DIR = "astrbot_plugin_dnf_reminder"
PLUGIN_TITLE = "DNF 私人提醒秘书"

CMD_ADD = "提醒添加"
CMD_LIST = "提醒列表"
CMD_DELETE = "提醒删除"
CMD_TEST = "提醒测试"


@register(PLUGIN_ID, "yunko1993", PLUGIN_TITLE, "1.4.1")
class PersonalReminder(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.plugin_name = PLUGIN_DATA_DIR
        self._scheduler_synced = False

        current_dir = os.path.dirname(os.path.abspath(__file__))
        data_base_dir = os.path.dirname(os.path.dirname(current_dir))
        self.data_dir = os.path.join(data_base_dir, "plugin_data", self.plugin_name)
        os.makedirs(self.data_dir, exist_ok=True)

        self.data_file = os.path.join(self.data_dir, "reminders.json")
        self.reminders = self._load_data()
        self._ensure_scheduler_ready()

    def _load_data(self):
        if not os.path.exists(self.data_file):
            return []

        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logging.error("DNF reminder: failed to load data: %s", e)
            return []

        if not isinstance(data, list):
            logging.error("DNF reminder: reminders.json is not a list.")
            return []

        normalized = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if "time" not in item or "content" not in item:
                continue
            normalized.append(
                {
                    "user_id": str(item.get("user_id", "")),
                    "umo": item.get("umo"),
                    "time": str(item["time"]),
                    "content": str(item["content"]),
                }
            )
        return normalized

    def _save_data(self):
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logging.error("DNF reminder: failed to save data: %s", e)
            return

        self._ensure_scheduler_ready(force=True)

    def _get_scheduler(self):
        if hasattr(self.context, "get_scheduler"):
            try:
                return self.context.get_scheduler()
            except Exception as e:
                logging.error("DNF reminder: failed to get scheduler: %s", e)

        runtime = getattr(self.context, "runtime", None)
        return getattr(runtime, "scheduler", None)

    def _ensure_scheduler_ready(self, force=False):
        scheduler = self._get_scheduler()
        if not scheduler:
            if force or not self._scheduler_synced:
                logging.warning(
                    "DNF reminder: scheduler is not ready yet, will retry when the plugin is used again."
                )
            self._scheduler_synced = False
            return

        if self._scheduler_synced and not force:
            return

        self._refresh_scheduler(scheduler)
        self._scheduler_synced = True

    def _refresh_scheduler(self, scheduler=None):
        scheduler = scheduler or self._get_scheduler()
        if not scheduler:
            self._scheduler_synced = False
            return

        for job in scheduler.get_jobs():
            job_id = getattr(job, "id", "")
            if isinstance(job_id, str) and job_id.startswith(f"{PLUGIN_ID}_"):
                scheduler.remove_job(job_id)

        for idx, item in enumerate(self.reminders):
            try:
                hour_text, minute_text = item["time"].split(":")
                hour = int(hour_text)
                minute = int(minute_text)
                scheduler.add_job(
                    self._scheduled_job_entry,
                    "cron",
                    hour=hour,
                    minute=minute,
                    args=[idx],
                    id=f"{PLUGIN_ID}_{idx}",
                    replace_existing=True,
                )
            except Exception as e:
                logging.error("DNF reminder: failed to register job %s: %s", item, e)

    def _scheduled_job_entry(self, reminder_index: int):
        if reminder_index < 0 or reminder_index >= len(self.reminders):
            logging.warning("DNF reminder: invalid reminder index: %s", reminder_index)
            return

        item = self.reminders[reminder_index]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logging.error("DNF reminder: no running event loop available for scheduled job.")
            return

        loop.create_task(self._send_private_notification(item))

    async def _send_private_notification(self, item):
        umo = item.get("umo")
        if not umo:
            logging.error(
                "DNF reminder: reminder has no unified_msg_origin, please recreate it. item=%s",
                item,
            )
            return

        msg_text = (
            "DNF 私人提醒\n"
            "--------------------\n"
            f"内容：{item['content']}\n"
            f"时间：{item['time']}\n"
            "--------------------\n"
            "记得领取。"
        )
        logging.info("DNF reminder: sending message to umo=%s", umo)

        try:
            from astrbot.api.event import MessageChain

            chain = MessageChain().message(msg_text)
            await self.context.send_message(umo, chain)
            logging.info("DNF reminder: MessageChain send succeeded.")
            return
        except Exception as e:
            logging.warning("DNF reminder: MessageChain send failed: %s", e)

        try:
            await self.context.send_message(umo, [Plain(msg_text)])
            logging.info("DNF reminder: Plain send succeeded.")
        except Exception as e:
            logging.error("DNF reminder: all send methods failed: %s", e)

    def _get_user_id(self, event: AstrMessageEvent):
        try:
            return str(event.get_sender_id())
        except Exception:
            return str(event.message_obj.sender.user_id)

    def _get_umo(self, event: AstrMessageEvent):
        try:
            return event.unified_msg_origin
        except Exception:
            return None

    @command(CMD_ADD)
    async def add(self, event: AstrMessageEvent):
        self._ensure_scheduler_ready()

        raw_msg = event.message_str.strip()
        parts = raw_msg.split()
        if len(parts) < 3:
            yield event.plain_result("格式错误，用法：/提醒添加 10:30 内容")
            return

        time_str = parts[1]
        content = " ".join(parts[2:])
        try:
            datetime.strptime(time_str, "%H:%M")
        except ValueError:
            yield event.plain_result("时间格式不对，请使用 24 小时制 HH:MM")
            return

        umo = self._get_umo(event)
        if not umo:
            yield event.plain_result("获取消息来源失败，当前环境可能不支持主动消息。")
            return

        self.reminders.append(
            {
                "user_id": self._get_user_id(event),
                "umo": umo,
                "time": time_str,
                "content": content,
            }
        )
        self._save_data()
        yield event.plain_result(f"设置成功：每天 {time_str} 提醒你 {content}")

    @command(CMD_LIST)
    async def list_reminders(self, event: AstrMessageEvent):
        self._ensure_scheduler_ready()

        user_id = self._get_user_id(event)
        my_items = [
            f"[{i}] {item['time']} - {item['content']}"
            for i, item in enumerate(self.reminders)
            if str(item.get("user_id")) == user_id
        ]
        if not my_items:
            yield event.plain_result("你还没有设置任何提醒。")
            return

        yield event.plain_result("你的提醒列表：\n" + "\n".join(my_items))

    @command(CMD_DELETE)
    async def delete(self, event: AstrMessageEvent):
        self._ensure_scheduler_ready()

        raw_msg = event.message_str.strip()
        parts = raw_msg.split()
        if len(parts) < 2:
            yield event.plain_result("用法：/提醒删除 [编号]")
            return

        try:
            index = int(parts[1])
        except ValueError:
            yield event.plain_result("删除失败：编号必须是数字。")
            return

        user_id = self._get_user_id(event)
        if not (0 <= index < len(self.reminders)):
            yield event.plain_result("删除失败：编号不存在。")
            return

        if str(self.reminders[index].get("user_id")) != user_id:
            yield event.plain_result("删除失败：这个提醒不属于你。")
            return

        removed = self.reminders.pop(index)
        self._save_data()
        yield event.plain_result(f"已删除 {removed['time']} 的提醒。")

    @command(CMD_TEST)
    async def test(self, event: AstrMessageEvent):
        self._ensure_scheduler_ready()

        user_id = self._get_user_id(event)
        my_items = [item for item in self.reminders if str(item.get("user_id")) == user_id]
        if not my_items:
            yield event.plain_result("你没有可测试的提醒。")
            return

        yield event.plain_result(f"开始发送 {len(my_items)} 条测试提醒。")
        for item in my_items:
            await self._send_private_notification(item)
            await asyncio.sleep(0.5)
