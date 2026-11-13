"""T02222：微信气泡**颜色**判侧（平台无关参考实现，本模块是事实源与回归网）。

与其并列的 Android 实现见 `capture/BubbleSideJudge.kt` + `EmoReplyReaderService`
（同一阈值、同一取样点位，注释逐条对应）。

## 为什么要在位置判定之外再加颜色信号

既有判侧 `judge_side(left)`（T01143/T01150）只用「文本左缘 ≥ 屏宽 × 0.3125」：
它依赖 OCR 行框的 left 值，而 left 会随字号缩放、折行合并、头像字形粘连、
群聊昵称行等漂移——真机出现过「对方长气泡被标成我」（T01150）。微信的
气泡底色是**平台强特征**：我说=绿（#95EC69 / #A9EA7C），对方说=白（#FFFFFF /
#F2F2F2），与文本坐标无关，因此作为主判据；颜色不可用时回退位置判定并标记
「方向存疑」。

## 三条防假阳性的关键约束

1. **色域匹配而非精确匹配**：气泡色经过抗锯齿/主题/截图像色深后不会正好等于锚点，
   判据写成「绿度差 + 中性度 + 明度」的区间（见 GREEN_* / NEUTRAL_* 常量）。
2. **必须先减去页面背景色**：微信浅色页底通常是 #EDEDED（与「对方白气泡」只差 18 级，
   与 #F2F2F2 只差 5 级），且用户还能设**聊天背景图**——真机历史截图实测左右缘基线
   分别为 (217,237,244) 与 (240,249,253)（渐变浅蓝）。故取左右两缘各一条基线：落在
   任一条上的样本作废，白气泡须比「更亮的那侧」还亮 ≥5 档。
3. **深色模式反向**：深色页底 #111111、对方气泡 #2C2C2C（只亮一档），而**文字**是
   近白 #FFFFFF。若沿用浅色的「近白即对方」，落在字形上的样本会把我说判成对方。
   故深色分支只认「比页底亮一档且不太亮」的区间，并用 page_lum 阈值分流明暗主题。
"""
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

RGB = Tuple[int, int, int]

MINE = "我"
OTHER = "对方"

# ===== 锚点色（任务给定，测试逐条断言命中区间）=====
GREEN_ANCHORS: List[RGB] = [(149, 236, 105), (169, 234, 124)]      # #95EC69 / #A9EA7C → 我说
WHITE_ANCHORS: List[RGB] = [(255, 255, 255), (242, 242, 242)]      # #FFFFFF / #F2F2F2 → 对方说
PAGE_LIGHT = (237, 237, 237)                                       # #EDEDED 微信浅色页底（干扰源）
DARK_PAGE = (17, 17, 17)                                           # #111111 微信深色页底
DARK_OTHER_BUBBLE = (44, 44, 44)                                   # #2C2C2C 深色对方气泡
DARK_MINE_BUBBLE = (26, 171, 57)                                   # #1AAB39 深色我说气泡（绿）

# ===== 判据阈值（Android 侧同名常量，改动须两侧同步）=====
GREEN_DELTA = 18          # g - max(r,b) ≥ 此值 → 绿（我说气泡）
GREEN_MIN_G = 90          # 且 g 绝对亮度下限，排掉暗部噪点（黑字边缘）
NEUTRAL_MAX_SPAN = 14     # max(r,g,b)-min(r,g,b) ≤ 此值 → 近中性（白/灰）
PAGE_EPS = 4              # 与页面基线的通道级差 ≤ 此值 → 就是页底本身，样本作废
LIGHT_MIN_CHANNEL = 225   # 浅色主题下「白气泡」的最低通道值
LIGHT_STEP_UP = 5         # 浅色：气泡须比页底亮 ≥ 此值（#F2F2F2 vs #EDEDED 只差 5 档）
LIGHT_PAGE_LUM = 80       # page 明度 < 此值 → 判为深色主题，走深色分支
DARK_STEP_UP = 12         # 深色：气泡需比页底亮 ≥ 此值
DARK_STEP_CAP = 110       # 深色：但不得亮过此值（否则是近白字形而非气泡）
DARK_MAX_LUM = 210        # 深色：气泡绝对明度上限，同样为排掉白色字形
MIN_VOTES = 2             # 决定性样本数下限（单点不定，防偶发撞色）

# ===== T02305 B：浅色主题下「我说绿」须贴近微信锚点 =====
# 原判据「任意 g 主导色即绿」会把 **App 品牌绿当成我说气泡**：真机银行类截图（左侧
# 对方气泡里的图片消息）的品牌绿表头 (0,160,93) 拿到 30/30 绿票，输出「我：客户账户
# 名称…」——且 by_color=True，连「方向存疑」都不进。两个我说绿锚点是 (149,236,105)
# 与 (169,234,124)：明度都 ≥105、g≥234、g-r≤87；品牌深绿 r≈0、g≈160、g-r≈160，四维
# 全部落在锚点区间之外，因此按这四个方向同时收口。
# 深色主题不套用本盒：我说气泡 #1AAB39=(26,171,57) 与常见品牌绿 (0,160,93) 数值高度
# 接近，加盒会把真气泡一起排掉（宁缺勿错），深色仍沿用色相判据 + 既有投票。
LIGHT_GREEN_MIN_G = 190    # 锚点 g=236/234；品牌绿 g≈160 出局
LIGHT_GREEN_MIN_R = 100    # 锚点 r=149/169；品牌绿 r=0 出局（本盒的主力排他项）
LIGHT_GREEN_MIN_B = 60     # 锚点 b=105/124
LIGHT_GREEN_GR_MAX = 130   # 锚点 g-r=87/65 + 抗锯齿余量；品牌绿 g-r≈160 出局

