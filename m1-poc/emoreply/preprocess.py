"""捕获层预处理：去重 → 噪声过滤 → 指向判定 → 空载荷兜底。

对应 FR-1.4（去重/过滤）、FR-1.3（指向判定）、FR-1.5（内容缺失兜底）。
全部是纯函数，不依赖 Android 运行时——这正是本环境可闭环验证的部分。
"""
from dataclasses import dataclass
import re
import unicodedata
from typing import Callable, List, Optional, Tuple, TYPE_CHECKING

from .bubble_side import (
    CONTENT_MIN_SPAN, RGB, LineBox, classify_sample, judge_line, page_colors_at,
)
from .models import Decision, Notification

if TYPE_CHECKING:  # 避免运行时循环依赖（semantic 不反向依赖 preprocess）
    from .semantic import SemanticJudge

# 重复推送通常紧随其后；窗口压到秒级，避免把同一人快速连发的两条消息误吞
DEDUPE_WINDOW_SECONDS = 2.0

FALLBACK_HINT = "上下文有限：请粘贴消息原文，或基于标题生成泛化建议"


def _merge_target(n: Notification, kept: List[Notification],
                  window: float) -> Optional[Notification]:
    """窗口内找 n 的归并目标：同「平台+会话+发送方」且至少一方正文为空。"""
    for k in reversed(kept):
        if n.timestamp - k.timestamp > window:
            break
        if (k.source, k.chat, k.sender) != (n.source, n.chat, n.sender):
            continue
        if k.body and n.body and k.body != n.body:
            continue
        return k
    return None


def _absorb(merged_into: Notification, n: Notification) -> None:
    """把重复通知 n 的信号并入归并目标（提及/引用取或、补空正文与标题）。"""
    merged_into.mentions_me = merged_into.mentions_me or n.mentions_me
    merged_into.quotes_me = merged_into.quotes_me or n.quotes_me
    if not merged_into.body and n.body:
        merged_into.body = n.body
    if not merged_into.title and n.title:
        merged_into.title = n.title


def dedupe(notifications: List[Notification],
           window: float = DEDUPE_WINDOW_SECONDS) -> List[Notification]:
    """归并重复通知（FR-1.4），输入需按 timestamp 升序。

    判定键是「平台+会话+发送方」而非整条载荷：微信对同一条消息可能先推摘要
    （正文为空）再推详情，只比正文会把一条消息算成两条。窗口内仅当「至少一方
    正文为空」时才归并——双方都有正文且不同时，那就是两条真实消息。
    """
    kept: List[Notification] = []
    for n in notifications:
        target = _merge_target(n, kept, window)
        if target is None:
            kept.append(n)
        else:
            _absorb(target, n)
    return kept


def is_directed_to_me(n: Notification,
                      semantic_judge: Optional["SemanticJudge"] = None) -> bool:
    """判定消息是否指向当前用户（FR-1.3 硬规则 + FR-1.6 语义判定二期扩展）。

    私聊一律视为指向；群聊先看 @提及 与 引用/回复。群聊无 @ 无引用时：
    - semantic_judge 为 None（默认）：按首期硬规则「不指向」——行为零回归；
    - 提供语义判定器（M4 二期）：交由规则信号层 / 可插拔 LLM 层判定，
      压误判优先（SRS 验收 FP ≤5%）。
    """
    if not n.is_group:
        return True
    if n.mentions_me or n.quotes_me:
        return True
    if semantic_judge is not None:
        return semantic_judge.judge(n).points_to_me
    return False


def decide(n: Notification,
           semantic_judge: Optional["SemanticJudge"] = None) -> Decision:
    """对单条（已去重的）通知给出处置判定。"""
    if n.is_system:
        return Decision("drop", "系统消息，非用户间对话（FR-1.4）", n)
    if n.from_self:
        return Decision("drop", "自己发出的消息（FR-1.4）", n)
    if not is_directed_to_me(n, semantic_judge):
        reason = ("群聊未 @ 或引用当前用户（FR-1.3 硬规则）"
                  if semantic_judge is None
                  else "群聊语义判定：不指向当前用户（FR-1.6）")
        return Decision("drop", reason, n)
    if not (n.body or "").strip():
        # FR-1.5：空载荷一律走兜底，禁止用模板冒充真实生成（附录 C-2 C20）
        return Decision("fallback", "通知载荷为空，禁止强行生成（FR-1.5）", n, FALLBACK_HINT)
    return Decision("generate", "指向当前用户且正文可用", n)


