"""
七牛云 OSS 核心业务逻辑
"""
from qiniu import Auth, BucketManager
from core.config import settings

class QiniuClient:
    """七牛云 OSS 客户端：直传 Token 生成与文件物理删除"""

    @staticmethod
    def get_auth() -> Auth:
        """获取 Qiniu 认证对象"""
        return Auth(settings.QINIU_ACCESS_KEY, settings.QINIU_SECRET_KEY)

    @classmethod
    def generate_upload_token(cls, expires: int = 7200) -> str:
        """
        生成前端直传凭证
        :param expires: 有效时长（秒）
        """
        if not settings.QINIU_ACCESS_KEY or not settings.QINIU_SECRET_KEY or not settings.QINIU_BUCKET_NAME:
            return ""
        q = cls.get_auth()
        # 允许文件名在前端决定，或者 Qiniu 随机生成
        token = q.upload_token(settings.QINIU_BUCKET_NAME, expires=expires)
        return token

    @classmethod
    def delete_file_from_oss(cls, object_key: str) -> bool:
        """
        从七牛云物理删除文件
        """
        if not object_key or not settings.QINIU_BUCKET_NAME:
            return False
        q = cls.get_auth()
        bucket = BucketManager(q)
        ret, info = bucket.delete(settings.QINIU_BUCKET_NAME, object_key)
        return info.status_code == 200
