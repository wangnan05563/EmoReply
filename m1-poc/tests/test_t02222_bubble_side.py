"""T02222：微信气泡颜色判侧的回归测试（合成截图 → 像素取样 → 判侧）。

用 PIL 按微信真实配色与几何合成聊天带截图（页底/气泡/字形三层），逐行喂给
`bubble_side.judge_side_with_fallback`，覆盖任务要求的**单聊 / 群聊 / 图片消息 /
语音消息 / 深色模式**，并验证：色域匹配（锚点非精确命中也算）、页面基线扣减、
颜色不可用时回退位置且标记存疑、方向准确率 ≥99%、单行判定 ≤50ms。
"""
import time

from PIL import Image, ImageDraw

from emoreply.bubble_side import (
    DARK_MINE_BUBBLE,
    DARK_OTHER_BUBBLE,
    DARK_PAGE,
    GREEN_ANCHORS,
    MINE,
    OTHER,
    PAGE_LIGHT,
    WHITE_ANCHORS,
    LineBox,
    classify_sample,
    judge_side_with_fallback,
    probe_points,
)

SCREEN_W = 1440
BUBBLE_PAD = 38          # 气泡内边距（文本框 → 气泡边缘），density 3.5 实测档位
OTHER_BUBBLE_LEFT = 230  # 对方气泡左缘（头像在更左侧）
MINE_BUBBLE_RIGHT = 1400  # 本人气泡右缘（头像在最右）

THEMES = {
    "light": {"page": PAGE_LIGHT, "other": (255, 255, 255), "mine": (149, 236, 105),
              "glyph": (25, 25, 25)},
    "dark": {"page": DARK_PAGE, "other": DARK_OTHER_BUBBLE, "mine": DARK_MINE_BUBBLE,
             "glyph": (245, 245, 245)},
}


def _glyphs(draw, box, color, tall=False):
    """把文本区域画成「一块块字形」，让取样点落在内边距而不是字体上。"""
    l, r, t, b = box
    h = (t + b) // 5 if not tall else t
    y2 = (t + 4 * b) // 5 if not tall else b
    x = l
    while x < r:
        draw.rectangle([x, h, min(x + 18, r), y2], fill=color)
        x += 30


def draw_message(img, theme, side, kind="text", text_width=400, top=40, height=90):
    """画一条消息，返回其 OCR 行框（LineBox）与真实侧别。

    kind: text（普通气泡）/ nickname（群聊昵称小字行，无气泡）/
    image（图片消息，中间调照片色）/ voice（语音气泡+白色波形）/
    long_mine_near_threshold（本人长气泡：折行合并把行框左缘拉到 450 阈值之下
    ——位置判定失效、只有颜色能救的真实场景）
    """
    th = THEMES[theme]
    draw = ImageDraw.Draw(img)
    if kind == "nickname":
        l = OTHER_BUBBLE_LEFT + BUBBLE_PAD
        box = (l, l + text_width, top, top + height)
        _glyphs(draw, box, th["glyph"])
        return LineBox(box[0], box[1], box[2], box[3]), OTHER

    if kind == "long_mine_near_threshold":
        box = (440, MINE_BUBBLE_RIGHT - BUBBLE_PAD, top, top + height)
        bubble = [420, top - BUBBLE_PAD, box[1] + BUBBLE_PAD, top + height + BUBBLE_PAD]
        draw.rectangle(bubble, fill=th["mine"])
        _glyphs(draw, box, th["glyph"])
        return LineBox(*box), MINE

    if side == MINE:
        text_right = MINE_BUBBLE_RIGHT - BUBBLE_PAD
        text_left = text_right - text_width
    else:
        text_left = OTHER_BUBBLE_LEFT + BUBBLE_PAD
        text_right = text_left + text_width
    box = (text_left, text_right, top, top + height)

    if kind == "image":
        # 中间调照片（既非绿也不在中性白/深色气泡区间）：颜色不可用 → 回退位置
        draw.rectangle([text_left - 6, top - 6, text_right + 6, top + height + 6],
                       fill=(150, 152, 148))
        _glyphs(draw, box, (200, 205, 210))
        return LineBox(*box), side

    bubble = [text_left - BUBBLE_PAD, top - BUBBLE_PAD,
              text_right + BUBBLE_PAD, top + height + BUBBLE_PAD]
    if kind == "voice":
        bubble[1] = min(bubble[1], top - 20)
    color = th["mine"] if side == MINE else th["other"]
    draw.rounded_rectangle(bubble, radius=28, fill=color)
    if kind == "voice":
        for i in range(4):  # 语音波形：气泡内的白色条（不得被当成白气泡）
            x = text_left + i * 26
            draw.rectangle([x, top + 24, x + 8, top + height - 24], fill=(255, 255, 255))
    _glyphs(draw, box, th["glyph"])
    return LineBox(*box), side


