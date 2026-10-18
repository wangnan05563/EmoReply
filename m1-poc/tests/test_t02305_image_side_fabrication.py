"""T02305：截图类消息的图内文字不再伪造侧别（A 环带几何判据 + B 锚点色域门 + C 零证据不落「我」）。

背景（真机 T02224 第③项不通过）：对方发来的银行界面截图，读屏输出
「我：客户账户名称:管理测试1」这类**伪造我方行**。三处成因（第 3 处由 10-17
真机复测才测实，前两轮按「截图绿表头冒充气泡」归因并不完整）：

1. 图内文字簇的剔除判据是「无标点行占比 ≥ 0.8」，而银行/表单类截图正文天生带
   冒号逗号（键值对排版）→ 整簇漏网，进到判侧；
2. 判侧把**截图自己的颜色**当成了气泡底色：品牌绿表头 (0,160,93) 拿到 30/30 绿票
   → side=「我」且 by_color=True（连「方向存疑」都不进）；
3. 拍屏类截图的颜色被 B 掐成零证据后，**位置回退**接手造出「我：」——图内文字
   左缘 468/490 越过了 `SELF_SIDE_LEFT_RATIO`（450/1440）；同时簇过滤跑在折行合并
   之后，合并把 3 行并成 1 条高行，A 的簇判据根本聚不起来。

夹具与真机同构：1440×3200、对方白气泡 #F2F2F2 坐在 #EDEDED 页底上、我说绿
#95EC69/#A9EA7C；带聊天背景图（左右缘基线 (217,237,444) 系）的场景单独用于 B。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emoreply.bubble_side import (
    DARK_MINE_BUBBLE, DARK_PAGE, LineBox, MINE, OTHER, PAGE_LIGHT,
    classify_sample, judge_side_with_fallback,
)
from emoreply.preprocess import (
    OcrLine, filter_image_text_clusters, merge_wrapped_lines,
)

W, H = 1440, 3200
SHOT_PAGE_L, SHOT_PAGE_R = (217, 237, 244), (240, 249, 253)  # 真机聊天背景图左右缘
BUBBLE_OTHER = (242, 242, 242)                      # 对方白气泡
BRAND_GREEN = (0, 160, 93)                          # App 品牌绿（银行类截图表头）
SHOT_BODY = (255, 255, 255)                         # 截图正文白底

BUB = (380, 800, 1000, 1520)   # 左侧（对方）气泡
SHOT = (400, 820, 980, 1500)   # 气泡内嵌的截图


def line(text, top, bottom=None, left=470, right=900):
    return OcrLine(text, left, right, top, bottom if bottom else top + 40, 0)


def scene(image=None, page_l=PAGE_LIGHT, page_r=PAGE_LIGHT):
    """「对方白气泡坐在页底上」的场景；[image]=闭包 (x,y)->RGB|None 时把图贴进气泡。"""
    bx0, by0, bx1, by1 = BUB

    def get_rgb(x, y):
        if x < 4 or x > W - 5:
            return page_l if x < W // 2 else page_r
        if image is not None:
            inside = image(x, y)
            if inside is not None:
                return inside
        if bx0 <= x < bx1 and by0 <= y < by1:
            return BUBBLE_OTHER
        return page_l if x < W // 2 else page_r
    return get_rgb


def bank_shot(x, y):
    """银行界面截图：上 1/3 是品牌绿表头（820..1040），其余是白底正文。"""
    if not (SHOT[0] <= x < SHOT[2] and SHOT[1] <= y < SHOT[3]):
        return None
    return BRAND_GREEN if y < 1040 else SHOT_BODY


def photo_shot(x, y):
    """照片类图片消息：整块彩色内容，没有可用的气泡底色。"""
    if not (SHOT[0] <= x < SHOT[2] and SHOT[1] <= y < SHOT[3]):
        return None
    return ((200, 60, 90), (30, 30, 90), BRAND_GREEN)[(y // 60) % 3]


def gray_shot(x, y):
    """灰阶拍屏截图（真机 10-17 史文豪会话那三张）：近中性但亮度带纹理。

    单样本通道跨度只有几（逃过 [_ring_content_ratio] 的 CONTENT_MIN_SPAN 下限），
    环带却极差近百——这正是 A 第一条判据漏网、需要「零证据 + 灰纹理」补抓的形态。
    """
    if not (SHOT[0] <= x < SHOT[2] and SHOT[1] <= y < SHOT[3]):
        return None
    v = 140 + (x * 7 + y * 13) % 90
    return (v, v + 1, v - 3)


BANK = scene(bank_shot)
PHOTO = scene(photo_shot)
GRAY = scene(gray_shot, SHOT_PAGE_L, SHOT_PAGE_R)
PLAIN = scene()


def cluster(top, n=3, step=45):
    """图内排版的典型形态：行高 40、间距 5（远小于行高 × 0.35）→ 紧凑簇。"""
    return [line(f"客户账户名称:条目{k}", top + k * step) for k in range(n)]


# ===== B：锚点色域门（排除 App 品牌绿冒充我说气泡）=====

class TestAnchorGreenGate:
    def test_wechat_anchors_still_mine_in_light_theme(self):
        assert classify_sample((149, 236, 105), PAGE_LIGHT) == MINE
        assert classify_sample((169, 234, 124), PAGE_LIGHT) == MINE

    def test_brand_green_no_longer_mine(self):
        """品牌绿 r=0/g=160/g-r=160：四个方向全在锚点区间外 → 样本作废。"""
        assert classify_sample(BRAND_GREEN, PAGE_LIGHT) is None

    def test_dark_theme_mine_green_unchanged(self):
        """深色我说气泡 #1AAB39=(26,171,57) 与品牌绿几乎同值，深色分支不加盒。"""
        assert classify_sample(DARK_MINE_BUBBLE, DARK_PAGE) == MINE

    def test_green_header_line_is_not_high_confidence_mine(self):
        """原缺陷主案：截图绿表头里的行不再得到 by_color=True 的「我」。"""
        grad = scene(bank_shot, SHOT_PAGE_L, SHOT_PAGE_R)
        box = LineBox(470, 900, 900, 960)           # 落在 820..1040 的绿表头内
        call = judge_side_with_fallback(grad, box, W, H)
        assert call.green_votes == 0
        assert not call.by_color, '误判必须降级为「方向存疑」，不能仍是高置信结论'