# ===== T02305 A：环带「图片内容」样本的跨度下限 =====
# NEUTRAL_MAX_SPAN(14) 是气泡/页底的中性容差，太松：聊天背景图的浅蓝渐变实测跨度 27
# 就会混进来。照片、App 界面色块、品牌绿表头的跨度都在 60 以上，故另立下限；与
# classify_sample 返回 None（判不出气泡底色）联合判定「这个样本属于图片内容」。
CONTENT_MIN_SPAN = 60

SELF_SIDE_LEFT_RATIO = 450 / 1440  # T01143/T01343 位置回退判据（与 kt isMine 同构）

# 取样点位：文本框四周的气泡内边距（padding）带。微信气泡内边距实测
# 横 ≈ 20-50px、纵 ≈ 15-40px（density 3.5），故偏移取 8/20/32 与 8/16 两档，
# 既落在气泡内又避开字形本体。
H_OFFSETS_PX = (8, 20, 32)
V_OFFSETS_PX = (8, 16)
H_FRACS = (0.25, 0.5, 0.75)   # 纵向取文本高度的 1/4、1/2、3/4
V_FRACS = (0.3, 0.5, 0.7)     # 横向取文本宽度的 3/10、1/2、7/10


@dataclass
class LineBox:
    """OCR 行框（截图坐标系，与 kt OcrLineInfo 同构）。"""

    left: int
    right: int
    top: int
    bottom: int
    text: str = ""

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass
class SideCall:
    """一行的判侧结果：side 为「我」/「对方」，by_color 区分是否由颜色决定。"""

    side: str
    by_color: bool
    green_votes: int
    other_votes: int


def _lum(rgb: RGB) -> int:
    return (rgb[0] + rgb[1] + rgb[2]) // 3


def _span(rgb: RGB) -> int:
    return max(rgb) - min(rgb)


def classify_sample(rgb: RGB, page: Optional[RGB], page2: Optional[RGB] = None) -> Optional[str]:
    """单像素 → MINE / OTHER / None（None = 该样本不作数）。

    [page]/[page2] 是屏幕左、右两缘的页面基线：真机截图（2026-10-16 核对 tmp/wadb
    历史截图）发现用户可设**聊天背景图**，页底是带渐变的浅色（实测左缘 (217,237,244)、
    右缘 (240,249,253)），单一基线会把另一侧的页底当成「比气泡暗」而误判成白气泡。
    故两缘各取一个基线：与任一基线同色即作废，「比页底亮」按更亮的那侧算。

    顺序即优先级：页底 → 绿 → 中性白。绿色优先，因为绿是微信我说气泡的唯一强特征
    （对方气泡、页底、字形都不可能是绿），而「中性偏亮」还可能撞上图标/图片高光。
    """
    if page is None:
        return None
    refs = (page, page2) if page2 is not None else (page,)
    light_theme = min(_lum(r) for r in refs) >= LIGHT_PAGE_LUM
    if _span(rgb) > NEUTRAL_MAX_SPAN and not _is_green(rgb, light_theme):
        return None  # 彩色（图片/表情/头像）：与气泡底色无关，作废
    if any(_same_as_page(rgb, r) for r in refs):
        return None
    if _is_green(rgb, light_theme):
        return MINE
    return _neutral_bubble(rgb, refs)


def _is_green(rgb: RGB, light_theme: bool = True) -> bool:
    """色相判据（两主题共用）+ 浅色主题专属的锚点色域盒（T02305 B）。

    [light_theme]=False 时只校色相——深色主题的我说绿 #1AAB39 与 App 品牌绿几乎同值，
    套盒会连真气泡一起排掉；深色场景由「比页底亮一档」的相对判据与投票兜底。
    """
    r, g, b = rgb
    if g - max(r, b) < GREEN_DELTA or g < GREEN_MIN_G:
        return False
    if not light_theme:
        return True
    return (g >= LIGHT_GREEN_MIN_G and r >= LIGHT_GREEN_MIN_R and b >= LIGHT_GREEN_MIN_B
            and g - r <= LIGHT_GREEN_GR_MAX)


def _same_as_page(rgb: RGB, page: RGB) -> bool:
    return all(abs(rgb[i] - page[i]) <= PAGE_EPS for i in range(3))