def judge(img, box, theme="light", color_available=True):
    get_rgb = lambda x, y: img.getpixel((x, y))[:3]  # noqa: E731
    return judge_side_with_fallback(
        get_rgb, box, img.width, img.height, color_available=color_available)


def canvas(theme):
    return Image.new("RGB", (SCREEN_W, 400), THEMES[theme]["page"])


# ===== 锚点色：色域匹配（任务给定的四个锚点必须命中，页底必须不命中）=====

def test_anchor_colors_classify():
    for g in GREEN_ANCHORS:
        assert classify_sample(g, PAGE_LIGHT) == MINE
        assert classify_sample(g, DARK_PAGE) == MINE
    for w in WHITE_ANCHORS:
        assert classify_sample(w, PAGE_LIGHT) == OTHER
    # 深色主题下对方气泡是深灰，靠「比页底亮一档」而非绝对白命中
    assert classify_sample(DARK_OTHER_BUBBLE, DARK_PAGE) == OTHER
    # 页面基线本身必须作废：浅色页底 #EDEDED 与 #F2F2F2 只差 5 级，不扣基线就会误判
    assert classify_sample(PAGE_LIGHT, PAGE_LIGHT) is None
    # 深色主题的近白字形（我说气泡里的白字）不得判成对方
    assert classify_sample((245, 245, 245), DARK_PAGE) is None
    # 彩色像素（照片/表情/头像）与气泡底色无关
    assert classify_sample((200, 60, 90), PAGE_LIGHT) is None
    assert classify_sample((30, 30, 90), PAGE_LIGHT) is None


# ===== 单聊：双向都由颜色决定，不依赖坐标 =====

def test_direct_chat_both_sides_by_color():
    for theme in ("light", "dark"):
        img = canvas(theme)
        mine, _ = draw_message(img, theme, MINE, text_width=420, top=60)
        other, _ = draw_message(img, theme, OTHER, text_width=520, top=220)
        assert judge(img, mine, theme).side == MINE
        assert judge(img, other, theme).side == OTHER
        assert judge(img, mine, theme).by_color is True
        assert judge(img, other, theme).by_color is True


def test_mine_anchor_colors_end_to_end():
    """两个绿色锚点（#95EC69 / #A9EA7C）作为气泡色各跑一遍完整取样链路。"""
    for mine_color in GREEN_ANCHORS:
        img = Image.new("RGB", (SCREEN_W, 400), PAGE_LIGHT)
        THEMES["light"]["mine"] = mine_color
        try:
            box, truth = draw_message(img, "light", MINE, text_width=300, top=80)
            call = judge(img, box, "light")
            assert call.side == truth and call.by_color
        finally:
            THEMES["light"]["mine"] = (149, 236, 105)


# ===== 深色模式：相对亮度区间生效 =====

def test_dark_theme_direction():
    img = canvas("dark")
    mine, _ = draw_message(img, "dark", MINE, text_width=380, top=70)
    other, _ = draw_message(img, "dark", OTHER, text_width=460, top=230)
    assert judge(img, mine, "dark").side == MINE
    assert judge(img, other, "dark").side == OTHER


def test_dark_theme_tall_glyphs_do_not_flip():
    """字形拉满行高（取样点会撞到近白字形）：我说仍是我说。"""
    img = canvas("dark")
    th = THEMES["dark"]
    draw = ImageDraw.Draw(img)
    l, r, t, b = 900, 1300, 60, 150
    draw.rounded_rectangle([l - BUBBLE_PAD, t - BUBBLE_PAD, r + BUBBLE_PAD, b + BUBBLE_PAD],
                           radius=28, fill=th["mine"])
    _glyphs(draw, (l, r, t, b), th["glyph"], tall=True)
    call = judge(img, LineBox(l, r, t, b), "dark")
    assert call.side == MINE
    assert call.green_votes >= 2


