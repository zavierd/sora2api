"""
Browser fingerprint utilities for bypassing Cloudflare
参考 gpt_team/chatgpt_register 的实现
"""

import random
import time
from typing import Dict, Optional
from curl_cffi.requests import AsyncSession


# 支持的浏览器指纹配置（指纹 + 对应的 User-Agent 信息）
# 选择现代、常用的 Chrome 版本，确保指纹和 UA 匹配
BROWSER_FINGERPRINTS = [
    {
        "impersonate": "chrome120",
        "version": "120.0.0.0",
        "major": "120",
    },
    {
        "impersonate": "chrome123",
        "version": "123.0.0.0",
        "major": "123",
    },
    {
        "impersonate": "chrome124",
        "version": "124.0.0.0",
        "major": "124",
    },
    {
        "impersonate": "chrome131",
        "version": "131.0.0.0",
        "major": "131",
    },
]


def get_random_fingerprint() -> dict:
    """随机选择一个浏览器指纹配置"""
    return random.choice(BROWSER_FINGERPRINTS)


def get_user_agent(fingerprint: dict = None) -> str:
    """获取与指纹匹配的 User-Agent"""
    fp = fingerprint or get_random_fingerprint()
    version = fp["version"]
    return f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"


def get_sec_ch_ua(fingerprint: dict = None) -> str:
    """获取与指纹匹配的 sec-ch-ua 头"""
    fp = fingerprint or get_random_fingerprint()
    major = fp["major"]
    return f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not A(Brand";v="24"'


def generate_fake_cf_clearance() -> str:
    """生成假的 cf_clearance cookie 值"""
    return f"fake_{int(time.time())}_{random.randint(10000, 99999)}"


class BrowserSession:
    """
    带有浏览器指纹的 HTTP 会话管理器
    使用 curl_cffi 模拟 Chrome TLS/JA3 指纹
    """
    
    def __init__(
        self,
        proxy: Optional[str] = None,
        timeout: int = 30,
        fingerprint: Optional[dict] = None,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.fingerprint = fingerprint or get_random_fingerprint()
        self._cf_clearance = generate_fake_cf_clearance()
    
    @property
    def impersonate(self) -> str:
        """获取 impersonate 值"""
        return self.fingerprint["impersonate"]
    
    @property
    def user_agent(self) -> str:
        """获取 User-Agent"""
        return get_user_agent(self.fingerprint)
    
    @property
    def sec_ch_ua(self) -> str:
        """获取 sec-ch-ua"""
        return get_sec_ch_ua(self.fingerprint)
    
    def get_default_headers(self) -> Dict[str, str]:
        """获取默认请求头"""
        return {
            "User-Agent": self.user_agent,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
        }
    
    def get_cookies_for_domain(self, domain: str) -> Dict[str, str]:
        """获取指定域名的 cookies（包含假的 cf_clearance）"""
        cookies = {
            "cf_clearance": self._cf_clearance,
        }
        return cookies
    
    async def create_session(self) -> AsyncSession:
        """
        创建带有正确指纹的 AsyncSession
        
        注意：curl_cffi 的 AsyncSession 需要在 async with 中使用
        这里返回一个配置好的 session
        """
        session = AsyncSession(
            impersonate=self.impersonate,
            timeout=self.timeout,
            proxy=self.proxy,
        )
        
        # 设置假的 cf_clearance cookie 到相关域名
        domains = [
            "chatgpt.com",
            "sora.chatgpt.com", 
            "auth.openai.com",
            "openai.com",
        ]
        for domain in domains:
            session.cookies.set("cf_clearance", self._cf_clearance, domain=domain)
        
        return session


async def create_browser_session(
    proxy: Optional[str] = None,
    timeout: int = 30,
    fingerprint: Optional[dict] = None,
) -> AsyncSession:
    """
    快捷函数：创建带有浏览器指纹的 AsyncSession
    
    Args:
        proxy: 代理 URL
        timeout: 超时时间
        fingerprint: 指纹配置，None 则随机
        
    Returns:
        配置好的 AsyncSession
    """
    fp = fingerprint or get_random_fingerprint()
    cf_clearance = generate_fake_cf_clearance()
    
    session = AsyncSession(
        impersonate=fp["impersonate"],
        timeout=timeout,
        proxy=proxy,
    )
    
    # 设置假的 cf_clearance cookie
    domains = ["chatgpt.com", "sora.chatgpt.com", "auth.openai.com", "openai.com"]
    for domain in domains:
        session.cookies.set("cf_clearance", cf_clearance, domain=domain)
    
    return session


def get_request_kwargs(
    proxy: Optional[str] = None,
    timeout: int = 30,
    fingerprint: Optional[dict] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Dict:
    """
    获取请求参数（用于 session.get/post）
    
    Args:
        proxy: 代理 URL
        timeout: 超时时间
        fingerprint: 指纹配置
        headers: 额外的请求头
        
    Returns:
        可直接传给 session.get/post 的 kwargs
    """
    fp = fingerprint or get_random_fingerprint()
    
    kwargs = {
        "timeout": timeout,
        "impersonate": fp["impersonate"],
    }
    
    if proxy:
        kwargs["proxy"] = proxy
    
    if headers:
        # 合并默认头和自定义头
        default_headers = {
            "sec-ch-ua": get_sec_ch_ua(fp),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
        }
        default_headers.update(headers)
        kwargs["headers"] = default_headers
    
    return kwargs
