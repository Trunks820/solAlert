"""
Telegram API 客户端
通过 HTTP API (http://kakarot8.fun:8000) 发送消息
"""
import asyncio
import logging
import httpx
from typing import Optional, List, Dict, Any, Union
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..core.config import TELEGRAM_CONFIG

logger = logging.getLogger('solalert.api.telegram_api')


class TelegramAPI:
    """
    Telegram API 统一客户端
    通过 HTTP API 发送消息到 Telegram
    
    提供简洁的消息发送接口，支持:
    - 单条消息发送
    - 批量消息发送
    - 论坛主题消息
    - 别名映射
    """
    
    # HTTP 客户端（单例模式）
    _http_client: Optional[httpx.AsyncClient] = None
    _semaphore: Optional[asyncio.Semaphore] = None
    
    # API 配置
    API_BASE_URL = "http://kakarot8.fun:8000"
    API_KEY = "Wy1997@Kakarot"
    
    # Chat ID 别名映射
    CHAT_ALIASES = {
        'forum': str(TELEGRAM_CONFIG.get('forum_group_id')),
        'default': str(TELEGRAM_CONFIG.get('target_channel_id')),
        'gmgn': str(TELEGRAM_CONFIG.get('gmgn_channel_id')),
        'bsc': str(TELEGRAM_CONFIG.get('bsc_channel_id')),
        'bsc_monitor': str(TELEGRAM_CONFIG.get('bsc_channel_id')),
    }
    
    @classmethod
    def _get_http_client(cls) -> httpx.AsyncClient:
        """获取 HTTP 客户端实例（单例）"""
        if cls._http_client is None:
            cls._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True
            )
            logger.debug("✅ HTTP 客户端初始化成功")
            
            # 初始化信号量（限制并发数）
            if cls._semaphore is None:
                cls._semaphore = asyncio.Semaphore(10)
        
        return cls._http_client
    
    @classmethod
    def _resolve_chat_id(cls, chat_id: Union[str, int]) -> str:
        """
        解析 Chat ID（支持别名）
        
        Args:
            chat_id: Chat ID 或别名
            
        Returns:
            解析后的 Chat ID
        """
        # 如果是数字，直接转为字符串
        if isinstance(chat_id, int):
            return str(chat_id)
        
        # 如果是别名，查找映射
        chat_id_str = str(chat_id).lower().strip()
        if chat_id_str in cls.CHAT_ALIASES:
            resolved = cls.CHAT_ALIASES[chat_id_str]
            logger.debug(f"📝 别名解析: {chat_id} -> {resolved}")
            return resolved
        
        return str(chat_id)
    
    @classmethod
    def _convert_reply_markup_to_buttons_array(cls, reply_markup: InlineKeyboardMarkup) -> List[List[Dict[str, str]]]:
        """
        将 InlineKeyboardMarkup 转换为按钮数组格式（适用于后端 HTTP API）
        
        后端 API 需要的格式:
        [
            [{"text": "按钮1", "url": "https://..."}, {"text": "按钮2", "url": "https://..."}],
            [{"text": "按钮3", "callback_data": "data"}]
        ]
        
        Args:
            reply_markup: InlineKeyboardMarkup 对象
            
        Returns:
            二维数组格式的按钮数据
        """
        if not reply_markup:
            return []
        
        # 提取按钮数据（转换为二维数组）
        buttons_array = []
        for row in reply_markup.inline_keyboard:
            button_row = []
            for button in row:
                button_data = {
                    'text': button.text
                }
                
                # 添加按钮动作（URL、callback_data 等）
                if button.url:
                    button_data['url'] = button.url
                elif button.callback_data:
                    button_data['callback_data'] = button.callback_data
                elif button.switch_inline_query:
                    button_data['switch_inline_query'] = button.switch_inline_query
                elif button.switch_inline_query_current_chat:
                    button_data['switch_inline_query_current_chat'] = button.switch_inline_query_current_chat
                
                button_row.append(button_data)
            
            if button_row:
                buttons_array.append(button_row)
        
        return buttons_array
    
    @classmethod
    async def send_message(
        cls,
        chat_id: Union[str, int],
        message: str,
        parse_mode: str = "HTML",
        topic_id: Optional[int] = None,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        disable_notification: bool = False,
        disable_web_page_preview: bool = True,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        发送消息（通过 HTTP API）
        
        Args:
            chat_id: 目标 Chat ID 或别名（如 'bsc', 'gmgn'）
            message: 消息内容（支持 HTML 格式）
            parse_mode: 解析模式（HTML/Markdown）
            topic_id: 论坛主题 ID（可选）
            reply_markup: 按钮（暂不支持）
            disable_notification: 是否静默发送
            disable_web_page_preview: 是否禁用链接预览
            max_retries: 最大重试次数
            
        Returns:
            {
                'success': True/False,
                'message_id': int,  # 成功时返回
                'chat_id': str,
                'error': str  # 失败时返回
            }
        """
        resolved_chat_id = cls._resolve_chat_id(chat_id)
        
        try:
            client = cls._get_http_client()
            url = f"{cls.API_BASE_URL}/api/telegram/send"
            
            # 构建请求负载
            payload = {
                "api_key": cls.API_KEY,
                "chat_id": resolved_chat_id,
                "message": message,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
                "disable_web_page_preview": disable_web_page_preview
            }
            
            # 如果有论坛主题 ID
            if topic_id:
                payload["message_thread_id"] = topic_id
            
            # 如果有按钮，转换为后端 API 需要的 buttons 数组格式
            if reply_markup:
                try:
                    buttons_array = cls._convert_reply_markup_to_buttons_array(reply_markup)
                    payload["buttons"] = buttons_array
                except Exception as e:
                    logger.warning(f"⚠️ 按钮转换失败: {e}")
            
            # 使用信号量限制并发
            async with cls._semaphore:
                for attempt in range(max_retries):
                    try:
                        if attempt == 0:
                            logger.debug(f"📤 [HTTP API] 发送消息 -> {resolved_chat_id}")
                        else:
                            logger.info(f"📤 [HTTP API] 重试发送 ({attempt + 1}/{max_retries}) -> {resolved_chat_id}")
                        
                        response = await client.post(url, json=payload)
                        response.raise_for_status()
                        
                        result = response.json()
                        
                        if result.get('status') == 'success':
                            message_id = result.get('message_id', 0)
                            logger.info(f"✅ [HTTP API] 发送成功 -> {resolved_chat_id} (ID: {message_id})")
                            return {
                                'success': True,
                                'message_id': message_id,
                                'chat_id': resolved_chat_id
                            }
                        else:
                            error_msg = result.get('error', 'Unknown error')
                            logger.error(f"❌ [HTTP API] 发送失败: {error_msg}")
                            return {
                                'success': False,
                                'chat_id': resolved_chat_id,
                                'error': error_msg
                            }
                    
                    except httpx.TimeoutException as e:
                        error_detail = f"URL={url}, 超时={type(e).__name__}"
                        if attempt < max_retries - 1:
                            wait_time = 2 * (2 ** attempt)
                            logger.warning(f"⚠️ [HTTP API] 超时，{wait_time}秒后重试 ({error_detail})")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"❌ [HTTP API] 超时，已重试{max_retries}次 ({error_detail})")
                            return {'success': False, 'chat_id': resolved_chat_id, 'error': f'timeout after {max_retries} retries: {error_detail}'}
                    
                    except httpx.HTTPStatusError as e:
                        logger.error(f"❌ [HTTP API] HTTP错误 {e.response.status_code}: {e}")
                        return {'success': False, 'chat_id': resolved_chat_id, 'error': f'http_error: {e.response.status_code}'}
                    
                    except Exception as e:
                        if attempt < max_retries - 1:
                            wait_time = 2 * (2 ** attempt)
                            logger.warning(f"⚠️ [HTTP API] 错误，{wait_time}秒后重试: {e}")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"❌ [HTTP API] 异常，已重试{max_retries}次: {e}")
                            return {'success': False, 'chat_id': resolved_chat_id, 'error': str(e)}
            
            return {'success': False, 'chat_id': resolved_chat_id, 'error': 'max_retries_exceeded'}
        
        except Exception as e:
            logger.error(f"❌ [HTTP API] 发送消息异常: {e}")
            return {'success': False, 'chat_id': str(chat_id), 'error': str(e)}
    
    @classmethod
    async def send_batch(
        cls,
        chat_ids: List[Union[str, int]],
        message: str,
        parse_mode: str = "HTML",
        **kwargs
    ) -> Dict[str, Any]:
        """
        批量发送消息
        
        Args:
            chat_ids: Chat ID 列表（支持别名）
            message: 消息内容
            parse_mode: 解析模式
            **kwargs: 其他参数（传递给 send_message）
            
        Returns:
            {
                'success_count': int,
                'failed_count': int,
                'total': int,
                'details': [{'chat_id': str, 'success': bool, 'message_id': int, 'error': str}, ...]
            }
        """
        tasks = []
        for chat_id in chat_ids:
            tasks.append(cls.send_message(chat_id, message, parse_mode, **kwargs))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        details = []
        success_count = 0
        failed_count = 0
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                details.append({
                    'chat_id': str(chat_ids[i]),
                    'success': False,
                    'error': str(result)
                })
                failed_count += 1
            elif result.get('success'):
                details.append(result)
                success_count += 1
            else:
                details.append(result)
                failed_count += 1
        
        logger.info(f"📊 批量发送完成: {success_count}/{len(chat_ids)} 成功")
        
        return {
            'success_count': success_count,
            'failed_count': failed_count,
            'total': len(chat_ids),
            'details': details
        }
    
    @classmethod
    async def send_to_forum_topic(
        cls,
        topic_id: int,
        message: str,
        parse_mode: str = "HTML",
        **kwargs
    ) -> Dict[str, Any]:
        """
        发送消息到论坛主题
        
        Args:
            topic_id: 论坛主题 ID
            message: 消息内容
            parse_mode: 解析模式
            **kwargs: 其他参数
            
        Returns:
            发送结果
        """
        forum_group_id = TELEGRAM_CONFIG.get('forum_group_id')
        return await cls.send_message(
            chat_id=forum_group_id,
            message=message,
            parse_mode=parse_mode,
            topic_id=topic_id,
            **kwargs
        )
    
    
    @classmethod
    def add_chat_alias(cls, alias: str, chat_id: Union[str, int]):
        """
        添加 Chat ID 别名
        
        Args:
            alias: 别名
            chat_id: Chat ID
        """
        cls.CHAT_ALIASES[alias.lower()] = str(chat_id)
        logger.info(f"✅ 添加别名: {alias} -> {chat_id}")
    
    @classmethod
    def get_chat_aliases(cls) -> Dict[str, str]:
        """获取所有别名映射"""
        return cls.CHAT_ALIASES.copy()


# ========== 便捷函数 ==========

async def send_telegram(
    chat_id: Union[str, int],
    message: str,
    **kwargs
) -> Dict[str, Any]:
    """
    便捷发送函数
    
    示例:
        await send_telegram('bsc', '<b>测试消息</b>')
        await send_telegram(-1002569554228, '消息内容')
    """
    return await TelegramAPI.send_message(chat_id, message, **kwargs)


async def send_telegram_batch(
    chat_ids: List[Union[str, int]],
    message: str,
    **kwargs
) -> Dict[str, Any]:
    """
    批量发送便捷函数
    
    示例:
        await send_telegram_batch(['bsc', 'gmgn'], '<b>批量消息</b>')
    """
    return await TelegramAPI.send_batch(chat_ids, message, **kwargs)