# ===== 群聊：昵称小字行无气泡 → 回退位置并标记存疑 =====

def test_group_nickname_falls_back_to_position():
    img = canvas("light")
    box, truth = draw_message(img, "light", OTHER, kind="nickname", text_width=180, top=80)
    call = judge(img, box, "light")
    assert call.side == truth          # 昵称行本来就是左侧对方 → 零证据落对方仍正确
    assert call.by_color is False      # 但必须标记为「非颜色判定」= 存疑


def test_group_chat_messages_by_color():
    img = canvas("light")
    nick, _ = draw_message(img, "light", OTHER, kind="nickname", text_width=150, top=40)
    body, _ = draw_message(img, "light", OTHER, text_width=520, top=110)
    mine, _ = draw_message(img, "light", MINE, text_width=300, top=250)
    assert [judge(img, b, "light").side for b in (nick, body, mine)] == [OTHER, OTHER, MINE]
    assert judge(img, body, "light").by_color and judge(img, mine, "light").by_color


# ===== 图片消息 / 语音消息 =====

def test_image_message_zero_evidence_never_claims_mine():
    """图片消息既非绿也非白 → 30 个取样点全部作废，此时刻意**不许**回退位置。

    T02305 真机复测推翻了本用例原先的「回退位置后双方均正确」：拍屏/截图类图内
    文字的行框左缘会越过 `SELF_SIDE_LEFT_RATIO`，位置回退于是凭空造出「我：客户
    账户xxx」——这正是「理由栏被识别成候选回复」反复复发的形态。零证据说明该行
    根本不在气泡上，任何侧别断言都是伪造；宁可统一落到对方（真实链路里这类行还
    会被 T01334 的簇过滤先行剔除，这里是漏网文本的兜底）。
    """
    for theme in ("light", "dark"):
        for side in (OTHER, MINE):
            img = canvas(theme)
            box, _ = draw_message(img, theme, side, kind="image",
                                  text_width=420, top=90)
            call = judge(img, box, theme)
            assert call.green_votes == 0 and call.other_votes == 0
            assert call.by_color is False
            assert call.side == OTHER
            # 非微信场景仍走位置判定（T02222 原行为，不受本规则影响）
            assert judge(img, box, theme, color_available=False).side == side


def test_voice_message_mine_green_wins_over_white_waveform():
    """语音气泡内的白色波形不得压过绿色投票。"""
    for theme in ("light", "dark"):
        img = canvas(theme)
        box, truth = draw_message(img, theme, MINE, kind="voice", text_width=240, top=100)
        call = judge(img, box, theme)
        assert call.side == truth and call.by_color
        assert call.green_votes >= call.other_votes


# ===== 回退逻辑：颜色不可用 / 非微信 / 位置失效 =====

def test_position_only_failure_recovered_by_color():
    """本人长气泡折行合并把 left 拉到 450 阈值之下：位置判「对方」，颜色判回「我」。"""
    for theme in ("light", "dark"):
        img = canvas(theme)
        box, truth = draw_message(img, theme, MINE, kind="long_mine_near_threshold",
                                  top=90)
        assert box.left < SCREEN_W * (450 / 1440)  # 位置判定必然误判
        call = judge(img, box, theme)
        assert call.side == truth and call.by_color
        # 关掉颜色开关（= 非微信平台）时退回位置判定，保留旧行为
        off = judge(img, box, theme, color_available=False)
        assert off.side == OTHER and off.by_color is False


def test_non_wechat_platform_skips_color_sampling():
    img = canvas("light")
    box, _ = draw_message(img, "light", MINE, text_width=400, top=80)
    call = judge(img, box, "light", color_available=False)
    assert call.by_color is False and call.green_votes == 0
    assert call.side == MINE  # 位置判定对正常几何仍然正确