def preprocess(notifications: List[Notification],
               semantic_judge: Optional["SemanticJudge"] = None) -> List[Decision]:
    """完整预处理链路：按时间排序 → 去重 → 逐条判定。"""
    ordered = sorted(notifications, key=lambda x: x.timestamp)
    return [decide(n, semantic_judge) for n in dedupe(ordered)]

# ===== T01129：读屏对话侧别解析（串行链路第一步：解析成功才进入生成）=====

SPEAKER_ME = "我"
SPEAKER_OTHER = "对方"
# 读屏行侧别标注前缀（EmoReplyReaderService 输出格式；全角冒号兼容半角）
_SPEAKER_ME_PREFIX = ("我：", "我:")
_SPEAKER_OTHER_PREFIX = ("对方：", "对方:")


def parse_speaker_line(line: str) -> tuple:
    """解析单行读屏文本的发言方与正文。

    「我：xxx」→ (我, xxx)；「对方：xxx」→ (对方, xxx)；
    无前缀行按对方处理（向后兼容 T01124 之前的无标注格式）。
    返回 (sender, body)；body 为 strip 后文本，空行返回 ("", "")。
    """
    text = (line or "").strip()
    if not text:
        return "", ""
    for prefix in _SPEAKER_ME_PREFIX:
        if text.startswith(prefix):
            return SPEAKER_ME, text[len(prefix):].strip()
    for prefix in _SPEAKER_OTHER_PREFIX:
        if text.startswith(prefix):
            return SPEAKER_OTHER, text[len(prefix):].strip()
    return SPEAKER_OTHER, text


# ===== T01343：读屏上下文优化（history 上限 + 时间分段）=====

# history 进 prompt 的条数上限：上下文过长时旧话题内容会混进候选回复
# （用户反馈 2026-09-29：前一小时已完结话题与当前话题生成混杂）。
# 超出只保留最近 N 条（时序不变），配合时间分段做「双保险」。
HISTORY_LIMIT = 12

# 读屏捕获层输出的时间分段标记前缀（EmoReplyReaderService 把纯时间/日期行
# 转成「时间：xxx」标记行，而非静默丢弃——时间行是唯一的话题边界信号）。
TIME_MARK_PREFIX = "时间："


def is_time_mark_line(line: Optional[str]) -> bool:
    """判定读屏行是否为时间分段标记（捕获层转换后的「时间：xxx」前缀行）。"""
    return (line or "").strip().startswith(TIME_MARK_PREFIX)


def split_dialogue_turns(lines: List[str],
                         history_limit: Optional[int] = HISTORY_LIMIT) -> tuple:
    """把带侧别标注的读屏多行文本拆为 (history, current)。

    串行语义：current 取**最后一条对方发言**（我方发言不需要回复）；
    之前全部发言按原始时序作为 history（sender 保留，提示词渲染「我：/对方：」
    使生成感知完整双方上下文）。返回 (history_lines, current_line)，
    每项为 (sender, body) 二元组；无对方发言时 current 为 None（解析失败，
    调用方须给出明确错误提示，不得进入生成）。

    T01343 上下文优化：
    - 时间分段：遇到「时间：」标记行（捕获层把纯时间/日期行转换而来）即开启
      新话题段——此前内容视为已完结话题，整段丢弃，不再回溯作为 current；
      避免跨时段旧话题混入生成上下文（用户实测反馈）。
    - history 上限：history 超过 [history_limit] 条时只保留最近 N 条
      （时序不变）；None 表示不限制。与时间分段互为兜底。
    """
    segments: List[List[tuple]] = [[]]
    for line in lines:
        t = (line or "").strip()
        if not t:
            continue
        if is_time_mark_line(t):
            segments.append([])  # 时间边界：新话题段开始
            continue
        segments[-1].append(parse_speaker_line(t))
    # 取最后一个非空段（时间标记后无内容时回落前段，保持向后兼容）
    seg = next((s for s in reversed(segments) if s), None)
    if not seg:
        return [], None
    last_sender = seg[-1][0]
    if last_sender == SPEAKER_OTHER:
        history, current = seg[:-1], seg[-1]
    else:
        # 末行是我方发言：在**当前段内**向前找最后一条对方发言作为 current；
        # 段内无对方发言时不回溯前段（时间边界前话题已完结）
        idx = next((i for i in range(len(seg) - 1, -1, -1)
                    if seg[i][0] == SPEAKER_OTHER), -1)
        if idx < 0:
            return [], None
        history = seg[:idx] + seg[idx + 1:]
        current = seg[idx]
    if history_limit is not None and len(history) > history_limit:
        history = history[-history_limit:]
    return history, current