# ===== A：环带几何判据（替代「无标点行占比」）=====

class TestRingGeometryFilter:
    def test_cluster_inside_photo_dropped(self):
        assert filter_image_text_clusters(cluster(1200), get_rgb=PHOTO,
                                          width=W, height=H) == []

    def test_cluster_in_bank_green_header_dropped(self):
        """真机主案：品牌绿表头不是气泡底色，环带全作废 → 整簇剔除。"""
        assert filter_image_text_clusters(cluster(880), get_rgb=BANK,
                                          width=W, height=H) == []

    def test_punctuation_no_longer_decides(self):
        """同一几何位置，文本有无标点不再改变结论（判据只看像素）。"""
        noisy = [line("第一行没有标点", 1200), line("第二行没有标点", 1245),
                 line("第三行没有标点", 1290)]
        punct = [line("在吗？", 1200), line("看到了吗？", 1245), line("回我一下！", 1290)]
        assert filter_image_text_clusters(noisy, get_rgb=PHOTO, width=W, height=H) == []
        assert filter_image_text_clusters(punct, get_rgb=PHOTO, width=W, height=H) == []

    def test_real_bubble_cluster_kept(self):
        """真·多行气泡：环带全是气泡底色 → 零作废样本 → 一行都不能丢。"""
        lines = cluster(1000)
        assert filter_image_text_clusters(lines, get_rgb=PLAIN, width=W, height=H) == lines

    def test_bubble_over_chat_background_image_kept(self):
        """用户设了聊天背景图时 #F2F2F2 比基线暗、颜色样本本就作废——但环带作废
        不等于图内：气泡里的行仍须保留。用「环带全作废」的极端场景锁住这个边界。"""
        lines = cluster(1000)
        grad = scene(None, SHOT_PAGE_L, SHOT_PAGE_R)
        kept = filter_image_text_clusters(lines, get_rgb=grad, width=W, height=H)
        # 该场景下环带确实全作废（页底亮过气泡），因此几何判据不能只看作废占比，
        # 还要求簇内行自身判得出气泡底色；簇内判不出 → 视为页面/背景，保留。
        assert kept == lines or len(kept) == len(lines)

    def test_single_line_never_dropped(self):
        """不足 min_cluster 的行不参与几何判据（图片说明单行走原有路径）。"""
        lines = [line("封面标题词", 1200)]
        assert filter_image_text_clusters(lines, get_rgb=PHOTO, width=W, height=H) == lines

    def test_without_pixels_keeps_everything(self):
        """拿不到像素时一律保留——绝不退回「按标点反推图内文字」的旧判据。"""
        lines = cluster(1200)
        assert filter_image_text_clusters(lines) == lines


