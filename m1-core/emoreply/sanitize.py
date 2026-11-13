"""候选回复的输出清洗。

SRS 附录 C 的两条实测约束：
- C3：LLM 输出的 markdown 格式符（**、*、链接）直接注入聊天框会变成噪声正文；
- C25：注入文本须剥离 HTML/标记，防标记污染（曾把用户输入包成 <p>…</p> 原样展示）。

清洗只剥离格式符号、不改写文字——改写会触发 C10 的语义丢失风险。

⚠ 安全侧约定（SonarQube python:S5852）：候选文本来自外部 LLM 输出，正则里含
  「量化的闭包类 + 后继限定」（如 ``[\\s|]+\\Z``、``(.+?)…``、``[^）)]*[）)]``）时，
  构造串会把回溯拖成指数级。被点名的五条模式改为显式线性扫描——与原正则
  逐字符等价（差分模糊测试 4.6 万条语料零差异），成本随输入长度线性。
  与 desktop-app/sanitize.js 的同构约定不变：行为一致，实现可以不同。
"""
import re
import unicodedata
from typing import Optional

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1", re.S)
_CODE_FENCE = re.compile(r"```[a-zA-Z0-9]*\n?")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)
_LIST_MARK = re.compile(r"^[ \t]{0,3}(?:[-*+]|\d+\.)[ \t]+", re.M)
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_STRAY_MARK = re.compile(r"(?<!\w)[*_~]{1,3}(?!\w)")
_BLANK_RUN = re.compile(r"\n{3,}")
# T01554：含中日韩汉字的判定（在 CJK 统一表意文字区内）
_CJK = re.compile(r"[\u4e00-\u9fff]")

# ===== 线性扫描用的字符口径（与被替换的正则字符类严格对齐）=====
_COLONS = (":", "：")
_PAREN_OPEN = ("（", "(")
_PAREN_CLOSE = ("）", ")")
_SPACE_TAB_IDEO = (" ", "\t", "\u3000")  # `[ \t　]`：显式三类，不含换行
_MD_MODIFIERS = " \t#*_>"  # `[ \t#*_>]`
_BULLETS = "-*+•"  # `[-*+•]`


def _is_digit(ch: str) -> bool:
    """正则 `\\d`（str 模式）= Unicode 十进制数字（Nd 类），不等于 str.isdigit()。"""
    return unicodedata.category(ch) == "Nd"


def _at(s: str, k: int) -> str:
    """取 s[k]（越界返回空串，便于直接与字符组做 in 判定）。"""
    return s[k:k + 1]


def _skip_space(s: str, k: int) -> int:
    """正则 `\\s*` 的跳过终点（Python 的 `\\s` 与 str.isspace() 同集）。"""
    n = len(s)
    while k < n and s[k].isspace():
        k += 1
    return k


def _skip_chars(s: str, k: int, chars) -> int:
    n = len(s)
    while k < n and s[k] in chars:
        k += 1
    return k


def _scan_digits(s: str, k: int) -> int:
    """正则 `\\d+`：返回数字段终点，一个都没有则 -1。"""
    n = len(s)
    j = k
    while j < n and _is_digit(s[j]):
        j += 1
    return j if j > k else -1


def _index_of_any(s: str, k: int, chars) -> int:
    for i in range(k, len(s)):
        if s[i] in chars:
            return i
    return -1


def _match_word_at(s: str, k: int, words) -> int:
    for w in words:
        if s.startswith(w, k):
            return len(w)
    return -1


def _match_word_fold(s: str, k: int, words) -> int:
    """忽略大小写的关键词匹配（原正则带 re.I；中文关键词不受影响）。"""
    for w in words:
        if s[k:k + len(w)].lower() == w.lower():
            return len(w)
    return -1


def _trim_trailing_pipe(s: str) -> str:
    """T01131 尾部悬空管道符（原 `[\\s|]+\\Z`）：剥掉尾部空白/竖线的极大后缀。"""
    i = len(s)
    while i > 0 and (s[i - 1] == "|" or s[i - 1].isspace()):
        i -= 1
    return s[:i]


