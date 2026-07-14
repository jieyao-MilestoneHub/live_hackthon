"""聊天優先高光分析（分析流程階段一～三，規則式初篩）。

管線：clean（CSV→chatlog.v1）→ spam（洗版標記）→ volume（每分鐘熱區 mean+1σ）
→ candidates（情緒排序取前 N）→ detect（換算+輸出 highlights.v1）。全為 pure function，
時間在 epoch 毫秒空間運算，最後經 sync 換算成影片相對毫秒。
"""
from .clean import clean_chatlog
from .detect import detect_highlights_from_chat

__all__ = ["clean_chatlog", "detect_highlights_from_chat"]
