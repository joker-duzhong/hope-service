# 账伴隔离测试

在服务仓库根目录执行：

```powershell
python -m pip install -r apps/ledger_mate/tests/requirements.txt
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m pytest -p no:cacheprovider apps/ledger_mate/tests -q
```

该测试目录应独立执行。测试进程替换共享数据库和 LLM 导入，避免加载 `.env`、连接生产数据库或请求真实模型；使用内存 SQLite 验证事务、接口与序列化。共享响应类型是只读加载的实际代码。

PostgreSQL 行锁与 advisory lock 通过生成的真实 SQL 验证；跨进程并发压力与真实模型效果未在这些隔离测试中覆盖。