def strip_markdown(text: str) -> str:
    """剥离 markdown 格式符，保留文字内容（C3）。"""
    out = _CODE_FENCE.sub("", text)
    out = _MD_LINK.sub(r"\1", out)
    out = _INLINE_CODE.sub(r"\1", out)
    out = _EMPHASIS.sub(r"\2", out)
    out = _HEADING.sub("", out)
    out = _LIST_MARK.sub("", out)
    out = _STRAY_MARK.sub("", out)
    return out


def strip_markup(text: str) -> str:
    """剥离 HTML/标记（C25）。"""
    return _HTML_TAG.sub("", text)


def clean_candidate(text: str) -> str:
    """注入输入框前的最终清洗。"""
    out = strip_markup(strip_markdown(text))
    out = _BLANK_RUN.sub("\n\n", out)
    out = _trim_trailing_pipe(out)
    return out.strip()


def _split_on_pipe(raw: str):
    """「正文|理由」分支：无 | 或切不出可用正文时返回 None（交回调用方走兜底）。"""
    if "|" not in raw:
        return None
    text, rationale = raw.rsplit("|", 1)
    text, rationale = text.strip(), rationale.strip()
    if text and rationale:
        # T01554：分隔符错位检测——text ≤3 字符而 rationale >10 字符且含中文，
        # 疑似「|」位置错（LLM 把短确认词放前面），交换两者避免解释句被当候选。
        if len(text) <= 3 and len(rationale) > 10 and _CJK.search(rationale):
            return _strip_option_prefix(clean_candidate(rationale)), text
        # 先剥 markdown 再剥标签前缀：前缀正则含 bullet [-*+•]，对未清洗文本会误剥 markdown 星号
        return _strip_option_prefix(clean_candidate(text)), rationale
    if text:
        # T01131 理由缺失时的行尾悬空管道符：剥掉该符号、只保留正文；
        # 不得把整段原文落入兜底当候选（真机实测：旅行时光一句的句尾残留了悬空管道符）
        return _strip_option_prefix(clean_candidate(text)), None
    return None


def split_rationale(raw: str) -> tuple:
    """FR-3.3：解析 LLM 候选行的「内容|理由」格式，理由缺失返回 None。

    生成层约定每条候选输出为「一句话内容|一句解释为什么这样回」；
    按最后一个 | 切分，内容本身含 | 时不受影响。清洗只作用于内容，
    理由仅作展示、不注入输入框。

    T01126：无 | 时兜底「正文 理由：xxx」内联结构（LLM 忘带管道符的常见变体）。
    """
    raw = (raw or "").strip()
    if not raw:
        return "", None
    piped = _split_on_pipe(raw)
    if piped is not None:
        return piped
    inline = _match_inline_rationale(raw)
    if inline:
        text, rationale = inline[0].strip(), inline[1].strip()
        if text and rationale:
            return _strip_option_prefix(clean_candidate(text)), rationale
    # T01554：启发式拆分——LLM 连「|」都忘记且内联结构未被上面的正则命中时的最后兜底。
    # 见 _heuristic_rationale_split 注释：本规则有截断真实候选的风险，故阈值收紧。
    split = _heuristic_rationale_split(raw)
    if split:
        return split
    return clean_candidate(raw), None


# T01554 启发式：候选正文里混入理由关键词（「好的 理由：这个建议很合理」）。
# 相比 kt 原实现做了两点收紧——原实现的「说明」弱词 + 关键词后不要求冒号，会把
# 「好的，我说明一下情况」误砍成候选「好的，我」+理由「说明一下情况」；把一句完整的话
# 拦腰截断比原来的「理由混入候选」更糟，故：
#   1) 关键词只留强标签词（理由/原因/解释/rationale/explanation），去掉口语动词「说明」；
#   2) 关键词后必须紧跟冒号（：/:），排除「说明一下」「说明书」这类句中用法。
_RATIONALE_SPLIT_KEYWORDS = ("理由", "原因", "解释", "rationale", "explanation")
_RATIONALE_KEYWORD_FORMS = {
    kw: re.compile(re.escape(kw) + r"\s*[:：]", re.I) for kw in _RATIONALE_SPLIT_KEYWORDS
}


