# 模拟服务器的配置
mock_server_base_url = 'http://localhost:5000'
# 实际 AI 对话服务器的配置
real_server_base_url = 'http://localhost:11434'
api_key = 'sk-'
model = 'deepseek-r1:8b'
messages = [
    {
        "role": "user",
        "content": "你好，请返回【测试成功】"
    }
]
#呜呜呜，前端大佬看到这句话请把它写成webui的格式好吗？