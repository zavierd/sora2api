#!/usr/bin/env python3
"""
GPT Team MySQL -> Sora2API 账号同步脚本

功能：
1. 连接 gpt_team MySQL 数据库
2. 读取 accounts 表中的原始 ChatGPT access_token
3. 通过 Sora2API 的 API 导入账号

使用方法：
    python3 sync_from_gpt_team.py [--dry-run] [--verbose]

配置：
    通过环境变量配置
"""

import os
import sys
import json
import argparse
import logging
import base64
from datetime import datetime
from typing import List, Dict, Optional

import requests

# 可选的 MySQL 驱动
try:
    import pymysql
    MYSQL_DRIVER = "pymysql"
except ImportError:
    try:
        import mysql.connector
        MYSQL_DRIVER = "mysql-connector"
    except ImportError:
        MYSQL_DRIVER = None

# 配置
class Config:
    # MySQL 配置 (gpt_team 数据库)
    MYSQL_HOST = os.getenv("GPT_TEAM_MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.getenv("GPT_TEAM_MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("GPT_TEAM_MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("GPT_TEAM_MYSQL_PASSWORD", "Gemini2024!")
    MYSQL_DATABASE = os.getenv("GPT_TEAM_MYSQL_DATABASE", "gpt_team")
    
    # Sora2API 配置
    SORA2API_URL = os.getenv("SORA2API_URL", "http://localhost:8385")
    SORA2API_ADMIN_USER = os.getenv("SORA2API_ADMIN_USER", "admin")
    SORA2API_ADMIN_PASS = os.getenv("SORA2API_ADMIN_PASS", "admin")
    
    # 同步配置
    # 只同步这些状态的账号
    SYNC_STATUSES = os.getenv("SYNC_STATUSES", "team_active,active").split(",")
    
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


class MySQLClient:
    """MySQL 数据库客户端"""
    
    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.conn = None
    
    def connect(self) -> bool:
        """连接数据库"""
        if MYSQL_DRIVER is None:
            logger.error("❌ 未安装 MySQL 驱动，请安装 pymysql 或 mysql-connector-python")
            return False
        
        try:
            if MYSQL_DRIVER == "pymysql":
                self.conn = pymysql.connect(
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password,
                    database=self.database,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor
                )
            else:
                self.conn = mysql.connector.connect(
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password,
                    database=self.database,
                    charset='utf8mb4'
                )
            logger.info(f"✅ 连接 MySQL 成功: {self.host}:{self.port}/{self.database}")
            return True
        except Exception as e:
            logger.error(f"❌ 连接 MySQL 失败: {e}")
            return False
    
    def close(self):
        """关闭连接"""
        if self.conn:
            self.conn.close()
    
    def get_accounts(self, statuses: List[str]) -> List[Dict]:
        """获取指定状态的账号"""
        if not self.conn:
            return []
        
        try:
            cursor = self.conn.cursor()
            
            # 构建 IN 查询
            placeholders = ','.join(['%s'] * len(statuses))
            sql = f"""
                SELECT id, email, access_token, session_token, status, user_id, team_account_id
                FROM accounts 
                WHERE status IN ({placeholders})
                AND access_token IS NOT NULL
                AND access_token != ''
            """
            
            cursor.execute(sql, statuses)
            
            if MYSQL_DRIVER == "pymysql":
                rows = cursor.fetchall()
            else:
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            
            cursor.close()
            return rows
        except Exception as e:
            logger.error(f"❌ 查询账号失败: {e}")
            return []


def decode_jwt_payload(token: str) -> Dict:
    """从 JWT token 中解码 payload（不验证签名）"""
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            payload = parts[1]
            # 添加 padding
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding
            decoded = base64.urlsafe_b64decode(payload)
            return json.loads(decoded)
    except Exception as e:
        logger.warning(f"Failed to decode JWT: {e}")
    return {}


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
    
    def add_token(self, token_value: str, proxy_url: str = "", 
                  image_concurrency: int = 1, video_concurrency: int = 3,
                  client_id: str = None) -> Dict:
        """添加单个 Token (使用原始 access_token)
        
        如果未提供 client_id，会从 JWT token 中自动提取
        """
        try:
            # 如果未提供 client_id，从 JWT 中提取
            if not client_id:
                jwt_payload = decode_jwt_payload(token_value)
                client_id = jwt_payload.get("client_id")
                if client_id:
                    logger.info(f"  📌 从 JWT 提取 client_id: {client_id[:20]}...")
            
            payload = {
                "token": token_value,
                "proxy_url": proxy_url,
                "image_concurrency": image_concurrency,
                "video_concurrency": video_concurrency
            }
            
            # 如果有 client_id，添加到 payload
            if client_id:
                payload["client_id"] = client_id
            
            resp = self.session.post(
                f"{self.base_url}/api/tokens",
                headers=self._headers(),
                json=payload,
                timeout=120
            )
            return resp.json()
        except Exception as e:
            logger.error(f"❌ 添加 Token 失败: {e}")
            return {"success": False, "message": str(e)}
    
    def delete_token(self, token_id: int) -> Dict:
        """删除 Token"""
        try:
            resp = self.session.delete(
                f"{self.base_url}/api/tokens/{token_id}",
                headers=self._headers(),
                timeout=30
            )
            return resp.json()
        except Exception as e:
            logger.error(f"❌ 删除 Token 失败: {e}")
            return {"success": False, "message": str(e)}


def sync_accounts(dry_run: bool = False, verbose: bool = False, force_update: bool = False):
    """同步账号主函数"""
    logger.info("=" * 60)
    logger.info("GPT Team MySQL -> Sora2API 账号同步")
    logger.info("=" * 60)
    
    # 1. 连接 MySQL
    logger.info(f"🔗 连接 MySQL: {Config.MYSQL_HOST}:{Config.MYSQL_PORT}/{Config.MYSQL_DATABASE}")
    mysql_client = MySQLClient(
        Config.MYSQL_HOST,
        Config.MYSQL_PORT,
        Config.MYSQL_USER,
        Config.MYSQL_PASSWORD,
        Config.MYSQL_DATABASE
    )
    
    if not mysql_client.connect():
        logger.error("❌ 无法连接 MySQL，同步终止")
        return
    
    # 2. 获取 gpt_team 中的账号
    logger.info(f"📊 查询状态为 {Config.SYNC_STATUSES} 的账号...")
    gpt_accounts = mysql_client.get_accounts(Config.SYNC_STATUSES)
    mysql_client.close()
    
    logger.info(f"📊 找到 {len(gpt_accounts)} 个符合条件的账号")
    
    if not gpt_accounts:
        logger.info("ℹ️  没有需要同步的账号")
        return
    
    if verbose:
        for acc in gpt_accounts[:10]:  # 只显示前10个
            logger.info(f"  - {acc['email']} (status: {acc['status']})")
        if len(gpt_accounts) > 10:
            logger.info(f"  ... 还有 {len(gpt_accounts) - 10} 个账号")
    
    # 3. 连接 Sora2API
    logger.info(f"🔗 连接 Sora2API: {Config.SORA2API_URL}")
    sora_client = Sora2APIClient(
        Config.SORA2API_URL,
        Config.SORA2API_ADMIN_USER,
        Config.SORA2API_ADMIN_PASS
    )
    
    if not sora_client.login():
        logger.error("❌ 无法连接 Sora2API，同步终止")
        return
    
    # 4. 获取 Sora2API 现有账号
    existing_tokens = sora_client.get_tokens()
    existing_emails = {t.get("email", ""): t for t in existing_tokens}
    logger.info(f"📊 Sora2API 现有 {len(existing_tokens)} 个账号")
    
    # 5. 分类处理
    new_accounts = []
    update_accounts = []
    
    for acc in gpt_accounts:
        email = acc.get("email", "")
        if not email:
            continue
        
        if email in existing_emails:
            if force_update:
                update_accounts.append(acc)
        else:
            new_accounts.append(acc)
    
    logger.info(f"🆕 需要新增 {len(new_accounts)} 个账号")
    if force_update:
        logger.info(f"🔄 需要更新 {len(update_accounts)} 个账号")
    
    if not new_accounts and not update_accounts:
        logger.info("✅ 所有账号已同步，无需操作")
        return
    
    # 6. 执行同步
    if dry_run:
        logger.info("🔍 [DRY RUN] 以下账号将被同步:")
        for acc in new_accounts:
            logger.info(f"  [新增] {acc['email']}")
        for acc in update_accounts:
            logger.info(f"  [更新] {acc['email']}")
        logger.info("🔍 [DRY RUN] 实际未执行任何操作")
        return
    
    # 统计
    added = 0
    updated = 0
    failed = 0
    
    # 新增账号
    for acc in new_accounts:
        email = acc['email']
        access_token = acc['access_token']
        
        logger.info(f"➕ 添加: {email}")
        result = sora_client.add_token(
            token_value=access_token,
            proxy_url=Config.PROXY_URL,
            image_concurrency=Config.IMAGE_CONCURRENCY,
            video_concurrency=Config.VIDEO_CONCURRENCY
        )
        
        if result.get("success"):
            added += 1
            if verbose:
                logger.info(f"  ✅ 成功")
        else:
            failed += 1
            logger.warning(f"  ❌ 失败: {result.get('message', 'Unknown error')}")
    
    # 更新账号 (删除旧的，添加新的)
    for acc in update_accounts:
        email = acc['email']
        access_token = acc['access_token']
        old_token = existing_emails.get(email)
        
        logger.info(f"🔄 更新: {email}")
        
        # 删除旧 token
        if old_token and old_token.get("id"):
            sora_client.delete_token(old_token["id"])
        
        # 添加新 token
        result = sora_client.add_token(
            token_value=access_token,
            proxy_url=Config.PROXY_URL,
            image_concurrency=Config.IMAGE_CONCURRENCY,
            video_concurrency=Config.VIDEO_CONCURRENCY
        )
        
        if result.get("success"):
            updated += 1
            if verbose:
                logger.info(f"  ✅ 成功")
        else:
            failed += 1
            logger.warning(f"  ❌ 失败: {result.get('message', 'Unknown error')}")
    
    # 7. 输出结果
    logger.info("=" * 60)
    logger.info(f"✅ 同步完成: 新增 {added}, 更新 {updated}, 失败 {failed}")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="GPT Team MySQL -> Sora2API 账号同步")
    parser.add_argument("--dry-run", action="store_true", help="只检查不执行")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--force-update", "-f", action="store_true", help="强制更新已存在的账号")
    
    # MySQL 配置
    parser.add_argument("--mysql-host", help="MySQL 主机")
    parser.add_argument("--mysql-port", type=int, help="MySQL 端口")
    parser.add_argument("--mysql-user", help="MySQL 用户名")
    parser.add_argument("--mysql-pass", help="MySQL 密码")
    parser.add_argument("--mysql-db", help="MySQL 数据库名")
    
    # Sora2API 配置
    parser.add_argument("--sora-url", help="Sora2API URL")
    parser.add_argument("--sora-user", help="Sora2API 管理员用户名")
    parser.add_argument("--sora-pass", help="Sora2API 管理员密码")
    
    args = parser.parse_args()
    
    # 覆盖配置
    if args.mysql_host:
        Config.MYSQL_HOST = args.mysql_host
    if args.mysql_port:
        Config.MYSQL_PORT = args.mysql_port
    if args.mysql_user:
        Config.MYSQL_USER = args.mysql_user
    if args.mysql_pass:
        Config.MYSQL_PASSWORD = args.mysql_pass
    if args.mysql_db:
        Config.MYSQL_DATABASE = args.mysql_db
    if args.sora_url:
        Config.SORA2API_URL = args.sora_url
    if args.sora_user:
        Config.SORA2API_ADMIN_USER = args.sora_user
    if args.sora_pass:
        Config.SORA2API_ADMIN_PASS = args.sora_pass
    
    sync_accounts(dry_run=args.dry_run, verbose=args.verbose, force_update=args.force_update)


if __name__ == "__main__":
    main()