def _heuristic_rationale_split(s: str) -> Optional[tuple]:
    """T01554：把「短候选 + 理由：长解释」按理由关键词切开。宁缺毋滥，不合规则返回 None。"""
    for kw, pat in _RATIONALE_KEYWORD_FORMS.items():
        m = pat.search(s)
        if not m or m.start() <= 0:
            continue
        text = s[: m.start()].strip(" ：:。，,.")
        tail = s[m.start():]
        # 长度判定用「含标签的原文」（判断后面是否真有一段解释），
        # 返回值剥掉「理由：」/「rationale: 」前缀——与中文 INLINE_RATIONALE 的 group(2)
        # 口径一致，UI 展示理由时无需再剥一次标签词。
        if text and len(text) <= 20 and len(tail) > 5:
            rationale = tail[m.end() - m.start():].strip(" ：:")
            return _strip_option_prefix(clean_candidate(text)), rationale
    return None


# T01081：兼容服务不支持 n>1 时，LLM 常把多条候选挤在同一段输出——
# 「选项1：xxx|理由1 选项2：yyy|理由2」或逐行「1. xxx」。
# 不拆开的话，用户点任一候选都会把全部选项连同解释一起注入聊天框（发送事故）。
_NUMBERED_WORDS = ("选项", "候选", "方案")
_NUMBERED_STOPS = ("：", ":", ".", "．")  # `[：:.．]`
_LINE_BULLET_PREFIX = re.compile(r"^[ \t　]*(?:[-*+•]|\d+[.、)]|[（(]\d+[)）])\s*")


def _match_numbered_run(s: str, k: int) -> int:
    """编号前缀 `(?:选项|候选|方案)\\s*\\d+\\s*(?:[（(][^）)]*[）)])?\\s*[：:.．]` 的终点，失败 -1。

    回溯路径已穷举（与 desktop-app/sanitize.js 同一份推理）：`\\d+` 回退一位后后继仍是
    数字，既不是开括号也不是终止符；括号组里 `[^）)]` 匹配不了闭括号，只能停在首个闭括号；
    整体跳过括号组则必需的 `[：:.．]` 落在开括号上必然失败——全模式只有单一线性解。
    """
    word = _match_word_at(s, k, _NUMBERED_WORDS)
    if word < 0:
        return -1
    digits = _scan_digits(s, _skip_space(s, k + word))
    if digits < 0:
        return -1
    after_digits = _skip_space(s, digits)
    if _at(s, after_digits) in _PAREN_OPEN:
        close = _index_of_any(s, after_digits + 1, _PAREN_CLOSE)
        if close < 0:
            return -1
        after_paren = _skip_space(s, close + 1)
        return after_paren + 1 if _at(s, after_paren) in _NUMBERED_STOPS else -1
    return after_digits + 1 if _at(s, after_digits) in _NUMBERED_STOPS else -1


def _find_numbered_start(s: str, frm: int) -> int:
    """原 `(?:^|\\s)` 锚点：从 frm 起找下一处编号前缀的关键词起点，未找到返回 -1。"""
    for p in range(max(frm, 0), len(s)):
        if p > 0 and not s[p - 1].isspace():
            continue
        if _match_word_at(s, p, _NUMBERED_WORDS) > 0 and _match_numbered_run(s, p) >= 0:
            return p
    return -1


def _split_by_numbered(s: str) -> list:
    """原 `_NUMBERED.split(s)` 的线性等价：命中段整体丢弃，返回切片（空段由调用方过滤）。"""
    parts = []
    i = 0
    start = _find_numbered_start(s, 0)
    while start >= 0:
        parts.append(s[i:start])
        i = _match_numbered_run(s, start)
        start = _find_numbered_start(s, i)
    parts.append(s[i:])
    return parts


def _strip_numbered_prefix(line: str) -> str:
    """行首编号前缀（原 `^[ \\t　]*(?:选项|候选|方案)…[：:.．]\\s*`）的线性等价。"""
    end = _match_numbered_run(line, _skip_chars(line, 0, _SPACE_TAB_IDEO))
    return line if end < 0 else line[_skip_space(line, end):]

