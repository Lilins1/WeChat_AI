"""
消息处理模块
负责处理聊天消息，包括:
- 消息队列管理
- 消息分发处理
- API响应处理
- 多媒体消息处理
"""

import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional
from openai import OpenAI
from wxauto import WeChat
from services.database import Session, ChatMessage
import random
import os
from services.ai.deepseek import DeepSeekAI
from handlers.memory import MemoryHandler
from config import config
import re

logger = logging.getLogger(__name__)

class MessageHandler:
    def __init__(self, root_dir, api_key, base_url, model, max_token, temperature, 
                 max_groups, robot_name, prompt_content, image_handler, emoji_handler, voice_handler, memory_handler):
        self.root_dir = root_dir
        self.api_key = api_key
        self.model = model
        self.max_token = max_token
        self.temperature = temperature
        self.max_groups = max_groups
        self.robot_name = robot_name
        self.prompt_content = prompt_content
        
        # 使用 DeepSeekAI 替换直接的 OpenAI 客户端
        self.deepseek = DeepSeekAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_token=max_token,
            temperature=temperature,
            max_groups=max_groups
        )
        
        # 消息队列相关
        self.user_queues = {}
        self.queue_lock = threading.Lock()
        self.chat_contexts = {}
        
        # 微信实例
        self.wx = WeChat()

        # 添加 handlers
        self.image_handler = image_handler
        self.emoji_handler = emoji_handler
        self.voice_handler = voice_handler
        self.memory_handler = memory_handler

    def save_message(self, sender_id: str, sender_name: str, message: str, reply: str):
        """保存聊天记录到数据库和短期记忆"""
        try:
            session = Session()
            chat_message = ChatMessage(
                sender_id=sender_id,
                sender_name=sender_name,
                message=message,
                reply=reply
            )
            session.add(chat_message)
            session.commit()
            session.close()
            # 新增短期记忆保存
            self.memory_handler.add_short_memory(message, reply)
        except Exception as e:
            print(f"保存消息失败: {str(e)}")

    def send_split_messages(self,text, chat_id):
        firstApply = True
        """通用消息分割发送函数"""
        if '#&#' in text:
            parts = [p.strip() for p in text.split('#&#') if p.strip()]
            for part in parts:
                self.wx.SendMsg(msg=part, who=chat_id)
                if not firstApply:
                    time.sleep(random.randint(0, 2))
                else:
                    firstApply = False
        elif text.strip():  # 非空内容才发送
            self.wx.SendMsg(msg=text.strip(), who=chat_id)

    def get_api_response(self, message: str, user_id: str) -> str:
        """获取 API 回复（含记忆增强）"""
        avatar_dir = os.path.join(self.root_dir, config.behavior.context.avatar_dir)
        prompt_path = os.path.join(avatar_dir, "avatar.md")
        original_content = ""

        try:
            # 步骤1：读取原始提示内容
            with open(prompt_path, "r", encoding="utf-8") as f:
                original_content = f.read()
                logger.debug(f"原始提示文件大小: {len(original_content)} bytes")

            # 步骤2：获取相关记忆并构造临时提示
            relevant_memories = self.memory_handler.get_relevant_memories(message)
            memory_prompt = "\n# 动态记忆注入\n" + "\n".join(relevant_memories) if relevant_memories else ""
            logger.debug(f"注入记忆条数: {len(relevant_memories)}")

            # 步骤3：写入临时记忆
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(f"{original_content}\n{memory_prompt}")

            # 步骤4：确保文件内容已刷新
            with open(prompt_path, "r", encoding="utf-8") as f:
                full_prompt = f.read()
                logger.debug(f"临时提示内容样例:\n{full_prompt[:200]}...")  # 显示前200字符

            # 调用API
            return self.deepseek.get_response(message, user_id, full_prompt)

        except Exception as e:
            logger.error(f"动态记忆注入失败: {str(e)}")
            return self.deepseek.get_response(message, user_id, original_content)  # 降级处理

        finally:
            # 步骤5：恢复原始内容（无论是否出错）
            try:
                with open(prompt_path, "w", encoding="utf-8") as f:
                    f.write(original_content)
            except Exception as restore_error:
                logger.error(f"恢复提示文件失败: {str(restore_error)}")

    def process_messages(self, chat_id: str):
        
        """处理消息队列中的消息"""
        with self.queue_lock:
            if chat_id not in self.user_queues:
                return
            user_data = self.user_queues.pop(chat_id)
            messages = user_data['messages']
            sender_name = user_data['sender_name']
            username = user_data['username']
            is_group = user_data.get('is_group', False)

        messages = messages[-5:]
        merged_message = ' \\ '.join(messages)
        print("\n" + "="*50)
        print(f"收到消息 - 发送者: {sender_name}")
        print(f"消息内容: {merged_message}")
        print("-"*50)

        try:
            # 检查消息是否包含图片识别结果
            is_image_recognition = any("发送了图片：" in msg or "发送了表情包：" in msg for msg in messages)
            if is_image_recognition:
                print("消息类型: 图片识别结果")
            
            # 检查是否为语音请求
            if self.voice_handler.is_voice_request(merged_message):
                logger.info("检测到语音请求")
                reply = self.get_api_response(merged_message, chat_id)
                if "</think>" in reply:
                    reply = reply.split("</think>", 1)[1].strip()
                
                voice_path = self.voice_handler.generate_voice(reply)
                if voice_path:
                    try:
                        self.wx.SendFiles(filepath=voice_path, who=chat_id)
                    except Exception as e:
                        logger.error(f"发送语音失败: {str(e)}")
                        if is_group:
                            reply = f"@{sender_name} {reply}"
                        self.wx.SendMsg(msg=reply, who=chat_id)
                    finally:
                        try:
                            os.remove(voice_path)
                        except Exception as e:
                            logger.error(f"删除临时语音文件失败: {str(e)}")
                else:
                    if is_group:
                        reply = f"@{sender_name} {reply}"
                    self.wx.SendMsg(msg=reply, who=chat_id)
                
                # 异步保存消息记录
                threading.Thread(target=self.save_message, 
                               args=(username, sender_name, merged_message, reply)).start()
                return

            # 检查是否为随机图片请求
            elif self.image_handler.is_random_image_request(merged_message):
                logger.info("检测到随机图片请求")
                image_path = self.image_handler.get_random_image()
                if image_path:
                    try:
                        self.wx.SendFiles(filepath=image_path, who=chat_id)
                        reply = "给主人你找了一张好看的图片哦~"
                    except Exception as e:
                        logger.error(f"发送图片失败: {str(e)}")
                        reply = "抱歉主人，图片发送失败了..."
                    finally:
                        try:
                            if os.path.exists(image_path):
                                os.remove(image_path)
                        except Exception as e:
                            logger.error(f"删除临时图片失败: {str(e)}")
                    
                    if is_group:
                        reply = f"@{sender_name} {reply}"
                    self.wx.SendMsg(msg=reply, who=chat_id)
                    return

            # 检查是否为图像生成请求，但跳过图片识别结果
            elif not is_image_recognition and self.image_handler.is_image_generation_request(merged_message):
                logger.info("检测到画图请求")
                image_path = self.image_handler.generate_image(merged_message)
                if image_path:
                    try:
                        self.wx.SendFiles(filepath=image_path, who=chat_id)
                        reply = "这是按照您的要求生成的图片\\(^o^)/~"
                    except Exception as e:
                        logger.error(f"发送生成图片失败: {str(e)}")
                        reply = "抱歉，图片生成失败了..."
                    finally:
                        try:
                            if os.path.exists(image_path):
                                os.remove(image_path)
                        except Exception as e:
                            logger.error(f"删除临时图片失败: {str(e)}")
                    
                    if is_group:
                        reply = f"@{sender_name} {reply}"
                    self.wx.SendMsg(msg=reply, who=chat_id)
                    return

            # 处理普通文本回复
            else:
                logger.info("处理普通文本回复")
                reply = self.get_api_response(merged_message, chat_id)
                if "</think>" in reply:
                    think_content, reply = reply.split("</think>", 1)
                    print("\n思考过程:")
                    print(think_content.strip())
                    print("\nAI回复:")
                    print(reply.strip())
                else:
                    print("\nAI回复:")
                    print(reply)
                
                if is_group:
                    reply = f"@{sender_name} {reply}"

                self.send_split_messages(reply, chat_id)

                # 发送文本回复
                # if '#&#' in reply:
                #     matches = re.findall(r'#&#\s*(.*?)\s*#&#', reply, flags=re.DOTALL)
                #     if matches:
                #         # 发送##块内容
                #         for content in matches:
                #             if content.strip():
                #                 msg=content.strip()
                #                 print('mession content:',msg)
                #                 self.wx.SendMsg(msg, who=chat_id)
                #                 time.sleep(random.randint(1, 2))
                        
                #         # 处理剩余文本（复用通用函数）
                #         remaining_text = re.sub(r'#&#\s*.*?\s*#&#', '', reply, flags=re.DOTALL)
                #         if remaining_text:
                #             print('remaining:',remaining_text)
                #             self.send_split_messages(remaining_text, chat_id)
                #     else:
                #         print('unvaild')
                #         self.send_split_messages(reply, chat_id)  # 无效##时回退
                        
                # else:
                #     print('print raw text')
                #     self.send_split_messages(reply, chat_id)  # 直接处理原始内容
                    

                # 检查回复中是否包含情感关键词并发送表情包
                print("\n检查情感关键词...")
                logger.info("开始检查AI回复的情感关键词")
                emotion_detected = False

                try:
                    if not hasattr(self.emoji_handler, 'emotion_map'):
                        logger.error("emoji_handler 缺少 emotion_map 属性")
                        return
                        
                    for emotion, keywords in self.emoji_handler.emotion_map.items():
                        if not keywords:  # 跳过空的关键词列表（如 neutral）
                            continue
                            
                        if any(keyword in reply for keyword in keywords):
                            emotion_detected = True
                            print(f"检测到情感: {emotion}")
                            logger.info(f"在回复中检测到情感: {emotion}")
                            
                            emoji_path = self.emoji_handler.get_emotion_emoji(reply)
                            if emoji_path:
                                try:
                                    print(f"发送情感表情包: {emoji_path}")
                                    self.wx.SendFiles(filepath=emoji_path, who=chat_id)
                                    logger.info(f"已发送情感表情包: {emoji_path}")
                                except Exception as e:
                                    logger.error(f"发送表情包失败: {str(e)}")
                            else:
                                logger.warning(f"未找到对应情感 {emotion} 的表情包")
                            break

                    if not emotion_detected:
                        print("未检测到明显情感")
                        logger.info("未在回复中检测到明显情感")
                        
                except Exception as e:
                    logger.error(f"情感检测过程发生错误: {str(e)}")
                    print(f"情感检测失败: {str(e)}")

                # 异步保存消息记录
                threading.Thread(target=self.save_message, 
                               args=(username, sender_name, merged_message, reply)).start()

            print("="*50 + "\n")

        except Exception as e:
            logger.error(f"处理消息失败: {str(e)}", exc_info=True)
            print("\n处理消息时出现错误:")
            print(f"错误信息: {str(e)}")
            print("="*50 + "\n")
        
    


    def add_to_queue(self, chat_id: str, content: str, sender_name: str, 
                    username: str, is_group: bool = False):
        """添加消息到队列"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        time_aware_content = f"[{current_time}] {content}"

        with self.queue_lock:
            if chat_id not in self.user_queues:
                self.user_queues[chat_id] = {
                    'timer': threading.Timer(5.0, self.process_messages, args=[chat_id]),
                    'messages': [time_aware_content],
                    'sender_name': sender_name,
                    'username': username,
                    'is_group': is_group
                }
                self.user_queues[chat_id]['timer'].start()
            else:
                self.user_queues[chat_id]['timer'].cancel()
                self.user_queues[chat_id]['messages'].append(time_aware_content)
                self.user_queues[chat_id]['timer'] = threading.Timer(5.0, self.process_messages, args=[chat_id])
                self.user_queues[chat_id]['timer'].start() 