# ===== T01134：读屏折行合并（OCR 行级几何，Android 侧同构）=====

# 判定用「行间空白 / 行高」的相对比例，而非绝对像素：绝对像素随屏幕密度与字号漂移
# （SM-G9980 density 3.5 实测折行空白约 30px，旧常量 26px 直接漏判——同一句话被拆成
# 两条独立消息，下游 current 只取到半句，候选回复语义偏移）。
# 同 block 是 ML Kit 已给出的「同一视觉块」信号，可放宽；跨 block 收紧以防误并相邻消息。
WRAP_MERGE_GAP_RATIO_SAME_BLOCK = 1.0
WRAP_MERGE_GAP_RATIO_CROSS_BLOCK = 0.6


@dataclass
class OcrLine:
    """OCR 单行文本及其屏幕坐标（px）；block 为 ML Kit 视觉块序号。"""

    text: str
    left: int
    right: int
    top: int
    bottom: int
    block: int = 0

    @property
    def height(self) -> int:
        return self.bottom - self.top


def _join_wrapped(prev: str, nxt: str) -> str:
    """折行拼接：中文交界直接相连，ASCII 字母数字交界补一个空格（英文断词场景）。"""
    if not prev:
        return nxt
    if not nxt:
        return prev
    if prev[-1].isascii() and prev[-1].isalnum() and nxt[0].isascii() and nxt[0].isalnum():
        return prev + " " + nxt
    return prev + nxt


def merge_wrapped_lines(
    lines: List[OcrLine],
    *,
    same_block_ratio: float = WRAP_MERGE_GAP_RATIO_SAME_BLOCK,
    cross_block_ratio: float = WRAP_MERGE_GAP_RATIO_CROSS_BLOCK,
) -> List[OcrLine]:
    """把「同一气泡内折行」的 OCR 行合并为一条消息（T01134）。

    与上一原始行的行间空白 < 行高 × 比例 → 判为同一消息折行并合并；比例按是否同
    block 取不同值。输入顺序无关（内部按 top 升序），返回新列表、不改动入参。
    """
    ordered = sorted(lines, key=lambda l: l.top)
    out: List[OcrLine] = []
    last: Optional[OcrLine] = None
    for line in ordered:
        if out and last is not None:
            gap = line.top - last.bottom
            line_h = max(line.height, last.height, 1)
            ratio = same_block_ratio if line.block == last.block else cross_block_ratio
            if gap < line_h * ratio:
                merged = out[-1]
                merged.text = _join_wrapped(merged.text, line.text)
                merged.left = min(merged.left, line.left)
                merged.right = max(merged.right, line.right)
                merged.bottom = max(merged.bottom, line.bottom)
                last = line
                continue
        out.append(OcrLine(line.text, line.left, line.right, line.top, line.bottom, line.block))
        last = line
    return out


def split_into_columns(lines: List[str], columns: int = 2) -> List[List[str]]:
    """T01138：把读屏/剪贴板内容按行均分到 N 栏（从左到右顺序依次填充）。

    与 Android `refreshInputPane` 的分栏逻辑同构：前 ceil(n/N) 行进左栏，
    其后依次填右栏——展示态双栏让竖向占用减半，空间利用率提升。
    空行不入栏；栏数大于行数时后续栏为空（调用方按空栏渲染占位）。
    """
    items = [l.strip() for l in lines if (l or "").strip()]
    if columns <= 0:
        return []
    per = (len(items) + columns - 1) // columns
    return [items[i * per:(i + 1) * per] for i in range(columns)]