# T01126：LLM 把结构化标题混进候选（「## 候选回复」「理由：xxx」）——
# 标题行整行丢弃、标签前缀剥离，防止点选把标题带进输入框（快速点选场景）。
# T02224：「内容」也是标签词——提示词契约本身就叫「内容|理由」，模型会把字段名当行首
# 标签吐回来（真机实测第 2 条候选是「内容：对方回复较为简短，直接点出未收到消息的事实…」），
# 不剥则字段名跟着注入聊天框。长词在前保证「回复内容」优先于「内容」整体匹配。
_LABEL_WORDS = r"(?:候选回复|参考回复|回复内容|内容|回复|候选|理由|原因|解释)"
_LABEL_LINE = re.compile(rf"^[ \t#*_>]*{_LABEL_WORDS}\s*[:：]?\s*[#*_\s]*$")
_LABEL_PREFIX = re.compile(rf"^[ \t#*_]*{_LABEL_WORDS}\s*[:：]\s*")
_INLINE_RATIONALE_WORDS = ("理由", "原因", "解释")


def _match_inline_rationale(s: str):
    """原 `^(.+?)\\s*(?:理由|原因|解释)\\s*[:：]\\s*(.+)$`（re.S）的线性等价，返回 (正文, 理由) 或 None。

    lazy 头部逐字扩展 = 「按位置升序尝试每个关键词起点，取第一个整体成功的」；
    关键词起点必须 ≥1（`(.+?)` 至少吃一个字符）。两类失败的处理不同，与引擎一致：
    `\\s*[:：]` 取不到冒号 → 该位置整体失败，继续试更晚的关键词；
    冒号命中但后面只剩空白 → `\\s*` 回退让出最后一个字符，匹配仍成立（理由为空），
    由调用方 trim 后决定是否回落。
    """
    n = len(s)
    positions = []
    for w in _INLINE_RATIONALE_WORDS:
        p = s.find(w, 1)
        while p >= 0:
            positions.append((p, len(w)))
            p = s.find(w, p + 1)
    positions.sort()
    for p, word_len in positions:
        before_colon = _skip_space(s, p + word_len)
        if _at(s, before_colon) not in _COLONS:
            continue
        after_colon = before_colon + 1
        if after_colon >= n:
            continue
        k = after_colon
        while k < n - 1 and s[k].isspace():
            k += 1
        return s[:p], s[k:]
    return None


def _strip_option_prefix(line: str) -> str:
    """剥离单候选行的「选项N：/候选N:/1./-/•/（1）」及「候选回复：/理由：」前缀。"""
    line = _LABEL_PREFIX.sub("", line, count=1)
    line = _strip_numbered_prefix(line)
    return _LINE_BULLET_PREFIX.sub("", line, count=1)


def _is_label_line(line: str) -> bool:
    """整行只是结构标签（「## 候选回复」「理由：」）→ 该行丢弃（T01126）。"""
    return bool(_LABEL_LINE.match(line.strip()))


# 悬空管道符：行尾的 |（「内容|」）或行首的 |（「|理由」）。
# 全/半角空格一并容忍——LLM 常在管道符后补一个空格。
_DANGLING_PIPE_TAIL = re.compile(r"\|[ \t\u3000]*$")
_LEADING_PIPE = re.compile(r"^[ \t\u3000]*\|")

# T01720：LLM 把理由换行输出（「1. 候选正文」+ 换行「理由：xxx」续行）——
# 逐行拆会把续行经 _strip_option_prefix 剥掉「理由：」前缀后当独立候选
# （真机实测「候选 6 条」= 3 真候选 + 3 理由行）。理由续行的强特征 =
# 行首（允许 markdown/缩进/bullet 修饰）强标签词 + 冒号，与 T01554 启发式同词表。
# 常规候选正文不会以此开头（「原因很简单：…」中 原因 后不是冒号，不命中）。
_RATIONALE_LINE_WORDS = ("理由", "原因", "解释", "rationale", "explanation")


