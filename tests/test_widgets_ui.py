"""Email primitives (agents/widgets/ui.py): escaping, sparkline scaling, deltas, size budgets."""
from widgets import ui
from widgets.runner import MAX_FRAGMENT_BYTES

EVIL = '<script>alert(1)</script>"{{UNSUBSCRIBE_URL}}'


def test_cells_escape_untrusted_text_but_keep_raw():
    assert ui.cell(EVIL) == "&lt;script&gt;alert(1)&lt;/script&gt;&quot;&#123;&#123;UNSUBSCRIBE_URL&#125;&#125;"
    assert ui.cell(ui.Raw("<b>x</b>")) == "<b>x</b>"
    assert ui.cell(None) == "&ndash;"


def test_links_only_allow_http_and_escape_text():
    assert 'href="#"' in ui.link("x", "javascript:alert(1)")
    a = ui.link(EVIL, 'https://e.com/?a="b"')
    assert "<script>" not in a and 'href="https://e.com/?a=&quot;b&quot;"' in a


def test_table_and_list_escape_everything():
    html = ui.data_table([EVIL, "n"], [[EVIL, 1], [ui.link(EVIL, "https://e.com"), None]], numeric=(1,))
    html += ui.item_list([(EVIL, EVIL)]) + ui.stat_row([(EVIL, EVIL, EVIL)])
    html += ui.attribution([(EVIL, "javascript:x"), (EVIL, None)], "2026-10-15", EVIL)
    html += ui.card(EVIL, EVIL, "", "#c8b89a", EVIL, EVIL)
    assert "<script>" not in html and "{{" not in html
    assert '<th scope="col"' in html


def test_card_rejects_bad_accent():
    assert "border-left:4px solid #c8b89a" in ui.card("w", "t", "", 'red;background:url(x)')
    assert "border-left:4px solid #112233" in ui.card("w", "t", "", "#112233")


def test_sparkline_scaling():
    assert ui.sparkline([1, 2, 3, 4, 5, 6, 7, 8]) == "▁▂▃▄▅▆▇█"
    assert ui.sparkline([10, 0, 10]) == "█▁█"
    assert ui.sparkline([3, 3, 3]) == "▄▄▄"
    assert ui.sparkline([1, None, "x", True, 2]) == "▁█"
    assert ui.sparkline([]) == "" and ui.spark_html([]) == ""


def test_delta_has_arrow_sign_and_colour():
    up = ui.delta(0.123, 2, "%", up_good=True)
    assert "&#9650;" in up and "+0.12%" in up and ui.UP in up
    down = ui.delta(-1.5, 1, up_good=True)
    assert "&#9660;" in down and "−1.5" in down and ui.DOWN in down
    assert ui.UP in ui.delta(-1, 0, up_good=False)
    neutral = ui.delta(0.5, 2, up_good=None)
    assert ui.FLAT in neutral and "+0.50" in neutral
    assert "&#9654;" in ui.delta(0.001, 2) and "0.00" in ui.delta(0.001, 2)
    assert "&ndash;" in ui.delta(None) and "&ndash;" in ui.delta(True)


def test_fmt_date():
    assert ui.fmt_date("2026-10-05") == "Oct 5"
    assert ui.fmt_date("2026-10-15T16:00:00Z", with_year=True) == "Oct 15, 2026"
    assert ui.fmt_date("soon") == ""


def test_primitives_stay_under_the_fragment_budget():
    rows = [[f"Very long game title number {i} with a subtitle", f"{i * 1.25:.2f}", ui.delta(i / 10, 2, "%")]
            for i in range(10)]
    body = ui.data_table(["Name", "Price", "Change"], rows, numeric=(1, 2))
    html = ui.card("t", "Ten rows", body, "#c8b89a", "*", "Oct 15",
                   ui.attribution([("Source", "https://example.com/" + "x" * 60)], "2026-10-15", "note"))
    assert len(html.encode("utf-8")) < MAX_FRAGMENT_BYTES
    items = ui.item_list([(ui.link("t" * 80, "https://e.com/" + "p" * 80), "m" * 120) for _ in range(6)])
    assert len(ui.card("t", "Six items", items, "#c8b89a").encode("utf-8")) < MAX_FRAGMENT_BYTES
