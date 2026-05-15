"""
七牛云 OSS 核心业务逻辑
"""
from core.config import settings
from core.exceptions import AppException

try:
    from qiniu import Auth, BucketManager, put_data
except ImportError:  # pragma: no cover - optional dependency guard
    Auth = None
    BucketManager = None
    put_data = None

class QiniuClient:
    """七牛云 OSS 客户端：直传 Token 生成与文件物理删除"""

    @staticmethod
    def get_auth() -> Auth:
        """获取 Qiniu 认证对象"""
        if Auth is None:
            raise AppException(code=500, message="缺少 qiniu 依赖，无法使用对象存储能力")
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
    def upload_bytes_to_oss(
        cls,
        object_key: str,
        data: bytes,
        mime_type: str = "application/octet-stream",
        expires: int = 3600,
    ) -> dict:
        """服务端上传二进制内容到七牛云，返回七牛上传结果。"""
        if not object_key or not data or not settings.QINIU_BUCKET_NAME:
            raise AppException(code=500, message="OSS 上传参数或配置缺失")
        if put_data is None:
            raise AppException(code=500, message="缺少 qiniu 依赖，无法上传对象存储文件")

        q = cls.get_auth()
        token = q.upload_token(settings.QINIU_BUCKET_NAME, object_key, expires=expires)
        ret, info = put_data(token, object_key, data, mime_type=mime_type)
        status_code = getattr(info, "status_code", None)
        ok = info.ok() if hasattr(info, "ok") else status_code == 200
        if not ok:
            raise AppException(code=500, message=f"上传对象存储文件失败: {info}")
        return ret or {}

    @classmethod
    def delete_file_from_oss(cls, object_key: str) -> bool:
        """
        从七牛云物理删除文件
        """
        if not object_key or not settings.QINIU_BUCKET_NAME:
            return False
        if BucketManager is None:
            raise AppException(code=500, message="缺少 qiniu 依赖，无法删除对象存储文件")
        q = cls.get_auth()
        bucket = BucketManager(q)
        ret, info = bucket.delete(settings.QINIU_BUCKET_NAME, object_key)
        return info.status_code == 200
