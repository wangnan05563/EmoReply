"""T01138：读屏/剪贴板内容双栏均分（与 Android refreshInputPane 同构）。"""
from emoreply.preprocess import split_into_columns


def test_two_columns_even_rows():
    lines = ["对方：大哥", "对方：洗澡呀", "我：好", "对方：儿子睡着了"]
    assert split_into_columns(lines) == [["对方：大哥", "对方：洗澡呀"], ["我：好", "对方：儿子睡着了"]]


def test_two_columns_odd_rows_left_heavier():
    lines = ["a", "b", "c"]
    assert split_into_columns(lines) == [["a", "b"], ["c"]]


def test_single_line_no_second_column():
    assert split_into_columns(["只有一行"]) == [["只有一行"], []]


def test_blank_lines_skipped():
    assert split_into_columns(["a", "  ", "b", ""]) == [["a"], ["b"]]


def test_empty_input():
    assert split_into_columns([]) == [[], []]


def test_three_columns():
    lines = [str(i) for i in range(7)]
    cols = split_into_columns(lines, columns=3)
    assert cols == [["0", "1", "2"], ["3", "4", "5"], ["6"]]


def test_columns_exceed_rows():
    cols = split_into_columns(["a"], columns=3)
    assert cols == [["a"], [], []]