# ===== 群聊读屏噪声过滤（用户反馈 2026-09-27：群消息解析对象错误）=====

# 微信群聊 UI 悬浮条（「23条新消息」），OCR 读出后被右侧坐标误判为自己消息
_GROUP_NEW_MSG = re.compile(r"\d+\s*条新消息")
# 表情/图标 OCR 误读特征：整行为括号包裹的短内容，如「(田+0)」
_BRACKET_ICON = re.compile(r"^\s*[（(].{0,6}[）)]\s*$")
# 昵称行不含标点（正文行几乎必有）
_HAS_PUNCT = re.compile(r"[，。！？、：；,\.!?:;]")
# 通话类系统提示（居中灰字，非双方消息）——用户反馈「对方已取消」被当成对方消息进入
# 上下文，LLM 误判为「对方撤回消息」而生成错向候选。前导允许 ≤3 个非中文字符：气泡旁的
# 通话图标会被 OCR 误读成单字母（实测「s对方已取消」）；限定非中文可避免误杀真实聊天内容
# （如「订单已取消」）。
_CALL_NOTICE = re.compile(
    r"^[^\u4e00-\u9fa5]{0,3}(对方已取消|已取消|对方已拒绝|已拒绝|对方无应答|无应答|"
    r"对方忙线中|对方忙|已挂断|通话时长.{0,12}|邀请你(语音|视频)通话|"
    r"(语音|视频)通话已取消|(语音|视频)通话中)$"
)


def strip_group_noise(lines: List[str]) -> List[str]:
    """群聊读屏行的噪声过滤（纯文本规则，在侧别标注之前执行）。

    1. 丢弃 UI 悬浮条（「N条新消息」）——它在屏幕右侧，会被侧别判定误标为「我：」；
    2. 丢弃表情/图标误读行（整行括号包裹的短内容，如「(田+0)」）；
    3. 合并发送者昵称行：群聊结构=昵称小字行+气泡正文行，OCR 读出后昵称成为
       独立「对方：xxx」行（对象错误）。特征=短行（≤12字）无标点，且下一行
       以其前 2+ 字开头（「夏思铭妈妈」→ 下行「夏思铭三年级…」）→ 删除昵称行；
    4. 丢弃通话类系统提示（「对方已取消」等）——非双方消息，混入会让生成层误判语境。
    """
    out: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        cur = (lines[i] or "").strip()
        nxt = (lines[i + 1] or "").strip() if i + 1 < n else ""
        if not cur:
            i += 1
            continue
        if _GROUP_NEW_MSG.search(cur) or _BRACKET_ICON.match(cur) or _CALL_NOTICE.match(cur):
            i += 1
            continue
        if (len(cur) <= 12 and nxt and not _HAS_PUNCT.search(cur)
                and nxt.startswith(cur[:2])):
            i += 1
            continue
        out.append(cur)
        i += 1
    return out


# ===== T01334：图片消息区域文字过滤 =====
# 图片消息（读书/视频封面等）的**图内文字会被 ML Kit OCR 读出**，坐标落在左侧，
# 与气泡文字无法区分，被当作「对方发言」混入生成 prompt，污染候选质量。
# 两条路径两套信号（各自用最有把握的那一个）：
#   1) 节点树路径：ImageView 节点的 bounds 就是图片区域（硬证据）→ 落在其中的文本行剔除；
#   2) OCR 路径：拿不到 ImageView，只有像素文本行 → 用「密集堆叠 + 几乎无标点」的
#      行簇特征启发式（图内排版紧凑、文案多为词组分行，与聊天口语带标点差异明显）。


@dataclass
class Region:
    """屏幕矩形区域（px）：节点树路径里的 ImageView 气泡区域。"""

    left: int
    right: int
    top: int
    bottom: int

    def contains_line(self, line: "OcrLine") -> bool:
        """文本行**中心点**落在区域内即判为区域内（避免边缘 1px 相交误杀）。"""
        cx = (line.left + line.right) // 2
        cy = (line.top + line.bottom) // 2
        return self.left <= cx <= self.right and self.top <= cy <= self.bottom


