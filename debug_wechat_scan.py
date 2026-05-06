"""
微信扫码登录问题诊断脚本
"""
import asyncio
import json
import httpx
import sys
import io
from core.config import settings
from core.redis_client import redis_client

# 设置输出编码为UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


async def diagnose():
    print("=" * 60)
    print("微信扫码登录诊断")
    print("=" * 60)

    appid = "wx29ee8c1ad373bafa"

    # 1. 检查配置
    print("\n[1] 检查微信配置...")
    config = settings.get_wechat_config(appid)
    if config:
        print(f"✅ 配置存在")
        print(f"   - Token: {config.get('token')}")
        print(f"   - Secret: {config.get('secret')[:10]}...")
        print(f"   - AES Key: {config.get('encoding_aes_key')[:10]}...")
    else:
        print(f"❌ 配置不存在")
        return

    # 2. 测试access_token
    print("\n[2] 测试获取 access_token...")
    try:
        secret = config.get("secret")
        url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={appid}&secret={secret}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            data = resp.json()
            if "access_token" in data:
                print(f"✅ access_token 获取成功")
                print(f"   - Token: {data['access_token'][:20]}...")
                print(f"   - Expires: {data.get('expires_in')}秒")
            else:
                print(f"❌ access_token 获取失败")
                print(f"   - Error: {data}")
    except Exception as e:
        print(f"❌ 请求失败: {e}")

    # 3. 检查回调URL配置
    print("\n[3] 检查回调URL配置...")
    callback_url = f"https://api.lxyy.fun/api/v1/wechat/callback/{appid}"
    print(f"   回调URL应该配置为: {callback_url}")
    print(f"   请在微信公众号后台检查:")
    print(f"   设置与开发 -> 基本配置 -> 服务器配置")

    # 4. 测试回调接口可达性
    print("\n[4] 测试回调接口...")
    try:
        async with httpx.AsyncClient() as client:
            # 测试GET验证
            import hashlib
            token = config.get('token')
            timestamp = '1234567890'
            nonce = 'test123'
            components = sorted([token, timestamp, nonce])
            signature = hashlib.sha1(''.join(components).encode()).hexdigest()

            test_url = f"{callback_url}?signature={signature}&timestamp={timestamp}&nonce={nonce}&echostr=test"
            resp = await client.get(test_url)
            if resp.text == "test":
                print(f"✅ GET验证接口正常")
            else:
                print(f"❌ GET验证接口异常: {resp.text}")

            # 测试POST接口
            test_xml = f"""<xml>
<ToUserName><![CDATA[gh_test]]></ToUserName>
<FromUserName><![CDATA[oTest123]]></FromUserName>
<CreateTime>1234567890</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[SCAN]]></Event>
<EventKey><![CDATA[test-scene-id]]></EventKey>
</xml>"""
            resp = await client.post(callback_url, content=test_xml)
            if resp.text == "success":
                print(f"✅ POST事件接口正常")
            else:
                print(f"❌ POST事件接口异常: {resp.text}")
    except Exception as e:
        print(f"❌ 接口测试失败: {e}")

    # 5. 检查Redis连接
    print("\n[5] 检查Redis连接...")
    try:
        test_key = "wechat_test_key"
        await redis_client.setex(test_key, 10, "test_value")
        value = await redis_client.get(test_key)
        if value == "test_value":
            print(f"✅ Redis连接正常")
        else:
            print(f"❌ Redis读写异常")
    except Exception as e:
        print(f"❌ Redis连接失败: {e}")

    # 6. 生成新的测试二维码
    print("\n[6] 生成测试二维码...")
    try:
        from core.wechat.services import WeChatService
        result = await WeChatService.create_qrcode(appid)
        print(f"✅ 二维码生成成功")
        print(f"   - Scene ID: {result['scene_id']}")
        print(f"   - QR URL: {result['qr_url']}")
        print(f"   - 状态查询: https://api.lxyy.fun/api/v1/auth/wechat/status?scene_id={result['scene_id']}")

        # 检查Redis中的初始状态
        scene_id = result['scene_id']
        data = await redis_client.get(f"wechat_scan:{scene_id}")
        if data:
            print(f"   - Redis状态: {data}")

        print(f"\n   [!] 请用微信扫描上面的二维码，然后检查:")
        print(f"   1. 微信公众号后台是否收到推送")
        print(f"   2. 服务器日志是否有回调请求")
        print(f"   3. Redis中的状态是否更新")

    except Exception as e:
        print(f"❌ 二维码生成失败: {e}")

    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(diagnose())