def _neutral_bubble(rgb: RGB, refs: Tuple[RGB, ...]) -> Optional[str]:
    """中性色 → 是否是「对方白气泡」。深色主题走「比页底亮一档」的相对区间。"""
    if _span(rgb) > NEUTRAL_MAX_SPAN:
        return None
    if min(_lum(r) for r in refs) < LIGHT_PAGE_LUM:  # 深色主题（任一侧页底暗即算）
        # 以最暗的页底为基准：气泡须比它亮一档，且不得亮过上限（否则是近白字形）
        step = _lum(rgb) - min(_lum(r) for r in refs)
        if DARK_STEP_UP <= step <= DARK_STEP_CAP and _lum(rgb) <= DARK_MAX_LUM:
            return OTHER
        return None
    if min(rgb) < LIGHT_MIN_CHANNEL:  # 浅色主题：白气泡最低通道门槛
        return None
    # 浅色页底（色号 EDEDED，或聊天背景图）比白气泡暗、与色号 F2F2F2 只差 5 档，因此只认
    # 「比两侧页底中更亮的那侧还亮」的方向；与任一页底同色的样本已在取样层作废。
    return OTHER if _lum(rgb) >= max(_lum(r) for r in refs) + LIGHT_STEP_UP else None


def probe_points(box: LineBox, width: int, height: int) -> List[Tuple[int, int]]:
    """气泡内边距取样点（水平 6 列 × 3 行 + 垂直 4 行 × 3 列 ≈ 30 点）。"""
    pts: List[Tuple[int, int]] = []
    for d in H_OFFSETS_PX:
        for f in H_FRACS:
            y = box.top + int(box.height * f)
            pts.append((box.left - d, y))
            pts.append((box.right + d, y))
    for d in V_OFFSETS_PX:
        for f in V_FRACS:
            x = box.left + int(box.width * f)
            pts.append((x, box.top - d))
            pts.append((x, box.bottom + d))
    return [(x, y) for x, y in pts if 0 <= x < width and 0 <= y < height]


def page_colors_at(get_rgb: Callable[[int, int], RGB], y: int, width: int,
                   height: int) -> Tuple[Optional[RGB], Optional[RGB]]:
    """页面基线色（左缘、右缘各一个）：聊天背景图可能带渐变，单一基线会误判。

    各取本侧两列的众数（并列取第一个）：避开这两列上偶发的头像/悬浮条像素。
    """
    def modal(xs: Tuple[int, ...]) -> Optional[RGB]:
        votes: List[RGB] = []
        for x in xs:
            for dy in (-40, 0, 40):
                yy = min(max(y + dy, 0), height - 1)
                votes.append(get_rgb(x, yy))
        if not votes:
            return None
        return max(dict.fromkeys(votes), key=votes.count)

    left = modal((2, 4))
    right = modal((width - 3, width - 5))
    if left is None or right is None:
        return left or right, left or right
    return left, right


def judge_line(get_rgb: Callable[[int, int], RGB], box: LineBox, width: int,
               height: int) -> Tuple[Optional[str], int, int]:
    """一行像素 → (side | None, green_votes, other_votes)。None = 颜色不可用。"""
    page_l, page_r = page_colors_at(get_rgb, (box.top + box.bottom) // 2, width, height)
    green = other = 0
    for x, y in probe_points(box, width, height):
        call = classify_sample(get_rgb(x, y), page_l, page_r)
        if call == MINE:
            green += 1
        elif call == OTHER:
            other += 1
    return decide_side(green, other), green, other


def decide_side(green: int, other: int) -> Optional[str]:
    """投票：绿优先（唯一强特征），其余需明显多于绿。"""
    if green >= MIN_VOTES and green >= other:
        return MINE
    if other >= MIN_VOTES and other > green:
        return OTHER
    return None


def judge_side_with_fallback(get_rgb: Callable[[int, int], RGB], box: LineBox,
                             width: int, height: int,
                             color_available: bool = True) -> SideCall:
    """主入口：颜色优先，不可用（非微信 / 深色读不出 / 像素取不到 / 预算超限）回退位置。

    [color_available]=False 时不取样，直接按 T01143 左缘比例回退——非微信平台
    （钉钉/QQ/短信气泡色不一）必须走这条分支，否则颜色规则反而降低准确率。
    """
    if color_available:
        side, green, other = judge_line(get_rgb, box, width, height)
        if side is not None:
            return SideCall(side, True, green, other)
        if green == 0 and other == 0:
            # T02305 真机复测（10-17 史文豪会话拍屏截图）：这类行的 30 个环带样本
            # **全部作废**（既非页底也非气泡底色，而是照片内部的灰底），颜色什么都
            # 没说，位置回退却因图内文字左缘越过 SELF_SIDE_LEFT_RATIO 造出「我：」。
            # 零证据 = 这行根本不在气泡上，位置信息随之失效；而「我」是唯一危险的
            # 伪造方向（把对方内容伪装成用户发言并驱动回复），故此处只允许落到对方。
            return SideCall(OTHER, False, 0, 0)
    else:
        green = other = 0
    fallback = MINE if box.left >= width * SELF_SIDE_LEFT_RATIO else OTHER
    return SideCall(fallback, False, green, other)