def filter_lines_in_regions(lines: List[OcrLine], regions: List[Region]) -> List[OcrLine]:
    """剔除中心点落在任一图片区域内的行（节点树路径，硬证据）。

    区域为空（或未识别到图片）时原样返回——宁可漏过滤也不误杀真实消息。
    """
    if not regions:
        return list(lines)
    return [ln for ln in lines
            if not any(rg.contains_line(ln) for rg in regions)]


# 环带样本算「图片内容」的通道跨度下限见 bubble_side.CONTENT_MIN_SPAN（像素阈值统一
# 放在事实源里，与 kt 的 BubbleSideJudge 同名常量一一对应）。


def filter_image_text_clusters(
    lines: List[OcrLine],
    *,
    get_rgb=None,
    width: int = 0,
    height: int = 0,
    min_cluster: int = 3,
    gap_ratio: float = 0.35,
    ring_offsets: Tuple[int, ...] = (12, 28, 48),
    content_ratio: float = 0.5,
) -> List[OcrLine]:
    """OCR 路径：剔除「图内文字」行簇（T02305 起改为像素几何判据，与 kt 同构）。

    ①紧凑行簇识别不变：连续 >= [min_cluster] 行、相邻行空白 < 行高 × [gap_ratio]。
    ②剔除判据从「无标点行占比」换成**像素证据**，两条任一命中即整簇剔除：
       · 环带图片内容占比 ≥ [content_ratio]：沿簇并集框四周按 [ring_offsets] 三档取样，
         样本须同时满足「通道跨度 >= CONTENT_MIN_SPAN（饱和色）」且「classify_sample
         判不出气泡底色」——照片、App 界面色块、品牌绿表头属此类；
       · 或整簇零气泡证据（[_cluster_without_bubble]）：每行 30 点环带绿票白票双 0，
         说明这些行根本不在气泡上——灰阶拍屏截图（跨度只有 5，逃过上一条）靠这个抓。
    ③必须在 [merge_wrapped_lines] **之前**调用（T02305 真机订正）：折行合并把整段并成
      一行，行高累加后 `gap < 高度 × gap_ratio` 再也不成立，簇散架 → 判据整条失效。

    为什么必须换：银行/表单类截图正文天生带冒号逗号（键值对排版），无标点行占比不达
    0.8，整簇漏网后进入判侧，截图自己的品牌绿又被当成我说气泡，输出伪造「我：」。
    用文本内容反推「是否图内」在这类截图上先天不成立，判据只能来自像素。

    两个「只看作废样本」的坑（判据刻意避开，各有对应用例锁住）：
    · 用户设了聊天背景图时页底可能比 #F2F2F2 气泡更亮，正常气泡的行簇环带会全判不出
      底色 —— 所以先用跨度下限把近中性的页底/白气泡挡在外面；
    · 微信我说气泡本身就是饱和绿，多行「我」的消息环带跨度远超阈值 —— 所以再用
      classify_sample 的 MINE（锚点绿）反向排除真气泡。

    [get_rgb] 为空（纯文本回放、拿不到像素）时**不做任何剔除**——绝不退回旧的词法判据。
    """
    if get_rgb is None or width <= 0 or height <= 0:
        return list(lines)
    ordered = sorted(lines, key=lambda l: l.top)
    out: List[OcrLine] = []
    i = 0
    n = len(ordered)
    while i < n:
        j = i
        while j + 1 < n:
            gap = ordered[j + 1].top - ordered[j].bottom
            h = max(ordered[j].height, ordered[j + 1].height, 1)
            if gap < h * gap_ratio:
                j += 1
            else:
                break
        cluster = ordered[i:j + 1]
        if len(cluster) >= min_cluster and (
            _ring_content_ratio(cluster, get_rgb, width, height, ring_offsets) >= content_ratio
            or _cluster_without_bubble(cluster, get_rgb, width, height)
        ):
            i = j + 1  # 整簇判为图内文字，剔除
            continue
        out.append(ordered[i])
        i += 1
    return out