# ===== C：零证据行不得凭空断言「我」（真机复测定下来的硬约束）=====

class TestZeroEvidenceNeverClaimsMine:
    def test_right_side_photo_line_is_not_mine(self):
        """行框左缘越过 `SELF_SIDE_LEFT_RATIO`（468/490 ≥ 450）也不再是「我」。

        真机那三张拍屏截图的伪造路径就在这儿：颜色 30 点全作废，位置回退却按左缘
        判成「我：客户账户名称:…」——零证据只说明这行不在气泡上，侧别无从得知。
        """
        box = LineBox(900, 1300, 1200, 1240)
        call = judge_side_with_fallback(GRAY, box, W, H)
        assert call.green_votes == 0 and call.other_votes == 0
        assert call.by_color is False
        assert call.side == OTHER

    def test_color_off_path_keeps_position(self):
        """非微信场景（钉钉/QQ/短信）颜色本就不可用：仍按位置判定，行为不变。"""
        box = LineBox(900, 1300, 1200, 1240)
        call = judge_side_with_fallback(GRAY, box, W, H, color_available=False)
        assert call.side == MINE and call.by_color is False

    def test_mine_bubble_still_mine(self):
        """绿气泡有真实绿票时不受本规则影响（否则「我」的消息会被整屏判成对方）。"""
        box = LineBox(900, 1300, 1200, 1240)

        def get_rgb(x, y):
            if x < 4 or x > W - 5:
                return PAGE_LIGHT if x < W // 2 else PAGE_LIGHT
            if 862 <= x < 1338 and 1162 <= y < 1278:
                return (149, 236, 105)
            return PAGE_LIGHT
        call = judge_side_with_fallback(get_rgb, box, W, H)
        assert call.by_color and call.side == MINE


# ===== A′′：灰阶拍屏（零气泡证据 + 带纹理中性灰环带）=====

class TestGrayPhotoCluster:
    def test_gray_cluster_dropped(self):
        """A 第一条判据（饱和色）对灰阶拍屏恒为 0 占比 → 第二条判据必须接住。"""
        assert filter_image_text_clusters(cluster(1200), get_rgb=GRAY,
                                          width=W, height=H) == []

    def test_flat_bubble_cluster_kept(self):
        """零票数但环带平坦（聊天背景图比 #F2F2F2 气泡更亮）：不是照片，须保留。"""
        lines = cluster(1000)
        grad = scene(None, SHOT_PAGE_L, SHOT_PAGE_R)
        assert filter_image_text_clusters(lines, get_rgb=grad, width=W, height=H) == lines

    def test_photo_cluster_still_dropped_by_first_rule(self):
        """彩色照片仍由第一条判据剔除（第二条只是补漏，不改变既有结论）。"""
        assert filter_image_text_clusters(cluster(1200), get_rgb=PHOTO,
                                          width=W, height=H) == []


def test_filter_runs_before_wrap_merge():
    """簇过滤必须排在折行合并**之前**（真机 10-17 复测：A 当时整条哑火的直接原因）。

    合并把 3 行并成 1 条 130px 高的行，既凑不满 min_cluster=3，行高又让
    `gap < 高度 × 0.35` 对下一段永不成立 → 图内文字整片漏网进入判侧。
    """
    raw = cluster(1200)
    assert filter_image_text_clusters(raw, get_rgb=GRAY, width=W, height=H) == []
    merged = merge_wrapped_lines(raw)
    assert len(merged) == 1                      # 复现真机的合并形态
    assert filter_image_text_clusters(merged, get_rgb=GRAY, width=W, height=H) == merged


# ===== 已知残留（写清楚，别让人当成已完全修复）=====

def test_residual_flat_white_screenshot_survives_filter():
    """整张纯白无彩的长截图：环带本身就是白，与对方气泡底色不可分，局部判据无解。

    这种行仍会被判成「对方」——在左侧气泡里这与真实发送方一致；B 已掐掉它被升级成
    高置信「我：」这条路。彻底剔除要整屏图块分割，属另立需求；本用例锁住边界。
    """
    white_shot = scene(lambda x, y: SHOT_BODY if (
        SHOT[0] <= x < SHOT[2] and SHOT[1] <= y < SHOT[3]) else None)
    lines = cluster(1100)
    assert filter_image_text_clusters(lines, get_rgb=white_shot, width=W, height=H) == lines
