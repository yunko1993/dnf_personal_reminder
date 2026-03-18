import logging
import json
import os
import asyncio
from datetime import datetime
from astrbot.api.all import *

@register("dnf_personal_reminder", "yunko1993", "DNF私人提醒秘书", "1.2.3")
class PersonalReminder(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        
        # 1. 设置数据存放路径
        self.plugin_name = "dnf_personal_reminder"
        # 定位到 data/plugin_data/dnf_personal_reminder/
        # 这里的路径计算：main.py -> 插件文件夹 -> plugins文件夹 -> AstrBot根目录
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        self.data_dir = os.path.join(base_dir, "data", "plugin_data", self.plugin_name)
        
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
            
        self.data_file = os.path.join(self.data_dir, "reminders.json")
        self.reminders = self._load_data()

        # 2. 初始化时即加载调度任务，兼容性最高
        try:
            self._refresh_scheduler()
            logging.info("DNF私人提醒插件初始化完成，定时任务已同步。")
        except Exception as e:
            logging.error(f"插件任务初始化失败: {e}")

    def _load_data(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"加载数据失败: {e}")
                return []
        return []

    def _save_data(self):
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=4)
            self._refresh_scheduler()
        except Exception as e:
            logging.error(f"保存数据失败: {e}")

    def _refresh_scheduler(self):
        """刷新定时任务列表"""
        scheduler = self.context.get_scheduler()
        # 移除该插件旧的定时任务
        for job in scheduler.get_jobs():
            if job.id.startswith(f"{self.plugin_name}_"):
                scheduler.remove_job(job.id)

        # 重新注册任务
        for idx, item in enumerate(self.reminders):
            try:
                h, m = item['time'].split(':')
                scheduler.add_job(
                    self._send_private_notification,
                    "cron",
                    hour=h,
                    minute=m,
                    args=[item],
                    id=f"{self.plugin_name}_{idx}",
                    replace_existing=True
                )
            except Exception as e:
                logging.error(f"注册任务 {item['content']} 失败: {e}")

    async def _send_private_notification(self, item):
        """发送私聊提醒"""
        msg = f"🔔 【私人秘书提醒】\n--------------------\n内容：{item['content']}\n时间：{item['time']}\n--------------------\n别忘了去领取哦！"
        try:
            # 始终发送私聊消息给设置该任务的用户
            await self.context.send_private_message(item['user_id'], [Plain(msg)])
        except Exception as e:
            logging.error(f"提醒发送失败: {e}")

    @command("提醒")
    async def reminder_manager(self, event: AstrMessageEvent):
        '''私人提醒管理助手'''
        pass

    @reminder_manager.group("添加")
    async def add(self, event: AstrMessageEvent, time_str: str, *, content: str):
        '''/提醒 添加 10:30 领心悦增幅器'''
        try:
            datetime.strptime(time_str, "%H:%M")
        except:
            yield CommandResult().error("时间格式错误！请使用 HH:MM (如 09:30)")
            return

        user_id = event.message_obj.user_id
        
        new_item = {
            "user_id": user_id,
            "time": time_str,
            "content": content
        }
        
        self.reminders.append(new_item)
        self._save_data()
        
        yield CommandResult().success(f"✅ 设置成功！每天 {time_str} 我会私聊提醒你。")

    @reminder_manager.group("列表")
    async def list_reminders(self, event: AstrMessageEvent):
        '''查看我设置的所有提醒'''
        user_id = event.message_obj.user_id
        my_items = [f"[{i}] {r['time']} - {r['content']}" for i, r in enumerate(self.reminders) if r['user_id'] == user_id]
        
        if not my_items:
            yield CommandResult().success("你当前没有任何私人提醒。")
        else:
            yield CommandResult().success("📅 你的提醒清单：\n" + "\n".join(my_items))

    @reminder_manager.group("删除")
    async def delete(self, event: AstrMessageEvent, index: int):
        '''根据编号删除提醒'''
        user_id = event.message_obj.user_id
        try:
            if 0 <= index < len(self.reminders) and self.reminders[index]['user_id'] == user_id:
                removed = self.reminders.pop(index)
                self._save_data()
                yield CommandResult().success(f"🗑 已删除：{removed['time']} {removed['content']}")
            else:
                yield CommandResult().error("删除失败：编号无效或无权限。")
        except:
            yield CommandResult().error("操作发生错误。")

    @reminder_manager.group("立即测试")
    async def test(self, event: AstrMessageEvent):
        '''立即触发我的测试提醒'''
        user_id = event.message_obj.user_id
        my_items = [r for r in self.reminders if r['user_id'] == user_id]
        if not my_items:
            yield CommandResult().error("你还没有设置任何任务。")
            return
            
        yield CommandResult().success("测试消息已发出，请注意查看私聊。")
        for item in my_items:
            await self._send_private_notification(item)
            await asyncio.sleep(0.5)