def _ring_content_ratio(
    cluster: List[OcrLine],
    get_rgb: Callable[[int, int], RGB],
    width: int,
    height: int,
    offsets: Tuple[int, ...],
) -> float:
    """簇并集框四周环带上的「图片内容」样本占比。"""
    left = min(l.left for l in cluster)
    right = max(l.right for l in cluster)
    top = min(l.top for l in cluster)
    bottom = max(l.bottom for l in cluster)
    page_cache: dict = {}
    hits = total = 0

    def page_at(y: int) -> Tuple[Optional[RGB], Optional[RGB]]:
        bucket = y // 16  # 页底沿 y 缓变（渐变背景图），按 16px 分桶复用
        if bucket not in page_cache:
            page_cache[bucket] = page_colors_at(get_rgb, y, width, height)
        return page_cache[bucket]

    for d in offsets:
        for f in (0.25, 0.5, 0.75):
            x = left + int((right - left) * f)
            y = top + int((bottom - top) * f)
            for px, py in ((x, top - d), (x, bottom + d), (left - d, y), (right + d, y)):
                if not (0 <= px < width and 0 <= py < height):
                    continue
                total += 1
                rgb = get_rgb(px, py)
                if max(rgb) - min(rgb) < CONTENT_MIN_SPAN:
                    continue          # 近中性：页底、白/灰气泡、字形都归此类
                pl, pr = page_at(py)
                if classify_sample(rgb, pl, pr) is None:
                    hits += 1         # 饱和且判不出气泡底色 → 图片内容
    return (hits / total) if total else 0.0


def _cluster_without_bubble(
    cluster: List[OcrLine],
    get_rgb: Callable[[int, int], RGB],
    width: int,
    height: int,
    *,
    lum_spread_min: int = 40,
) -> bool:
    """整簇「零气泡证据 + 环带是带纹理的中性灰」→ 判为图内文字。

    为什么需要第二条判据：[_ring_content_ratio] 用「饱和色」把字形与页底挡在外面，
    代价是**灰阶拍屏照片**整块环带都被跨度下限放过（真机 10-17 史文豪会话那三张
    银行界面截图，样本众数 (185,186,181)、单样本跨度仅 5）→ 占比 0 → 漏网。

    两个条件缺一不可，各自挡掉一类误杀：
    · 每行 30 点环带绿票白票双 0 —— 真气泡（白/绿/深色）必然给得出票数；
    · 环带样本近中性但亮度极差 ≥ [lum_spread_min] —— 挡住「#F2F2F2 气泡坐在亮聊天
      背景图上」这种同样零票数却完全平坦的场景（实测平坦气泡极差 0~5、
      灰阶照片 49~108，阈值 40 两侧都有余量）。

    只在 ≥3 行的紧凑簇上生效，单行（群聊昵称行、时间行、悬浮条）一律不剔。
    """
    if not all(
        judge_line(get_rgb, LineBox(l.left, l.right, l.top, l.bottom), width,
                   height)[1:] == (0, 0)
        for l in cluster
    ):
        return False
    left = min(l.left for l in cluster)
    right = max(l.right for l in cluster)
    top = min(l.top for l in cluster)
    bottom = max(l.bottom for l in cluster)
    lums: List[int] = []
    for d in (12, 28, 48):
        for f in (0.25, 0.5, 0.75):
            x = left + int((right - left) * f)
            y = top + int((bottom - top) * f)
            for px_, py_ in ((x, top - d), (x, bottom + d), (left - d, y), (right + d, y)):
                if not (0 <= px_ < width and 0 <= py_ < height):
                    continue
                rgb = get_rgb(px_, py_)
                if max(rgb) - min(rgb) >= CONTENT_MIN_SPAN:
                    return False  # 有饱和样本：不是「灰阶照片」这一型，交给环带占比判据
                lums.append(sum(rgb) // 3)
    return bool(lums) and max(lums) - min(lums) >= lum_spread_min


# T01339 复验反馈：视频/图片等非纯文本不应纳入识别——OCR 噪声行过滤（与 kt 同构）。
# ①媒体占位符（节点树/OCR 均可能出现，零信息量）；②右下角叠标行（群成员+/点赞+1
# 等贴在图片视频上的角标 OCR 残留，如「1止部+」「E個+」）；③符号乱码行（Unicode
# Symbol 类占比过高，emoji 范围视为合法不算噪声）。保守优先：不满足判据一律保留。
_MEDIA_PLACEHOLDER = re.compile(r'^\s*[【\[]\s*(图片|视频|动画表情|文件|语音|链接|名片|音乐|位置|红包|转账)\s*[】\]]\s*$')
_OVERLAY_BADGE = re.compile(r'^[\dA-Za-z\u4e00-\u9fff]{1,6}\s*[+＋]$')
_EMOJI_RANGES = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2190, 0x21FF), (0x2B00, 0x2BFF))


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


