import logging
import json
import os
import asyncio
from datetime import datetime
from astrbot.api.all import *

@register("dnf_personal_reminder", "yunko1993", "DNF私人提醒秘书", "1.4.0")
class PersonalReminder(Star):
    def __init__(self, context: Context):
            super().__init__(context)
            
            # 1. 设置文件夹名称（改成和你截图里的插件文件夹同名）
            self.plugin_name = "astrbot_plugin_dnf_reminder"
            
            # 2. 完美的路径解析逻辑
            # __file__ 当前在 data/plugins/astrbot_plugin_dnf_reminder/main.py
            current_dir = os.path.dirname(os.path.abspath(__file__))
            # 退两级到达 data/ 目录
            data_base_dir = os.path.dirname(os.path.dirname(current_dir))
            # 精准拼接到你截图里的目标位置：data/plugin_data/astrbot_plugin_dnf_reminder
            self.data_dir = os.path.join(data_base_dir, "plugin_data", self.plugin_name)
            
            if not os.path.exists(self.data_dir):
                os.makedirs(self.data_dir)
                
            self.data_file = os.path.join(self.data_dir, "reminders.json")
            self.reminders = self._load_data()
            self._refresh_scheduler()

    def _load_data(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return []
        return[]

    def _save_data(self):
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=4)
            self._refresh_scheduler()
        except Exception as e:
            logging.error(f"DNF提醒保存失败: {e}")

    def _get_scheduler(self):
        if hasattr(self.context, 'get_scheduler'):
            return self.context.get_scheduler()
        if hasattr(self.context, 'runtime') and hasattr(self.context.runtime, 'scheduler'):
            return self.context.runtime.scheduler
        return None

    def _refresh_scheduler(self):
        scheduler = self._get_scheduler()
        if not scheduler: return

        for job in scheduler.get_jobs():
            if job.id.startswith(f"{self.plugin_name}_"):
                scheduler.remove_job(job.id)

        for idx, item in enumerate(self.reminders):
            try:
                h, m = item['time'].split(':')
                scheduler.add_job(
                    self._send_private_notification,
                    "cron", hour=h, minute=m,
                    args=[item],
                    id=f"{self.plugin_name}_{idx}",
                    replace_existing=True
                )
            except: pass

    # ================= 核心修复：遵循最新官方 API 发送主动消息 =================
    async def _send_private_notification(self, item):
        msg_text = f"🔔 【私人秘书提醒】\n--------------------\n内容：{item['content']}\n时间：{item['time']}\n--------------------\n👉 记得领取哦！"
        
        # 提取存储的统一消息来源符 (umo)
        umo = item.get('umo')
        if not umo:
            logging.error("DNF提醒: 致命错误 - 任务缺少 unified_msg_origin！请删除旧任务并重新添加。")
            return
            
        logging.info(f"DNF提醒: 正在通过统一标识 [{umo}] 发送消息...")
        
        try:
            # 官方推荐的发送语法
            from astrbot.api.event import MessageChain
            chain = MessageChain().message(msg_text)
            await self.context.send_message(umo, chain)
            logging.info("DNF提醒: 已成功发送主动消息。")
        except Exception as e:
            logging.error(f"DNF提醒: 标准方法发送失败: {e}")
            try:
                # 备用降级发送方案
                await self.context.send_message(umo, [Plain(msg_text)])
                logging.info("DNF提醒: 已通过备用 Component 列表发送成功。")
            except Exception as e2:
                logging.error(f"DNF提醒: 备用发送方案也失败: {e2}")

    # ================= 指令区 =================
    
    @command("提醒添加")
    async def add(self, event: AstrMessageEvent):
        '''用法: /提醒添加 10:30 内容'''
        raw_msg = event.message_str.strip()
        parts = raw_msg.split()
        if len(parts) < 3:
            yield event.plain_result("❌ 格式错误！格式: /提醒添加 10:30 内容")
            return
            
        time_str = parts[1]
        content = " ".join(parts[2:])
        try:
            datetime.strptime(time_str, "%H:%M")
        except:
            yield event.plain_result("❌ 时间格式不对，请使用 24小时制 HH:MM")
            return

        try:
            user_id = str(event.get_sender_id())
        except:
            user_id = str(event.message_obj.sender.user_id)
            
        # --- 核心数据获取：存储当前会话的 UMO ---
        try:
            umo = event.unified_msg_origin
        except:
            yield event.plain_result("❌ 获取消息来源标识失败，当前环境可能不支持主动消息。")
            return

        self.reminders.append({
            "user_id": user_id, 
            "umo": umo,             # 保存了它，机器人就知道该往哪里发消息了
            "time": time_str, 
            "content": content
        })
        self._save_data()
        yield event.plain_result(f"✅ 设置成功！每天 {time_str} 我会准时提醒你。")

    @command("提醒列表")
    async def list_reminders(self, event: AstrMessageEvent):
        try:
            user_id = str(event.get_sender_id())
        except:
            user_id = str(event.message_obj.sender.user_id)
            
        my_items = [f"[{i}] {r['time']} - {r['content']}" for i, r in enumerate(self.reminders) if str(r['user_id']) == user_id]
        if not my_items:
            yield event.plain_result("你还没有设置任何提醒。")
        else:
            yield event.plain_result("📅 你的提醒清单：\n" + "\n".join(my_items))

    @command("提醒删除")
    async def delete(self, event: AstrMessageEvent):
        raw_msg = event.message_str.strip()
        parts = raw_msg.split()
        if len(parts) < 2:
            yield event.plain_result("用法: /提醒删除 [编号]")
            return
        try:
            index = int(parts[1])
            try:
                user_id = str(event.get_sender_id())
            except:
                user_id = str(event.message_obj.sender.user_id)
                
            if 0 <= index < len(self.reminders) and str(self.reminders[index]['user_id']) == user_id:
                removed = self.reminders.pop(index)
                self._save_data()
                yield event.plain_result(f"🗑 已删除 {removed['time']} 的提醒。")
            else:
                yield event.plain_result("❌ 编号无效或该任务不属于你。")
        except:
            yield event.plain_result("❌ 删除失败。")

    @command("提醒测试")
    async def test(self, event: AstrMessageEvent):
        try:
            user_id = str(event.get_sender_id())
        except:
            user_id = str(event.message_obj.sender.user_id)
            
        my_items = [r for r in self.reminders if str(r['user_id']) == user_id]
        if not my_items:
            yield event.plain_result("你没有设置任务，无法测试。")
            return
            
        yield event.plain_result(f"🚀 正在发送 {len(my_items)} 条测试消息...")
        for item in my_items:
            await self._send_private_notification(item)
            await asyncio.sleep(0.5)
