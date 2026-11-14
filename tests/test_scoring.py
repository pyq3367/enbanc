"""成本口径：从面板公开的两种成本形态里认出一种，并记住结论。

回归重点不是「算得准」，而是三件容易静默坏掉的事：
1. 面板换代后成本项整体变 None，排序看起来还正常却不再区分贵贱；
2. 0 价（免费条）被当成缺值，免费档从此排到最贵；
3. 换机器重装后不重新识别，拿旧结论套新面板。
"""
from __future__ import annotations

import functools
import json
import os
import tempfile
import time
from pathlib import Path

from enbanc import scoring

PROMPT = 0.00000015
COMPLETION = 0.0000006
ANCHOR = {"prompt": PROMPT, "completion": COMPLETION}


def with_home(fn):
    """把 ENBANC_HOME 指到临时目录，避免污染真实用户目录。"""
    @functools.wraps(fn)
    def wrapper():
        old = os.environ.get("ENBANC_HOME")
        base = Path(tempfile.mkdtemp(prefix="enbanc-scoring-"))
        os.environ["ENBANC_HOME"] = str(base)
        try:
            return fn()
        finally:
            if old is None:
                os.environ.pop("ENBANC_HOME", None)
            else:
                os.environ["ENBANC_HOME"] = old
    return wrapper


def test_blend_requires_every_weighted_field():
    assert scoring.blend(ANCHOR) is not None
    assert scoring.blend({"prompt": PROMPT}) is None          # 缺 completion
    assert scoring.blend({}) is None
    assert scoring.blend(None) is None


def test_zero_price_is_a_price_not_a_gap():
    # 免费条必须算出 0.0，而不是 None —— 否则免费模型会被当成「未标价」
    assert scoring.blend({"prompt": 0, "completion": 0}) == 0.0


def test_rate_is_ten_x_for_ten_x_price():
    ten_x = {"prompt": PROMPT * 10, "completion": COMPLETION * 10}
    assert scoring.rate(ANCHOR, ANCHOR) == 1.0
    assert scoring.rate(ten_x, ANCHOR) == 10.0
    assert scoring.rate(ANCHOR, ANCHOR, anchor_rate=0.11) == 0.11


def test_rate_returns_none_without_anchor():
    assert scoring.rate(ANCHOR, {}) is None
    assert scoring.rate({}, ANCHOR) is None


def test_detect_prefers_price_when_panel_publishes_unit_prices():
    models = [{"pricing": ANCHOR}, {"pricing": ANCHOR}, {"pricing": None, "credits": 0.7}]
    mode, ev = scoring.detect(models)
    assert mode == "price"
    assert ev["price_coverage"] > 0.5


def test_detect_falls_back_to_credits_for_an_old_panel():
    models = [{"pricing": None, "credits": 0.69}, {"pricing": None, "credits": 0.11}]
    mode, _ = scoring.detect(models)
    assert mode == "credits"


def test_detect_without_models_does_not_invent_a_mode():
    mode, ev = scoring.detect([])
    assert mode is None
    assert "empty" in ev["note"]


@with_home
def test_resolve_records_once_then_reads_back():
    models = [{"pricing": ANCHOR}]
    mode, _ = scoring.resolve(models)
    assert mode == "price"
    assert scoring.mode_path().is_file()
    # 第二次不再探测：传一份「看起来像旧面板」的清单也不改结论
    assert scoring.resolve([{"credits": 0.5}])[0] == "price"
    # 显式覆盖优先，且写回记录
    assert scoring.resolve(None, override="credits")[0] == "credits"
    assert scoring.load_mode()["mode"] == "credits"


@with_home
def test_refresh_reprobes_stale_record():
    scoring.save_mode("credits", {"price_coverage": 0.0})
    path = scoring.mode_path()
    stale = json.loads(path.read_text(encoding="utf-8"))
    stale["at"] = time.time() - 10 * 24 * 3600
    path.write_text(json.dumps(stale), encoding="utf-8")
    mode, _ = scoring.resolve([{"pricing": ANCHOR}], refresh=True)
    assert mode == "price"


@with_home
def test_describe_reports_unset_before_any_detection():
    assert "unset" in scoring.describe()
    scoring.save_mode("price", {"price_coverage": 1.0, "credits_coverage": 0.0})
    line = scoring.describe()
    assert "price" in line and "100%" in line