def _symbol_noise_ratio(text: str) -> float:
    """Unicode Symbol 类（S*）且非 emoji 的字符占比（按去空白后的长度）。"""
    chars = [ch for ch in text if not ch.isspace()]
    if not chars:
        return 0.0
    noise = sum(1 for ch in chars
                if unicodedata.category(ch).startswith('S') and not _is_emoji(ch))
    return noise / len(chars)


# T01554 同构：连续 3+ 个相同字符（去空白后）
_CONSECUTIVE_REPEAT = re.compile(r'(.)\1{2,}')


def _is_meaningful_char(ch: str) -> bool:
    """字母/数字/汉字/emoji 视为「有意义字符」（与 kt isGarbledText 同口径）。"""
    return ch.isalnum() or '\u4e00' <= ch <= '\u9fff' or _is_emoji(ch)


def is_garbled_text(text: Optional[str]) -> bool:
    """T01554：乱码文本检测——识别 OCR 误读产生的无效文本（与 kt isGarbledText 同构）。

    四条判据任一命中即视为乱码：
    1. 含 Unicode 控制字符（Cc 区，排除常见空白 \\t\\n\\r）；
    2. 有意义字符（字母/数字/汉字/emoji）占非空白字符比例 < 70%；
    3. 连续 3+ 个相同「非字母数字、非 emoji、非汉字」字符（如「■■■」「???」）；
    4. 纯符号行：无任何字母/数字/汉字/emoji 且长度 > 2。

    注意：这是**整行丢弃**的重判据，宁可漏杀也不误杀真实短消息——
    中文聊天里「？？？」「!!!」这类重复标点本就是无效噪声，而「好好好」等叠字
    由判据 3 的「非字母数字」排除项保护（汉字重复不算乱码）。
    """
    t = (text or '').strip()
    if not t:
        return True

    # 1. 控制字符（排除 \t \n \r）
    if any(unicodedata.category(ch) == 'Cc' and ch not in '\t\n\r' for ch in t):
        return True

    # 2. 有意义字符占比
    non_space = [ch for ch in t if not ch.isspace()]
    if non_space:
        meaningful = sum(1 for ch in non_space if _is_meaningful_char(ch))
        if meaningful / len(non_space) < 0.7:
            return True

    # 3. 连续重复的非文字符号
    compact = re.sub(r'\s', '', t)
    for m in _CONSECUTIVE_REPEAT.finditer(compact):
        if not _is_meaningful_char(m.group(1)):
            return True

    # 4. 纯符号行
    if len(t) > 2 and not any(_is_meaningful_char(ch) for ch in t):
        return True

    return False


def filter_non_text_noise(lines: List[OcrLine], *, symbol_ratio: float = 0.34) -> List[OcrLine]:
    """剔除媒体类噪声行：占位符 / 角标叠字 / 符号乱码（真实短消息不受影响）。

    T01554：在符号占比判据前先跑 is_garbled_text 四重判据——OCR 路径偶发的
    控制字符、纯符号行、连续乱码符号会被提前丢弃，不再依赖单一的比例阈值。
    """
    out = []
    for ln in lines:
        t = ln.text.strip()
        if not t or _MEDIA_PLACEHOLDER.match(t) or _OVERLAY_BADGE.match(t):
            continue
        if is_garbled_text(t):
            continue
        if _symbol_noise_ratio(t) >= symbol_ratio:
            continue
        out.append(ln)
    return out


def merge_screen_batches(batches: List[List[str]]) -> List[str]:
    """T01145：多屏读屏结果合并（事实源，与 kt mergeScreenBatches 同构）。

    读屏扩展为「当前屏 + 上滑 N 屏」后，各批次的 OCR 行按时间先后顺序拼接，
    相邻批次间的重叠行（滚动重叠区/同屏残留）按整行文本去重——保留首次出现
    位置，时序不变。输入行可含侧别标注前缀（「我：/对方：」），按整行去重。
    """
    out: List[str] = []
    seen = set()
    for batch in batches:
        for line in batch:
            t = (line or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out
