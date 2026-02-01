#!/usr/bin/env python3
"""
CLIProxyAPI -> Sora2API 账号同步脚本

功能：
1. 读取 CLIProxyAPI 的 Codex 认证文件
2. 提取 Refresh Token
3. 通过 Sora2API 的 API 导入账号

使用方法：
    python3 sync_from_cliproxy.py [--dry-run] [--verbose]

配置：
    通过环境变量或 config.yaml 配置
"""

import os
import sys
import json
import glob
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
import requests

# 配置
class Config:
    # CLIProxyAPI 认证目录
    CLIPROXY_AUTH_DIR = os.getenv("CLIPROXY_AUTH_DIR", "/root/CLIProxyAPI/auths")
    
    # Sora2API 配置
    SORA2API_URL = os.getenv("SORA2API_URL", "http://localhost:8000")
    SORA2API_ADMIN_USER = os.getenv("SORA2API_ADMIN_USER", "admin")
    SORA2API_ADMIN_PASS = os.getenv("SORA2API_ADMIN_PASS", "admin")
    
    # Codex Client ID (用于 RT -> AT 转换)
    # 默认使用 CLIProxyAPI 的 Codex client_id
    CODEX_CLIENT_ID = os.getenv("CODEX_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")
    
    # 代理配置 (可选)
    PROXY_URL = os.getenv("PROXY_URL", "")
    
    # 并发配置
    IMAGE_CONCURRENCY = int(os.getenv("IMAGE_CONCURRENCY", "1"))
    VIDEO_CONCURRENCY = int(os.getenv("VIDEO_CONCURRENCY", "3"))

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class Sora2APIClient:
    """Sora2API 客户端"""
    
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.token = None
        self.session = requests.Session()
    
    def login(self) -> bool:
        """登录获取 Admin Token"""
        try:
            resp = self.session.post(
                f"{self.base_url}/api/login",
                json={"username": self.username, "password": self.password},
                timeout=30
            )
            data = resp.json()
            if data.get("success"):
                self.token = data.get("token")
                logger.info("✅ Sora2API 登录成功")
                return True
            else:
                logger.error(f"❌ Sora2API 登录失败: {data.get('message')}")
                return False
        except Exception as e:
            logger.error(f"❌ Sora2API 登录异常: {e}")
            return False
    
    def _headers(self) -> Dict:
        """获取请求头"""
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}
    
    def get_tokens(self) -> List[Dict]:
        """获取现有 Token 列表"""
        try:
            resp = self.session.get(
                f"{self.base_url}/api/tokens",
                headers=self._headers(),
                timeout=30
            )
            return resp.json() if resp.status_code == 200 else []
        except Exception as e:
            logger.error(f"❌ 获取 Token 列表失败: {e}")
            return []
    
    def import_pure_rt(self, refresh_tokens: List[str], client_id: str, 
                       proxy_url: str = "", image_concurrency: int = 1,
                       video_concurrency: int = 3) -> Dict:
        """批量导入 Refresh Token"""
        try:
            payload = {
                "refresh_tokens": refresh_tokens,
                "client_id": client_id,
                "proxy_url": proxy_url,
                "image_concurrency": image_concurrency,
                "video_concurrency": video_concurrency
            }
            resp = self.session.post(
                f"{self.base_url}/api/tokens/import/pure-rt",
                headers=self._headers(),
                json=payload,
                timeout=300  # RT 转换可能较慢
            )
            return resp.json()
        except Exception as e:
            logger.error(f"❌ 导入 RT 失败: {e}")
            return {"success": False, "message": str(e)}

def read_cliproxy_codex_files(auth_dir: str) -> List[Dict]:
    """读取 CLIProxyAPI 的 Codex 认证文件"""
    codex_files = []
    pattern = os.path.join(auth_dir, "codex-*.json")
    
    for filepath in glob.glob(pattern):
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            # 只处理 codex 类型且有 refresh_token 的文件
            if data.get("type") == "codex" and data.get("refresh_token"):
                codex_files.append({
                    "filepath": filepath,
                    "email": data.get("email", ""),
                    "refresh_token": data.get("refresh_token"),
                    "access_token": data.get("access_token", ""),
                    "expired": data.get("expired", ""),
                    "disabled": data.get("disabled", False)
                })
        except Exception as e:
            logger.warning(f"⚠️  读取文件失败 {filepath}: {e}")
    
    return codex_files

def filter_new_accounts(codex_files: List[Dict], existing_emails: set) -> List[Dict]:
    """过滤出新账号（不在 Sora2API 中的）"""
    new_accounts = []
    for account in codex_files:
        email = account.get("email", "")
        if email and email not in existing_emails:
            new_accounts.append(account)
    return new_accounts

