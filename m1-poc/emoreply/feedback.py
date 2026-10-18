"""T01350：采纳率闭环接回生成（m1-poc 事实源，Android 侧 core/Adaptive.kt 同构镜像）。

来源（T01345 竞品分析）：钉钉把问题案例回流训练形成「生成→评估→优化」闭环；
本项目 FeedbackStore.adoptionRate() 只统计、不反作用于生成——闭环是断的。

三方 LLM 无训练闭环下的等价替代——**推理侧路由**（不改模型，改输入）：
- 按「关系 × tone 桶 × 风格」维度统计采纳率（tone 是连续滑杆值，直接做 key 会
  碎片化，按 20 分一桶收敛到 5 桶）；
- 某维度样本量 ≥ MIN_SAMPLES 且采纳率 < LOW_RATE_THRESHOLD → 该场景本次生成
  自动下调语气一档（TONE_STEP_DOWN）——低分场景不去赌模型当前语气；
- 样本量不足不生效（避免少量样本误判）；用户可关闭（enabled=False）。

TemplateStore 盘点结论（T01345 依赖确认）：模板是用户主动保存的裸文本，无场景/
关系标签、无质量元数据、新装 0 条——直接当低分场景兜底候选会「降级反而更差」，
故本版路由仅做降档；模板参考注入走既有 T01262 favorites 机制，不在本单范围。
"""
import json
import os
import sqlite3
from typing import Dict, Optional, Tuple

# 策略常量（Android core/Adaptive.kt 同值镜像）
MIN_SAMPLES = 10          # 维度样本量下限：不足不生效（防少量样本误判）
LOW_RATE_THRESHOLD = 0.30 # 采纳率低于该值视为「低分场景」
TONE_STEP_DOWN = 10       # 降档步长（滑杆 0-100，一档≈两个 5 步进）
TONE_BUCKET_SIZE = 20     # tone 连续值 → 5 桶（0-20/21-40/.../81-100）


def tone_bucket(tone: int) -> int:
    """tone 连续值 → 统计桶号（0-4）。"""
    return max(0, min(4, int(tone) // TONE_BUCKET_SIZE))


def dim_key(relation: str, tone: int, style: str) -> str:
    """统计维度键：关系|tone桶|风格（relation 先经 tone 归一化口径对齐）。"""
    from .tone import normalize_relation
    return f"{normalize_relation(relation)}|{tone_bucket(tone)}|{style or ''}"


def adapt_tone(relation: str, tone: Optional[int], style: str,
               stats: Dict[str, dict], enabled: bool = True) -> Tuple[Optional[int], bool]:
    """低分场景自动降档。返回 (生效 tone, 是否降档)。

    - enabled=False / tone=None / 样本 < MIN_SAMPLES → 原样返回；
    - 采纳率 < LOW_RATE_THRESHOLD → max(0, tone - TONE_STEP_DOWN)。
    """
    if not enabled or tone is None:
        return tone, False
    entry = stats.get(dim_key(relation, tone, style))
    if not entry:
        return tone, False
    total = entry.get("adopted", 0) + entry.get("ignored", 0)
    if total < MIN_SAMPLES:
        return tone, False
    rate = entry["adopted"] / total
    if rate >= LOW_RATE_THRESHOLD:
        return tone, False
    return max(0, int(tone) - TONE_STEP_DOWN), True


class FeedbackStore:
    """三维采纳统计（单文件 SQLite，零留存：只存计数与维度键，不存正文）。"""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS dim_stats (
        key TEXT PRIMARY KEY,
        adopted INTEGER NOT NULL DEFAULT 0,
        ignored INTEGER NOT NULL DEFAULT 0
    );
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(self._SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def record(self, relation: str, tone: int, style: str, adopted: bool) -> None:
        """记录一次采纳/忽略到对应维度（脱敏：只有维度键与计数）。"""
        key = dim_key(relation, tone, style)
        col = "adopted" if adopted else "ignored"
        self._conn.execute(
            f"INSERT INTO dim_stats(key, {col}) VALUES(?, 1) "
            f"ON CONFLICT(key) DO UPDATE SET {col} = {col} + 1",
            (key,),
        )
        self._conn.commit()

    def dim_stats(self) -> Dict[str, dict]:
        rows = self._conn.execute("SELECT key, adopted, ignored FROM dim_stats").fetchall()
        return {r[0]: {"adopted": r[1], "ignored": r[2]} for r in rows}

    def rate(self, relation: str, tone: int, style: str) -> Optional[float]:
        entry = self.dim_stats().get(dim_key(relation, tone, style))
        if not entry:
            return None
        total = entry["adopted"] + entry["ignored"]
        return entry["adopted"] / total if total else None

    def wipe(self) -> None:
        self._conn.execute("DELETE FROM dim_stats")
        self._conn.commit()

    # ---- 持久化导出（Android SharedPreferences JSON 同构口径）----

    def export_json(self) -> str:
        return json.dumps(self.dim_stats(), ensure_ascii=False)