def test_white_page_background_is_not_a_bubble():
    """页底本身接近纯白（部分主题/其他应用）：白气泡取样落到页底上时不得判定。"""
    img = Image.new("RGB", (SCREEN_W, 400), (255, 255, 255))
    box = LineBox(300, 800, 80, 170)  # 直接在纯白页底上放一个行框
    call = judge(img, box, "light")
    assert call.by_color is False and call.other_votes == 0
    assert call.side == OTHER  # 零证据 → 不落位置回退，一律对方（见 T02305 用例说明）

    # 真机历史截图（tmp/wadb/wxchat.png）实测的聊天背景图：左缘 (217,237,244)、
    # 右缘 (240,249,253) 渐变色页底 + 白色对方气泡
    grad = Image.new("RGB", (SCREEN_W, 400), (228, 243, 249))
    ImageDraw.Draw(grad).rounded_rectangle([230, 60, 900, 240], radius=28,
                                           fill=(255, 255, 255))
    call2 = judge_line_with(grad, LineBox(268, 862, 68, 232))
    assert call2.side == OTHER and call2.by_color is True

    # 聊天背景图本身就是带蓝调的浅色：取样点落到页底渐变上不得判成对方气泡
    assert classify_sample((220, 240, 246), (217, 237, 244), (240, 249, 253)) is None


def judge_line_with(img, box):
    get_rgb = lambda x, y: img.getpixel((x, y))[:3]  # noqa: E731
    return judge_side_with_fallback(get_rgb, box, img.width, img.height)


# ===== 任务验收指标：准确率 ≥99%，单行 ≤50ms =====

def _batch(theme, specs):
    """一屏画两条消息（上下留足间距避免气泡重叠），返回 [(img, LineBox, 真实侧别)]。"""
    img = canvas(theme)
    ground = []
    for i, (side, kind, width) in enumerate(specs):
        box, truth = draw_message(img, theme, side, kind=kind, text_width=width,
                                  top=40 + i * 180)
        ground.append((img, box, truth))
    return ground


def test_direction_accuracy_over_mixed_screen():
    """200 条混合消息（单聊正文/群聊昵称/图片/语音/位置失效样本）逐条判侧。

    T02305 后「图片」行的零证据一律落对方，其中本人发的图片必然算错——这是反伪
    造规则的**有意代价**，不属于判侧能力的退化，故单独统计；≥99% 指标只适用于
    颜色/位置真能定侧的行。
    """
    for theme in ("light", "dark"):
        specs = []
        for i in range(200):
            side = MINE if i % 2 else OTHER
            if i % 25 == 3:
                specs.append((OTHER, "nickname", 160))
            elif i % 25 == 7:
                specs.append((side, "image", 420))
            elif i % 25 == 11:
                specs.append((side, "voice", 240))
            elif i % 25 == 17:
                specs.append((MINE, "long_mine_near_threshold", 0))
            else:
                specs.append((side, "text", 200 + (i % 7) * 90))
        correct = pos_correct = 0
        zero_evidence = 0
        for chunk_start in range(0, len(specs), 2):
            batch = specs[chunk_start:chunk_start + 2]
            for (_side, kind, _width), (img, box, truth) in zip(
                    batch, _batch(theme, batch)):
                call = judge(img, box, theme)
                if kind == "image":
                    zero_evidence += 1
                    assert call.side == OTHER and call.green_votes == 0
                    continue
                correct += call.side == truth
                pos_correct += (MINE if box.left >= SCREEN_W * (450 / 1440)
                                else OTHER) == truth
        total = len(specs) - zero_evidence
        assert correct / total >= 0.99, f"{theme}: {correct}/{total}"
        # 颜色信号必须真的优于纯位置（否则这条数据没有意义）
        assert pos_correct / total < correct / total
        print(f"\n{theme}: color-assisted {correct}/{total} = "
              f"{correct / total:.1%}, position-only {pos_correct}/{total} = "
              f"{pos_correct / total:.1%}, zero-evidence image lines "
              f"forced to 对方: {zero_evidence}")


def test_single_line_within_50ms():
    img = canvas("light")
    boxes = [draw_message(img, "light", MINE if i % 2 else OTHER,
                          text_width=200 + i * 30, top=40)[0] for i in range(6)]
    t0 = time.perf_counter()
    for _ in range(10):
        for b in boxes:
            assert len(probe_points(b, img.width, img.height)) <= 36
    ms = (time.perf_counter() - t0) * 1000 / (len(boxes) * 10)
    assert ms < 50, f"per-line color judgment took {ms:.2f}ms"