def _rationale_start_candidates(s: str) -> list:
    """按引擎回溯顺序产出关键词可能起点：修饰符前缀由长到短，可选 bullet 先吞一个空白再放回。"""
    positions = []
    prefix_end = _skip_chars(s, 0, _MD_MODIFIERS)
    for k in range(prefix_end, -1, -1):
        if _at(s, k) in _BULLETS:
            if _at(s, k + 1) in _SPACE_TAB_IDEO:
                positions.append(k + 2)
            positions.append(k + 1)
        positions.append(k)
    return positions


def _match_rationale_at(s: str, p: int):
    """位置 p 起「关键词 + 冒号 + 正文」；任一环节不成立返回 None。"""
    word = _match_word_fold(s, p, _RATIONALE_LINE_WORDS)
    if word < 0:
        return None
    before_colon = _skip_space(s, p + word)
    if _at(s, before_colon) not in _COLONS:
        return None
    after_colon = before_colon + 1
    n = len(s)
    if after_colon >= n:
        return None
    k = after_colon
    while k < n - 1 and s[k].isspace():
        k += 1
    return s[k:].strip()


def _find_rationale_line_head(s: str) -> Optional[str]:
    """行首「理由：xxx」标签行的理由正文（原 `_RATIONALE_LINE.match(...).group(1).strip()`）。

    `*` 同属修饰符类与 bullet 类，「`***　理由：`」存在让回一个 `*` 当 bullet 的解，
    故按候选起点顺序逐个尝试（与 desktop-app/sanitize.js 同一份枚举）。
    冒号后只剩空白时 `(.+)$` 靠 `\\s*` 让出最后一个字符仍算命中（trim 后为空），
    冒号后什么都没有才不命中。
    """
    for p in _rationale_start_candidates(s):
        hit = _match_rationale_at(s, p)
        if hit is not None:
            return hit
    return None


def _merge_rationale_lines(lines: list) -> list:
    """T01720：把「理由：xxx」续行接回上一条候选行，理由行自身不产出候选。

    - 常规：上一行补 |（「正文」+「理由：xxx」→「正文|xxx」），交给 split_rationale 统一解析；
    - 上一行以悬空管道符收尾（「正文|」）：直接续接理由内容，不重复补管道符；
    - 上一行已含 |（已接过度理由）：用「；」续接进理由段，避免 rsplit 切错位；
    - 无上一行（理由行在最前 / 全部是理由行）→ 丢弃。
    """
    out = []
    for ln in lines:
        rationale = _find_rationale_line_head(ln)
        if rationale is not None:
            if out and rationale:
                if _DANGLING_PIPE_TAIL.search(out[-1]):
                    out[-1] = out[-1] + rationale
                elif "|" in out[-1]:
                    out[-1] = out[-1] + "；" + rationale
                else:
                    out[-1] = out[-1] + "|" + rationale
            continue
        out.append(ln)
    return out


def _join_dangling_pipe(lines: list) -> list:
    """把被换行拆开的「内容|理由」重新接回一行。

    LLM 会把理由换到下一行（「内容|」+换行+「理由」，或「内容」+换行+「|理由」），
    逐行拆会产出两条独立候选：前一条丢理由、后一条把理由当正文（真机实测候选 3
    无理由、候选 4 恰是候选 3 的理由），违反 FR-3.3「≥90% 候选附解释」。
    管道符是「这两行本属同一条」的显式标记，据此合并；无此标记时不做任何合并，
    以免误并两条真实候选。
    """
    out = []
    for ln in lines:
        if out and (_DANGLING_PIPE_TAIL.search(out[-1]) or _LEADING_PIPE.search(ln)):
            out[-1] = out[-1] + ln
        else:
            out.append(ln)
    return out


