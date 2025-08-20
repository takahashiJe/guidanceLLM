# -*- coding: utf-8 -*-
import pytest

from worker.app.services.routing.drive_rules import is_car_direct_accessible

def test_car_accessible_for_general_spot():
    assert is_car_direct_accessible("poi", {"parking": "yes"}) is True
    assert is_car_direct_accessible("poi", None) is True

def test_mountain_defaults_to_ap_required():
    # 山岳は原則トレイルヘッド経由
    assert is_car_direct_accessible("mountain", {}) is False
    assert is_car_direct_accessible("mountain", {"highway": "trailhead"}) is False

def test_overrides_by_explicit_flag():
    # 明示タグで上書き可（drive_rules 実装の仕様に合わせて）
    assert is_car_direct_accessible("mountain", {"car_direct": "yes"}) is True
    assert is_car_direct_accessible("poi", {"car_direct": "no"}) is False
