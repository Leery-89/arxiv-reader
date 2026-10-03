"""模型相关的常量。单独一个文件，不 import 任何东西。

cache.py 算 key 要用模型名和温度，但不该为此初始化 API 客户端；
llm.py 调模型也用同一份，两边永远一致。
"""

MODEL = "deepseek-chat"
TEMPERATURE = 0          # 评测需要可复现，先用 0

# 解析器版本（D18）：改了 fetcher.py 里任何会改变论文文本的逻辑，就把这个数加 1。
# 它进缓存 key（旧结果自动失效）和快照（知道快照是哪版解析器产出的）。
#   1  初版
#   2  修 LaTeXML 千分位 bug：2true294 → 2,294（D18）
PARSER_VERSION = 2