def expand_candidates(raw: Optional[str]) -> list:
    """把一条 LLM 输出拆成单候选行列表（只拆结构，不改写文字）。

    - 多行输出：逐行拆（每行一条候选，LLM 逐行列候选的常见格式）；
    - 单行但含内联编号（「选项1：… 选项2：…」）：按编号拆；
    - 其余：原样单条返回，交由 split_rationale 继续处理。

    T01126：标题行（「## 候选回复」「理由：」）整行丢弃，不作为候选。
    """
    s = (raw or "").strip()
    if not s:
        return []
    lines = [ln.strip() for ln in s.splitlines() if ln.strip() and not _is_label_line(ln)]
    lines = _merge_rationale_lines(lines)  # T01720：理由续行接回上一条候选，不产出独立候选
    if len(lines) > 1:
        return [_strip_option_prefix(ln) for ln in _join_dangling_pipe(lines)]
    if _find_numbered_start(s, 0) >= 0:
        parts = [p.strip() for p in _split_by_numbered(s) if p.strip()]
        if len(parts) > 1:
            return [_strip_option_prefix(p) for p in parts]
    if lines:
        return [_strip_option_prefix(lines[0])]
    # T01126：全部行都是结构标签（LLM 只输出标题没正文）→ 无候选
    return []


def _has_rationale_mark(line: str) -> bool:
    """行内是否已带理由结构标记：管道分隔、「… 理由：…」内联、「理由：」标签行。"""
    return ("|" in line or _match_inline_rationale(line) is not None
            or _find_rationale_line_head(line) is not None)


def pair_unmarked_reasons(lines: list, expected: int) -> list:
    """T02224 根治「理由栏被识别成候选回复」：用数量契约取代词表补丁。

    真机现场（同一屏抓到的 LLM 原文）：模型把「内容|理由」的分隔符整个丢掉，候选与理由
    退化成无标记的交替纯文本行——4 行 = 2 候选 + 2 理由，界面出「候选 4 条」，
    用户点第 2 条会把「语气简洁明了，直接推进下一步」这类分析语注入聊天输入框。
    历次修复（T01126/T01136/T01554/T01720）都在扩理由行的词表，但词表只能认出
    **带标签**的理由行；无标签时它必然漏——这是补丁反复复发的根因。

    这里改用一个模型破坏不了的约束：候选条数 N 在请求时就定死了（生成器 n / 提示词
    「两个候选」）。展开行数 > N 且全行无任何理由标记时，按鸽笼原理多出来的行不可能
    都是候选，只能是「正文, 理由」相邻成对——按序配成「正文|理由」交回 split_rationale，
    再按 N 截断。条件不满足（N 未知、行数 ≤ N、行数为奇、已有标记）时原样返回，
    仍走词表路径：宁可不配，也不把两条真候选误吞成理由。

    与 kt `Sanitize.pairUnmarkedReasons` / desktop `pairUnmarkedReasons` 同构。
    """
    if expected <= 0 or len(lines) <= expected or len(lines) % 2 != 0:
        return lines
    if any(_has_rationale_mark(ln) for ln in lines):
        return lines
    pairs = list(zip(lines[::2], lines[1::2]))[:expected]
    return [f"{text}|{reason}" for text, reason in pairs]

# ===== T01158 FR-7.1：换一批去重（与 kt Sanitize 同构，算法一致便于对齐）=====
# 判定用「归一化文本 + LCS 相似度」：表情/标点/空白差异不算差异，
# 避免「好呀 😊」与「好呀~」被判成两条新候选（真机点换一批的直观感受就是没换）。
_PUNCT = re.compile(r"[\s，。！？、；：,.!?;:~～\-—…\"'()（）\[\]【】]")


def normalize_for_compare(text: str) -> str:
    """比较用归一化：去表情、去标点空白、统一小写。"""
    out = _EMOJI_UNIT.sub("", text or "")
    return _PUNCT.sub("", out).lower()


