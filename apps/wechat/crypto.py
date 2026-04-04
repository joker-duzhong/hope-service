"""
微信消息加解密工具
基于微信官方加解密方案实现
"""
import base64
import hashlib
import socket
import struct
import time
import xml.etree.ElementTree as ET
from typing import Optional, Tuple

from Crypto.Cipher import AES


class PKCS7Encoder:
    """PKCS7 编码器"""

    block_size = 32

    @classmethod
    def encode(cls, text: bytes) -> bytes:
        text_length = len(text)
        amount_to_pad = cls.block_size - (text_length % cls.block_size)
        if amount_to_pad == 0:
            amount_to_pad = cls.block_size
        pad = chr(amount_to_pad).encode()
        return text + pad * amount_to_pad

    @classmethod
    def decode(cls, decrypted: bytes) -> bytes:
        pad = decrypted[-1]
        return decrypted[:-pad]


class WeChatCrypto:
    """微信消息加解密"""

    def __init__(self, token: str, encoding_aes_key: str, appid: str):
        self.token = token
        self.appid = appid
        # EncodingAESKey 长度为 43，补 = 后 base64 解码得到 32 字节 AES Key
        self.aes_key = base64.b64decode(encoding_aes_key + "=")
        assert len(self.aes_key) == 32

    def verify_signature(self, signature: str, timestamp: str, nonce: str) -> bool:
        """验证签名"""
        components = [self.token, timestamp, nonce]
        components.sort()
        combined = "".join(components)
        hashed = hashlib.sha1(combined.encode("utf-8")).hexdigest()
        return hashed == signature

    def verify_msg_signature(
        self, signature: str, timestamp: str, nonce: str, encrypted_msg: str
    ) -> bool:
        """验证消息签名"""
        components = [self.token, timestamp, nonce, encrypted_msg]
        components.sort()
        combined = "".join(components)
        hashed = hashlib.sha1(combined.encode("utf-8")).hexdigest()
        return hashed == signature

    def decrypt(self, encrypted_msg: str) -> str:
        """解密消息"""
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv=self.aes_key[:16])
        decrypted = cipher.decrypt(base64.b64decode(encrypted_msg))
        # 去除 PKCS7 填充
        decrypted = PKCS7Encoder.decode(decrypted)
        # 去除 16 字节随机字符串
        content = decrypted[16:]
        # 获取消息长度 (4 字节网络字节序)
        msg_len = socket.ntohl(struct.unpack("I", content[:4])[0])
        # 获取消息内容
        msg = content[4 : 4 + msg_len].decode("utf-8")
        # 获取 appid
        from_appid = content[4 + msg_len :].decode("utf-8")
        if from_appid != self.appid:
            raise ValueError(f"AppID mismatch: {from_appid} != {self.appid}")
        return msg

    def encrypt(self, msg: str) -> str:
        """加密消息"""
        # 16 字节随机字符串
        random_str = b"xxxxxxxxxxxxxxxx"
        msg_bytes = msg.encode("utf-8")
        msg_len = struct.pack("I", socket.htonl(len(msg_bytes)))
        appid_bytes = self.appid.encode("utf-8")
        content = random_str + msg_len + msg_bytes + appid_bytes
        # PKCS7 填充
        padded = PKCS7Encoder.encode(content)
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv=self.aes_key[:16])
        encrypted = cipher.encrypt(padded)
        return base64.b64encode(encrypted).decode("utf-8")

    def generate_signature(
        self, timestamp: str, nonce: str, encrypted_msg: str
    ) -> str:
        """生成签名"""
        components = [self.token, timestamp, nonce, encrypted_msg]
        components.sort()
        combined = "".join(components)
        return hashlib.sha1(combined.encode("utf-8")).hexdigest()

    def decrypt_message(
        self, encrypted_xml: str, msg_signature: str, timestamp: str, nonce: str
    ) -> str:
        """解密 XML 消息并返回明文 XML"""
        # 解析加密的 XML
        root = ET.fromstring(encrypted_xml)
        encrypted_msg = root.findtext("Encrypt", default="")

        # 验证消息签名
        if not self.verify_msg_signature(msg_signature, timestamp, nonce, encrypted_msg):
            raise ValueError("Invalid message signature")

        # 解密消息
        return self.decrypt(encrypted_msg)

    def encrypt_message(self, reply_msg: str, timestamp: str, nonce: str) -> str:
        """加密回复消息并返回加密后的 XML"""
        encrypted = self.encrypt(reply_msg)
        signature = self.generate_signature(timestamp, nonce, encrypted)
        return f"""<xml>
<Encrypt><![CDATA[{encrypted}]]></Encrypt>
<MsgSignature><![CDATA[{signature}]]></MsgSignature>
<TimeStamp>{timestamp}</TimeStamp>
<Nonce><![CDATA[{nonce}]]></Nonce>
</xml>"""