def sync_accounts(dry_run: bool = False, verbose: bool = False):
    """同步账号主函数"""
    logger.info("=" * 60)
    logger.info("CLIProxyAPI -> Sora2API 账号同步")
    logger.info("=" * 60)
    
    # 1. 读取 CLIProxyAPI Codex 文件
    logger.info(f"📁 读取 CLIProxyAPI 认证目录: {Config.CLIPROXY_AUTH_DIR}")
    codex_files = read_cliproxy_codex_files(Config.CLIPROXY_AUTH_DIR)
    
    # 过滤已禁用的账号
    active_codex = [f for f in codex_files if not f.get("disabled", False)]
    
    logger.info(f"📊 找到 {len(codex_files)} 个 Codex 文件, {len(active_codex)} 个活跃账号")
    
    if not active_codex:
        logger.info("ℹ️  没有需要同步的账号")
        return
    
    if verbose:
        for acc in active_codex:
            logger.info(f"  - {acc['email']}")
    
    # 2. 连接 Sora2API
    logger.info(f"🔗 连接 Sora2API: {Config.SORA2API_URL}")
    client = Sora2APIClient(
        Config.SORA2API_URL,
        Config.SORA2API_ADMIN_USER,
        Config.SORA2API_ADMIN_PASS
    )
    
    if not client.login():
        logger.error("❌ 无法连接 Sora2API，同步终止")
        return
    
    # 3. 获取 Sora2API 现有账号
    existing_tokens = client.get_tokens()
    existing_emails = {t.get("email", "") for t in existing_tokens}
    logger.info(f"📊 Sora2API 现有 {len(existing_tokens)} 个账号")
    
    # 4. 找出需要同步的新账号
    new_accounts = filter_new_accounts(active_codex, existing_emails)
    logger.info(f"🆕 需要同步 {len(new_accounts)} 个新账号")
    
    if not new_accounts:
        logger.info("✅ 所有账号已同步，无需操作")
        return
    
    if verbose:
        logger.info("新账号列表:")
        for acc in new_accounts:
            logger.info(f"  - {acc['email']}")
    
    # 5. 执行同步
    if dry_run:
        logger.info("🔍 [DRY RUN] 以下账号将被同步:")
        for acc in new_accounts:
            logger.info(f"  - {acc['email']}")
        logger.info("🔍 [DRY RUN] 实际未执行任何操作")
        return
    
    # 提取 RT 列表
    refresh_tokens = [acc["refresh_token"] for acc in new_accounts]
    
    logger.info(f"🚀 开始导入 {len(refresh_tokens)} 个账号...")
    result = client.import_pure_rt(
        refresh_tokens=refresh_tokens,
        client_id=Config.CODEX_CLIENT_ID,
        proxy_url=Config.PROXY_URL,
        image_concurrency=Config.IMAGE_CONCURRENCY,
        video_concurrency=Config.VIDEO_CONCURRENCY
    )
    
    # 6. 输出结果
    if result.get("success"):
        added = result.get("added", 0)
        updated = result.get("updated", 0)
        failed = result.get("failed", 0)
        logger.info(f"✅ 同步完成: 新增 {added}, 更新 {updated}, 失败 {failed}")
        
        if verbose and result.get("results"):
            for r in result["results"]:
                status = "✓" if r.get("status") != "failed" else "✗"
                email = r.get("email", "unknown")
                msg = r.get("message", r.get("status", ""))
                logger.info(f"  {status} {email}: {msg}")
    else:
        logger.error(f"❌ 同步失败: {result.get('message')}")

def main():
    parser = argparse.ArgumentParser(description="CLIProxyAPI -> Sora2API 账号同步")
    parser.add_argument("--dry-run", action="store_true", help="只检查不执行")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--auth-dir", help="CLIProxyAPI 认证目录")
    parser.add_argument("--sora-url", help="Sora2API URL")
    parser.add_argument("--sora-user", help="Sora2API 管理员用户名")
    parser.add_argument("--sora-pass", help="Sora2API 管理员密码")
    
    args = parser.parse_args()
    
    # 覆盖配置
    if args.auth_dir:
        Config.CLIPROXY_AUTH_DIR = args.auth_dir
    if args.sora_url:
        Config.SORA2API_URL = args.sora_url
    if args.sora_user:
        Config.SORA2API_ADMIN_USER = args.sora_user
    if args.sora_pass:
        Config.SORA2API_ADMIN_PASS = args.sora_pass
    
    sync_accounts(dry_run=args.dry_run, verbose=args.verbose)

if __name__ == "__main__":
    main()