def similarity(a: str, b: str) -> float:
    """LCS 比率相似度 = 2*LCS / (len(a)+len(b))（与 kt 同算法）。

    候选文本很短（<200 字），O(n*m) 完全可接受，换来 kt/py 结果完全一致。
    """
    a, b = normalize_for_compare(a), normalize_for_compare(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, start=1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    lcs = prev[-1]
    return 2.0 * lcs / (len(a) + len(b))


def is_duplicate(text: str, exclude: list, threshold: float = 0.8) -> bool:
    """候选是否与已给候选重复（换一批过滤用）。"""
    if not text:
        return True
    return any(similarity(text, e) >= threshold for e in (exclude or []))


def filter_duplicates(candidates: list, exclude: list, threshold: float = 0.8) -> list:
    """丢弃与 exclude 重复的候选；exclude 为空时原样返回。"""
    if not exclude:
        return list(candidates)
    return [c for c in candidates if not is_duplicate(c, exclude, threshold)]


# ===== T01159：表情（emoji）处理 =====
# 表情是正文的一部分（不属 markdown/markup 噪声，C3/C25 清洗不得动它）；
# 但 LLM 常见「🎉🎉🎉」堆砌，注入聊天框会显得廉价——只做收敛，不改写文字。
_MOD = "\U0001F3FB-\U0001F3FF\uFE0F\u20E3"  # 肤色/变体选择符/keycap
_ZWJ = "\u200D"  # 零宽连接符（👨‍👩‍👧 这类组合表情）
_BASE = (
    "\U0001F000-\U0001FAFF"  # 表情主体（含补充符号与图形）
    "\U0001F1E6-\U0001F1FF"  # 区域指示符（国旗）
    "\u2600-\u27BF\u2B00-\u2BFF"  # 杂项/装饰符号、箭头与几何图形
    "\u203C\u2049\u2122\u2139\u2194-\u2199\u21A9\u21AA"
    "\u231A\u231B\u2328\u23CF\u23E9-\u23F3\u23F8-\u23FA"
    "\u25AA\u25AB\u25B6\u25C0\u25FB-\u25FE"
    "\u2934\u2935\u3030\u303D\u3297\u3299"
)
# 单元 = 表情主体 + 修饰符，且允许 ZWJ 串联成组合表情（👨‍👩‍👧 计 1 个）
_UNIT = rf"(?:[{_BASE}][{_MOD}]*(?:{_ZWJ}[{_BASE}][{_MOD}]*)*)"
_EMOJI_UNIT = re.compile(_UNIT)
_EMOJI_REPEAT = re.compile(rf"({_UNIT})(?:[ \t\u3000]*\1)+")
_SPACE_RUN = re.compile(r"[ \t\u3000]{2,}")


def count_emoji_units(text: str) -> int:
    """统计表情单元数（组合表情计 1 个）。"""
    return len(_EMOJI_UNIT.findall(text or ""))


def normalize_emoji(text: str, max_units: int = 2) -> str:
    """收敛表情：连续重复压成一个，总数超 max_units 时丢弃靠后的多余表情。

    只删表情本身，不动文字——与 clean_candidate 同原则（不改写语义）。
    """
    if not text:
        return text or ""
    out = _EMOJI_REPEAT.sub(lambda m: m.group(1), text)
    units = list(_EMOJI_UNIT.finditer(out))
    if len(units) <= max_units:
        return out
    # 从后往前删，避免索引偏移
    for m in reversed(units[max_units:]):
        out = out[:m.start()] + out[m.end():]
    return _SPACE_RUN.sub(" ", out).strip()


def strip_emoji(text: str) -> str:
    """去除全部表情（表情开关关闭时使用）。"""
    if not text:
        return text or ""
    return _SPACE_RUN.sub(" ", _EMOJI_UNIT.sub("", text)).strip()


# ===== 元话语候选过滤（用户反馈 2026-09-27：LLM 输出「请提供具体的聊天记录…
# 这样我才能帮你起草回复。」被当候选）=====

_META_REPLY = re.compile(
    r"(请[^，。；！?？]{0,15}(提供|补充|给出|发送给我|告诉我|描述|说明)"
    r"[^，。；！?？]{0,20}(聊天记录|上下文|最近(说)?的话|更多信息|详细内容|聊天内容)"
    r"|这样我才能帮你|才能帮你(起草|回复|生成)"
    r"|无法(生成|起草)(回复|候选)"
    r"|作为一个?(AI|ai|人工智能)助手?|作为一个?语言模型)",
)


def is_meta_candidate(text: str) -> bool:
    """判定候选是否为 LLM 元话语（请求澄清/自我指称），命中则丢弃。

    只匹配「要求补充聊天上下文」「能力自述」类模式——
    普通对话里「请告诉我地址」这类真实回复不含聊天上下文词，不会误杀。
    """
    return bool(_META_REPLY.search(text or ""))
