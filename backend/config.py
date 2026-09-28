"""模型相关的常量。单独一个文件，不 import 任何东西。

cache.py 算 key 要用模型名和温度，但不该为此初始化 API 客户端；
llm.py 调模型也用同一份，两边永远一致。
"""

MODEL = "deepseek-chat"
TEMPERATURE = 0          # 评测需要可复现，先用 0
